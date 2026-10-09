"""Agent tools for the Lendyr Bank demo, backed by fictional in-memory data.

Authentication is enforced here, in code: every data tool refuses to answer until
authenticate_customer has succeeded for the current agent session (state lives in
agent.state, so it cannot be set by the model or the caller). Replace the mock data
with calls to a core-banking API for anything beyond a demo.

Tool results that signal a problem end with "DO NOT RETRY." because the model otherwise
loops on failures.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import date, timedelta
from decimal import Decimal

from strands import ToolContext, tool

# --- Fictional customer data (demo only) ------------------------------------------------
_CUSTOMER_ID = "846302"
_FIRST_NAME = "Brian"
# PBKDF2-HMAC-SHA256 of the demo PIN, so the PIN itself is not stored in source.
_PIN_SALT = b"lendyr-demo-salt-846302"
_PIN_ITERATIONS = 600_000
_PIN_HASH = "91a53fd1fec906230b21fd81fb5afe29ee4e49beb77f86cacfc1c45e8bac84f3"
_MAX_FAILED_ATTEMPTS = 3

_BALANCES = {
    "Checking": Decimal("420.50"),
    "Savings": Decimal("5500.00"),
    "Credit Card": Decimal("1250.00"),
    "Auto Loan": Decimal("14000.00"),
}

# (days ago, description, category, amount), most recent first
_TRANSACTIONS = [
    (1, "Grocery", "groceries", Decimal("85.20")),
    (2, "Gas", "fuel", Decimal("45.00")),
    (3, "Target", "shopping", Decimal("112.00")),
    (4, "Coffee", "dining", Decimal("4.50")),
    (5, "Utility Bill", "utilities", Decimal("95.00")),
    (7, "Sushi Bar", "dining", Decimal("96.00")),
    (10, "Bistro", "dining", Decimal("128.75")),
    (14, "Brunch Cafe", "dining", Decimal("54.00")),
    (20, "Pizza Place", "dining", Decimal("62.25")),
    (45, "Steakhouse", "dining", Decimal("70.00")),  # outside the 30-day window
]

_AUTO_LOAN = {
    "balance": Decimal("14000.00"),
    "on_time_payments_last_12": 12,
    "deferral_interest_30_days": Decimal("45.00"),
}

# agent.state keys
_K_AUTH = "authenticated_customer_id"
_K_FAILED = "failed_pin_attempts"
_K_DEFERRAL = "loan_deferral_granted"


def _money(value: Decimal) -> str:
    return f"${value:,.2f}"


def balances_summary() -> str:
    """'Checking: $420.50 | Savings: ...' for the demo customer (callers must have authenticated)."""
    return " | ".join(f"{name}: {_money(amount)}" for name, amount in _BALANCES.items())


def _not_authenticated() -> str:
    return (
        "NOT AUTHENTICATED: ask the customer for their customer ID and PIN, then call "
        "authenticate_customer. Do not share any account information yet. DO NOT RETRY."
    )


def _authenticated(tool_context: ToolContext) -> bool:
    return bool(tool_context.agent.state.get(_K_AUTH))


@tool(context=True)
def authenticate_customer(customer_id: str, pin: str, tool_context: ToolContext) -> str:
    """Authenticates a customer with their customer ID and PIN. Must succeed before any account information is shared."""
    state = tool_context.agent.state
    if state.get(_K_AUTH):
        return f"SUCCESS: {_FIRST_NAME} is already authenticated."
    failed = state.get(_K_FAILED) or 0
    if failed >= _MAX_FAILED_ATTEMPTS:
        return "LOCKED: too many failed attempts. Ask the customer to contact the bank by phone. DO NOT RETRY."

    # Reject malformed input (e.g. the model passing placeholders before the customer has
    # replied) without counting it as a failed attempt.
    if not (str(customer_id).strip().isdigit() and str(pin).strip().isdigit()):
        return (
            "MISSING: the customer has not provided a numeric customer ID and PIN yet. "
            "Ask them for both, and only call this tool with the values they typed. DO NOT RETRY."
        )

    candidate = hashlib.pbkdf2_hmac(
        "sha256", str(pin).strip().encode(), _PIN_SALT, _PIN_ITERATIONS
    ).hex()
    id_ok = hmac.compare_digest(str(customer_id).strip().encode(), _CUSTOMER_ID.encode())
    pin_ok = hmac.compare_digest(candidate.encode(), _PIN_HASH.encode())
    if id_ok and pin_ok:
        state.set(_K_AUTH, _CUSTOMER_ID)
        state.set(_K_FAILED, 0)
        return f"SUCCESS: customer authenticated. First name: {_FIRST_NAME}. DO NOT RETRY."

    state.set(_K_FAILED, failed + 1)
    remaining = _MAX_FAILED_ATTEMPTS - (failed + 1)
    return (
        f"FAILED: customer ID or PIN is incorrect. {remaining} attempt(s) remaining. "
        "Do not say which one was wrong. DO NOT RETRY."
    )


@tool(context=True)
def get_account_balances(tool_context: ToolContext) -> str:
    """Returns the current balance of every account the authenticated customer holds."""
    if not _authenticated(tool_context):
        return _not_authenticated()
    return f"SUCCESS: {balances_summary()}. DO NOT RETRY."


@tool(context=True)
def get_recent_transactions(tool_context: ToolContext, count: int = 5) -> str:
    """Returns the authenticated customer's most recent transactions, newest first."""
    if not _authenticated(tool_context):
        return _not_authenticated()
    count = max(1, min(int(count), 10))
    items = [f"{i}. {desc} ({_money(amt)})" for i, (_, desc, _, amt) in enumerate(_TRANSACTIONS[:count], 1)]
    return f"SUCCESS: Here are your last {len(items)} transactions: " + ", ".join(items) + ". DO NOT RETRY."


