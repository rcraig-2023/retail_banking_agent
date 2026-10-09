"""Export Strands agent traces to watsonx Orchestrate (Agentic Control Plane) over OTLP/HTTP.

Adds a second span processor to the existing TracerProvider (the AgentCore/ADOT one in the
managed runtime), so CloudWatch tracing is unchanged. Spans are re-shaped to the attribute
conventions documented in "Use watsonx Orchestrate as an agentic control plane for external
AI agents" (IBM Developer, 2026-09).

Configuration (all via environment variables; nothing secret is stored in code):
    WXO_AGENT_ID             ID of the external agent registered in Orchestrate. Telemetry is
                             disabled when this is unset.
    WXO_TENANT_ID            <account-id>_<instance-id>
    WXO_OTEL_URL             https://api.<region>.watson-orchestrate.cloud.ibm.com/instances/<id>/v1/orchestrate/inject/traces
    WXO_API_KEY_SECRET_ID    Secrets Manager secret holding the IBM Cloud API key (plain string)
    WXO_API_KEY              Alternative to the secret, for local testing only
    WXO_TOKEN_URL            default https://iam.cloud.ibm.com/identity/token
    WXO_ENVIRONMENT_NAME     "live" (default) or "draft"
    WXO_AGENT_NAME           name used in span names, default retail_banking_support
    WXO_CAPTURE_CONTENT      "true" (default) or "false"; false omits user input and agent
                             output from exported spans
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from typing import Callable, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import SpanKind

logger = logging.getLogger(__name__)

_DEFAULT_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"
_STRANDS_SCOPE_PREFIX = "strands"
_MAX_ATTR_CHARS = 4000
_installed = False

# Secrets must not reach the Orchestrate conversation view. Masks "PIN 24592", "pin is 1234", etc.
_SECRET_PATTERN = re.compile(r"(?i)\b(pin|passcode|password)\b(\W*(?:is|:)?\W*)\d{3,12}")


def _redact(text: str) -> str:
    return _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}****", text)


def _jwt_expiry(token: str) -> float | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _load_api_key() -> str:
    key = os.getenv("WXO_API_KEY", "").strip()
    if key:
        return key
    secret_id = os.getenv("WXO_API_KEY_SECRET_ID", "").strip()
    if not secret_id:
        raise RuntimeError("Set WXO_API_KEY_SECRET_ID (or WXO_API_KEY for local testing)")
    import boto3

    return boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"].strip()


def _exchange_token() -> tuple[str, float]:
    """Exchange the IBM Cloud API key for a bearer token (form-encoded, per IBM IAM)."""
    body = urlencode(
        {"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": _load_api_key()}
    ).encode("utf-8")
    request = Request(
        os.getenv("WXO_TOKEN_URL", _DEFAULT_TOKEN_URL),
        data=body,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - https URL from config
        payload = json.loads(response.read().decode("utf-8"))
    token = payload.get("token") or payload.get("access_token")
    if not token:
        raise RuntimeError("Token response did not contain a token")
    return token, _jwt_expiry(token) or time.time() + float(payload.get("expires_in", 3000))


def _text_from_blocks(raw: str) -> str:
    """Strands events carry content as a JSON list of Bedrock content blocks."""
    try:
        blocks = json.loads(raw)
    except (TypeError, ValueError):
        return str(raw)
    if isinstance(blocks, list):
        return "\n".join(b["text"] for b in blocks if isinstance(b, dict) and "text" in b)
    return str(raw)


def _event_value(span: ReadableSpan, event_name: str, key: str) -> str:
    for event in span.events:
        if event.name == event_name and event.attributes and key in event.attributes:
            return str(event.attributes[key])
    return ""


class WxoSpanExporter(SpanExporter):
    """Translate Strands spans to the Orchestrate trace shape and ship them over OTLP/HTTP."""

    def __init__(
        self,
        endpoint: str,
        tenant_id: str,
        agent_id: str,
        agent_name: str,
        environment_name: str,
        capture_content: bool,
        base_resource: Resource,
        token_provider: Callable[[], tuple[str, float]] = _exchange_token,
    ) -> None:
        self._endpoint = endpoint
        self._tenant_id = tenant_id
        self._agent_id = agent_id
        self._agent_name = agent_name
        self._capture_content = capture_content
        self._token_provider = token_provider
        self._delegate: OTLPSpanExporter | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()
        # Orchestrate attributes the agent by agent.id only; do not send agent.name.
        self._resource = base_resource.merge(
            Resource.create(
                {
                    "service.name": "retail-banking-agent",
                    "tenant.id": tenant_id,
                    "agent.id": agent_id,
                    "environment.name": environment_name,
                    "deployment.environment": environment_name,
                }
            )
        )
        self._environment_name = environment_name

    # -- token handling -------------------------------------------------------------------
    def _get_delegate(self) -> OTLPSpanExporter:
        with self._lock:
            if self._delegate is not None and time.time() < self._expires_at - 120:
                return self._delegate
            token, expires_at = self._token_provider()
            new_delegate = OTLPSpanExporter(
                endpoint=self._endpoint,
                headers={
                    "Authorization": f"Bearer {token}",
                    "x-ibm-tenant-id": self._tenant_id,
                    "x-ibm-agent-id": self._agent_id,
                    "x-langfuse-ingestion-version": "4",
                },
                timeout=15,
            )
            old, self._delegate, self._expires_at = self._delegate, new_delegate, expires_at
            if old is not None:
                old.shutdown()
            return new_delegate

    # -- span translation -----------------------------------------------------------------
    def _translate(self, span: ReadableSpan) -> ReadableSpan:
        attrs = dict(span.attributes or {})
        operation = str(attrs.get("gen_ai.operation.name", ""))
        name = span.name
        kind = span.kind
        parent = span.parent
        new_attrs: dict = {
            k: v
            for k, v in attrs.items()
            # agent.name must not be sent (Orchestrate would misclassify the trace)
            if k not in ("gen_ai.agent.name", "gen_ai.event.start_time", "gen_ai.event.end_time")
        }
        new_attrs["environment.name"] = self._environment_name

        if operation == "invoke_agent":
            name = f"invoke_agent {self._agent_name}"
            kind = SpanKind.SERVER
            # In AgentCore the request is wrapped by an ASGI span that is not exported here.
            # Orchestrate builds the conversation from the root span, so make this the root.
            parent = None
            session_id = str(attrs.get("session.id") or f"{span.context.trace_id:032x}")
            new_attrs.update(
                {
                    "agent.id": self._agent_id,
                    "thread.id": session_id,
                    "conversation.id": session_id,
                    "langfuse.session.id": session_id,
                    "langfuse.user.id": str(attrs.get("user.id", "anonymous")),
                    "agent.execution.status": "error" if not span.status.is_ok and span.status.status_code.name == "ERROR" else "success",
                }
            )
            if self._capture_content:
                user_text = _text_from_blocks(_event_value(span, "gen_ai.user.message", "content"))
                answer = _event_value(span, "gen_ai.choice", "message").strip()
                if user_text:
                    new_attrs["input"] = _redact(user_text)[:_MAX_ATTR_CHARS]
                if answer:
                    new_attrs["output"] = _redact(answer)[:_MAX_ATTR_CHARS]
        elif operation == "chat":
            name = "gen_ai.chat"
            kind = SpanKind.CLIENT
            new_attrs["gen_ai.system"] = "aws.bedrock"
        elif operation == "execute_tool":
            tool_name = str(attrs.get("gen_ai.tool.name", "tool"))
            name = f"tool_call {tool_name}"
            new_attrs["tool.name"] = tool_name
            result = _text_from_blocks(_event_value(span, "gen_ai.choice", "message"))
            failed = attrs.get("gen_ai.tool.status") != "success" or "FAILURE" in result
            new_attrs["tool.business_outcome"] = "failed" if failed else "success"
            if self._capture_content and result:
                new_attrs["tool.output"] = _redact(result)[:_MAX_ATTR_CHARS]

        # Events carry prompts, tool arguments and results; never forwarded (may contain PII).
        # Structured gen_ai.*.messages attributes break Orchestrate's trace view, so none are set.
        return ReadableSpan(
            name=name,
            context=span.context,
            parent=parent,
            resource=self._resource,
            attributes=new_attrs,
            events=(),
            links=span.links,
            kind=kind,
            status=span.status,
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        selected = [
            self._translate(s)
            for s in spans
            if s.instrumentation_scope and s.instrumentation_scope.name.startswith(_STRANDS_SCOPE_PREFIX)
        ]
        if not selected:
            return SpanExportResult.SUCCESS
        try:
            result = self._get_delegate().export(selected)
            logger.info(
                "watsonx Orchestrate export: spans=%d result=%s", len(selected), result.name
            )
            return result
        except Exception:  # never let telemetry break the agent
            logger.warning("watsonx Orchestrate trace export failed", exc_info=True)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        with self._lock:
            if self._delegate is not None:
                self._delegate.shutdown()
                self._delegate = None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


def enable_wxo_telemetry(token_provider: Callable[[], tuple[str, float]] | None = None) -> bool:
    """Attach the Orchestrate exporter to the global TracerProvider. Returns True if enabled."""
    global _installed
    if _installed:
        return True
    agent_id = os.getenv("WXO_AGENT_ID", "").strip()
    if not agent_id:
        logger.info("WXO_AGENT_ID not set; watsonx Orchestrate telemetry disabled")
        return False
    missing = [n for n in ("WXO_TENANT_ID", "WXO_OTEL_URL") if not os.getenv(n, "").strip()]
    if missing:
        logger.warning("watsonx Orchestrate telemetry disabled; missing %s", ", ".join(missing))
        return False
    environment = os.getenv("WXO_ENVIRONMENT_NAME", "live").strip().lower()
    if environment not in ("live", "draft"):
        logger.warning("WXO_ENVIRONMENT_NAME must be live or draft; telemetry disabled")
        return False

    provider = trace.get_tracer_provider()
    if not hasattr(provider, "add_span_processor"):  # local run: no SDK provider configured yet
        provider = TracerProvider()
        trace.set_tracer_provider(provider)

    exporter = WxoSpanExporter(
        endpoint=os.environ["WXO_OTEL_URL"].strip(),
        tenant_id=os.environ["WXO_TENANT_ID"].strip(),
        agent_id=agent_id,
        agent_name=os.getenv("WXO_AGENT_NAME", "retail_banking_support").strip(),
        environment_name=environment,
        capture_content=os.getenv("WXO_CAPTURE_CONTENT", "true").strip().lower() != "false",
        base_resource=getattr(provider, "resource", Resource.get_empty()),
        token_provider=token_provider or _exchange_token,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter, schedule_delay_millis=2000))
    _installed = True
    logger.info("watsonx Orchestrate telemetry enabled for agent %s", agent_id)
    return True
