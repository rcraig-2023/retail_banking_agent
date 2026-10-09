from typing import Any
import asyncio
from strands import Agent
from strands.agent.conversation_manager.null_conversation_manager import NullConversationManager
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from memory.session import get_memory_session_manager
from wxo_telemetry import enable_wxo_telemetry
from banking_tools import tools
from guardrails import ConversationGuard

app = BedrockAgentCoreApp()
enable_wxo_telemetry()  # no-op unless WXO_AGENT_ID is configured
log = app.logger

DEFAULT_SYSTEM_PROMPT = """
You are Lena, a customer support agent for Lendyr Bank, a fictional retail bank. You are friendly, concise and professional.

CRITICAL RULES:
1. Introduce yourself ("Welcome to Lendyr Bank, I'm Lena.") in your very first reply only. Never repeat the greeting later.
2. You must not share any account information until the customer is authenticated. If the customer asks for anything
   account-related before that, ask for their customer ID and PIN and wait for their reply. Only call
   authenticate_customer after the customer has typed both values, using exactly what they typed. Never guess,
   invent or use placeholder values, and never call any other tool before the customer is authenticated.
3. The system confirms authentication and shows the balances itself. Afterwards do not greet or use the
   customer's name again unless it is natural; start answers directly with the information.
4. Never repeat a customer's PIN back, and never say which of the two (ID or PIN) was wrong after a failed attempt.
5. Use the tools for all numbers: get_account_balances, get_recent_transactions, get_spending_by_category (for "how much
   did I spend on ..."), request_loan_deferral (when the customer cannot make a loan payment), report_stolen_card,
   start_loan_application.
6. Report tool results exactly: the exact balances, amounts, dates and tracking tokens. Do not round, add or change
   figures, and keep the tool's own wording and numbered lists (for example "1. Grocery ($85.20), 2. Gas ($45.00)").
7. Do not overthink. Call the tool, read the result, and answer in one or two sentences. If a tool result ends with
   "DO NOT RETRY.", do not call that tool again for the same request.
"""

def _make_conversation_manager():
    return NullConversationManager()

def agent_factory():
    cache = {}
    def get_or_create_agent(session_id, user_id):
        _actor_id = user_id
        key = f"{session_id}/{_actor_id}"
        if key not in cache:
            cache[key] = Agent(
                model=BedrockModel(model_id="us.meta.llama3-3-70b-instruct-v1:0", streaming=False),
                session_manager=get_memory_session_manager(session_id, _actor_id),
                conversation_manager=_make_conversation_manager(),
                system_prompt=DEFAULT_SYSTEM_PROMPT,
                tools=tools,
                hooks=[ConversationGuard()],
                trace_attributes={"session.id": session_id or "", "user.id": _actor_id or ""},
            )
        return cache[key]
    return get_or_create_agent
    
get_or_create_agent = agent_factory()

def strip_trailing_tool_use(messages: Any) -> list[dict]:
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")
    messages = list(messages)
    while messages:
        last = messages[-1]
        if not isinstance(last, dict):
            raise ValueError("each message must be an object")
        original_content = last.get("content", [])
        if not isinstance(original_content, list) or not all(isinstance(block, dict) for block in original_content):
            raise ValueError("each message content value must be a list of content blocks")
        content = [block for block in original_content if "toolUse" not in block]
        if len(content) == len(original_content):
            break
        if content:
            messages[-1] = {**last, "content": content}
            break
        messages.pop()
    return messages

def _extract_prompt(payload: dict):
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if "messages" in payload:
        return strip_trailing_tool_use(payload["messages"])
    if "tool_results" in payload:
        tool_results = payload["tool_results"]
        if not isinstance(tool_results, list) or not all(
            isinstance(tool_result, dict) and isinstance(tool_result.get("toolUseId"), str)
            for tool_result in tool_results
        ):
            raise ValueError("tool_results must contain objects with a toolUseId string")
        return [{"role": "user", "content": [{"toolResult": {
            "toolUseId": tr["toolUseId"],
            "status": tr.get("status", "success"),
            "content": tr.get("content", []),
        }} for tr in tool_results]}]
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    return prompt

@app.entrypoint
async def invoke(payload, context):
    log.info("Invoking Agent.....")
    session_id = getattr(context, 'session_id', 'default-session')
    user_id = getattr(context, 'user_id', 'default-user')
    agent = get_or_create_agent(session_id, user_id)
    prompt = _extract_prompt(payload)
    
    streamed_text = ""
    final_text = ""
    async for event in agent.stream_async(prompt):
        if isinstance(event, dict) and "result" in event:
            final_text = str(event["result"]).strip()
            continue
        if not isinstance(event, dict) or "event" not in event:
            continue
        cbs = event["event"].get("contentBlockStart")
        if cbs is not None and not cbs.get("start"):
            continue
        delta = (event["event"].get("contentBlockDelta") or {}).get("delta", {})
        streamed_text += delta.get("text", "")
        yield event

    # Replies fixed by ConversationGuard (end_turn) are never streamed as text events; they
    # only appear in the final result. Send them so the client does not see an empty reply.
    if final_text and final_text not in streamed_text:
        yield {"event": {"contentBlockDelta": {"delta": {"text": final_text}, "contentBlockIndex": 0}}}

if __name__ == "__main__":
    app.run()