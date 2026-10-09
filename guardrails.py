"""Code-level guardrails for the Lena agent (Strands hook provider).

Prompts alone do not stop a smaller model from inventing credentials or retrying a
failed tool call forever. This provider enforces the conversation rules in code:

1. authenticate_customer is cancelled unless both values appear in the customer's own
   latest message, so the model can never call it with guessed or placeholder values.
2. A turn may make at most MAX_TOOL_CALLS_PER_TURN tool calls.
3. When a tool returns a terminal problem (not authenticated, missing, failed, locked,
   limit reached) the turn ends immediately with a fixed, safe reply.
"""
from __future__ import annotations

from strands.hooks import (
    AfterInvocationEvent,
    AfterToolsEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from banking_tools import balances_summary

MAX_TOOL_CALLS_PER_TURN = 4

_GREETING = "Welcome to Lendyr Bank, I'm Lena. "
_ASK_CREDENTIALS = "To get started, could you please provide your customer ID and PIN?"

_K_GREETED = "greeted"

# tool-result prefix -> fixed reply (None means "use the credentials prompt")
_TERMINAL_REPLIES = {
    "NOT AUTHENTICATED": None,
    "MISSING": None,
    "FAILED: customer ID or PIN": (
        "I'm sorry, I couldn't verify those details. Please re-enter your customer ID and PIN."
    ),
    "LOCKED": (
        "For your security, this session is locked after too many unsuccessful attempts. "
        "Please contact Lendyr Bank by phone to continue."
    ),
    "LIMIT": "I'm having trouble completing that right now. Please try again.",
}


def _latest_customer_text(agent) -> str:
    """Text of the most recent message the customer actually typed (not a tool result)."""
    for message in reversed(agent.messages):
        if message.get("role") != "user":
            continue
        texts = [b["text"] for b in message.get("content", []) if isinstance(b, dict) and "text" in b]
        if texts:
            return " ".join(texts)
    return ""


def _result_texts(message) -> list[str]:
    texts = []
    for block in message.get("content", []):
        result = block.get("toolResult") if isinstance(block, dict) else None
        if result:
            texts.extend(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
    return texts


class ConversationGuard(HookProvider):
    def __init__(self) -> None:
        self._calls_this_turn = 0

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeInvocationEvent, self._reset)
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolsEvent, self._after_tools)
        registry.add_callback(AfterInvocationEvent, self._mark_greeted)

    def _reset(self, event: BeforeInvocationEvent) -> None:
        self._calls_this_turn = 0

    def _before_tool(self, event: BeforeToolCallEvent) -> None:
        self._calls_this_turn += 1
        if self._calls_this_turn > MAX_TOOL_CALLS_PER_TURN:
            event.cancel_tool = "LIMIT: too many tool calls in one turn."
            return
        if event.tool_use["name"] == "authenticate_customer":
            typed = _latest_customer_text(event.agent)
            args = event.tool_use.get("input", {})
            values = [str(args.get("customer_id", "")).strip(), str(args.get("pin", "")).strip()]
            if not all(v and v in typed for v in values):
                event.cancel_tool = "MISSING: the customer has not typed a customer ID and PIN yet."

    def _after_tools(self, event: AfterToolsEvent) -> None:
        for text in _result_texts(event.message):
            if text.startswith("SUCCESS: customer authenticated"):
                first_name = text.split("First name:")[1].split(".")[0].strip()
                event.end_turn = (
                    f"Thank you, {first_name}. You are successfully authenticated. "
                    f"Here are your current balances: {balances_summary()}."
                )
                return
            for prefix, reply in _TERMINAL_REPLIES.items():
                if text.startswith(prefix):
                    event.end_turn = reply if reply is not None else self._credentials_prompt(event.agent)
                    return

    @staticmethod
    def _credentials_prompt(agent) -> str:
        if agent.state.get(_K_GREETED):
            return _ASK_CREDENTIALS
        return _GREETING + _ASK_CREDENTIALS

    @staticmethod
    def _mark_greeted(event: AfterInvocationEvent) -> None:
        event.agent.state.set(_K_GREETED, True)
