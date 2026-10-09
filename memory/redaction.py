"""Keep PINs and passwords out of AgentCore Memory.

The memory session manager stores every message (and tool-call arguments) as JSON text, and
long-term memory strategies derive facts and summaries from that text. This converter masks
secrets before anything is written, so they never reach memory. The live conversation in the
running agent is untouched: the model still sees what the customer typed.

Masks both prose ("my PIN is 24592") and JSON tool arguments ("pin": "24592").
"""
import re
from typing import Tuple

from bedrock_agentcore.memory.integrations.strands.bedrock_converter import AgentCoreMemoryConverter
from strands.types.session import SessionMessage

_SECRET_PATTERN = re.compile(r"(?i)\b(pin|passcode|password)\b(\W*(?:is|:)?\W*)\d{3,12}")


def redact_secrets(text: str) -> str:
    return _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}****", text)


class RedactingConverter(AgentCoreMemoryConverter):
    @staticmethod
    def message_to_payload(session_message: SessionMessage) -> list[Tuple[str, str]]:
        return [
            (redact_secrets(text), role)
            for text, role in AgentCoreMemoryConverter.message_to_payload(session_message)
        ]