@tool(context=True)
def get_spending_by_category(category: str, tool_context: ToolContext, days: int = 30) -> str:
    """Returns how much the authenticated customer spent in a category (groceries, fuel, shopping, dining, utilities) over the last N days."""
    if not _authenticated(tool_context):
        return _not_authenticated()
    days = max(1, min(int(days), 365))
    wanted = category.strip().lower()
    known = sorted({c for _, _, c, _ in _TRANSACTIONS})
    if wanted not in known:
        return f"FAILED: unknown category '{category}'. Known categories: {', '.join(known)}. DO NOT RETRY."
    total = sum((amt for ago, _, cat, amt in _TRANSACTIONS if cat == wanted and ago <= days), Decimal("0"))
    return f"SUCCESS: You have spent {_money(total)} on {wanted} in the last {days} days. DO NOT RETRY."


@tool(context=True)
def request_loan_deferral(tool_context: ToolContext) -> str:
    """Checks the authenticated customer's eligibility for a 30-day auto loan payment deferral and grants it if they qualify."""
    if not _authenticated(tool_context):
        return _not_authenticated()
    state = tool_context.agent.state
    interest = _AUTO_LOAN["deferral_interest_30_days"]
    new_balance = _AUTO_LOAN["balance"] + interest
    if state.get(_K_DEFERRAL):
        return (
            f"ALREADY GRANTED: a 30-day extension is already in place. New loan balance is {_money(new_balance)}. "
            "DO NOT RETRY."
        )
    if _AUTO_LOAN["on_time_payments_last_12"] < 12:
        return "DECLINED: payment history does not meet the deferral requirements. DO NOT RETRY."
    state.set(_K_DEFERRAL, True)
    return (
        "APPROVED: 30-day extension granted. "
        f"{_money(interest)} interest accrued over 30 days. New payment date is next month. "
        f"New loan balance is {_money(new_balance)}. DO NOT RETRY."
    )


@tool(context=True)
def report_stolen_card(account_id: str, tool_context: ToolContext) -> str:
    """Locks a compromised card and provides a tracking token. Requires an authenticated customer."""
    if not _authenticated(tool_context):
        return _not_authenticated()
    return f"SUCCESS: Card linked to {account_id} locked. Token: 88X-QZT. DO NOT RETRY."


@tool(context=True)
def start_loan_application(account_id: str, loan_amount: int, tool_context: ToolContext) -> str:
    """Initiates a retail loan application. Requires an authenticated customer."""
    if not _authenticated(tool_context):
        return _not_authenticated()
    return "SUCCESS: Loan started. DO NOT RETRY."


tools = [
    authenticate_customer,
    get_account_balances,
    get_recent_transactions,
    get_spending_by_category,
    request_loan_deferral,
    report_stolen_card,
    start_loan_application,
]
