"""A2A entrypoint for the retail banking agent.

Serves the same tools and system prompt as main.py over the A2A protocol so it can be
registered as an external agent in watsonx Orchestrate. main.py (HTTP protocol) is unchanged.
"""
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from bedrock_agentcore.runtime import serve_a2a
from strands import Agent
from strands.models import BedrockModel
from strands.multiagent.a2a.executor import StrandsA2AExecutor

from guardrails import ConversationGuard
from main import DEFAULT_SYSTEM_PROMPT, tools

AGENT_NAME = "Retail Banking Customer Support"
AGENT_DESCRIPTION = (
    "Retail banking customer care assistant (Lena) for a fictional retail bank. Authenticates the "
    "customer, then shows balances and recent transactions, summarizes spending by category, "
    "grants auto loan payment deferrals, reports stolen cards, and starts loan applications."
)


def build_agent(context_id: str) -> Agent:
    return Agent(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        model=BedrockModel(model_id="us.meta.llama3-3-70b-instruct-v1:0", streaming=False),
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        tools=tools,
        hooks=[ConversationGuard()],
        trace_attributes={"session.id": context_id},
    )


def build_agent_card() -> AgentCard:
    skills = [
        AgentSkill(
            id="authenticate_customer",
            name="Authenticate customer",
            description="Verifies the customer ID and PIN before any account information is shared.",
            tags=["banking", "identity"],
        ),
        AgentSkill(
            id="get_account_balances",
            name="Account balances",
            description="Shows balances for checking, savings, credit card and auto loan accounts.",
            tags=["banking", "accounts"],
        ),
        AgentSkill(
            id="get_recent_transactions",
            name="Recent transactions",
            description="Lists the most recent transactions.",
            tags=["banking", "accounts"],
        ),
        AgentSkill(
            id="get_spending_by_category",
            name="Spending by category",
            description="Totals spending in a category such as dining over the last 30 days.",
            tags=["banking", "insights"],
        ),
        AgentSkill(
            id="request_loan_deferral",
            name="Loan payment deferral",
            description="Checks eligibility for and grants a 30-day auto loan payment deferral.",
            tags=["banking", "loans"],
        ),
        AgentSkill(
            id="report_stolen_card",
            name="Report stolen card",
            description="Locks a compromised card and returns a tracking token.",
            tags=["banking", "cards"],
        ),
        AgentSkill(
            id="start_loan_application",
            name="Start loan application",
            description="Initiates a retail loan application.",
            tags=["banking", "loans"],
        ),
    ]
    return AgentCard(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        url="http://localhost:9000/",  # replaced with the runtime URL by serve_a2a/AGENTCORE_RUNTIME_URL
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=True),
        skills=skills,
        default_input_modes=["text"],
        default_output_modes=["text"],
    )


if __name__ == "__main__":
    serve_a2a(StrandsA2AExecutor(agent_factory=build_agent), build_agent_card())
