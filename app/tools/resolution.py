"""Mock execution of a resolution (D50). Writes a ledger line and a runtime order change.

Where it sits: after the agent has drafted a reply that the policy rules approve, the agent calls
`execute_resolution` to carry the action out. There is no real payment system, so "execute" means
two local writes: one line in the resolution ledger (config.RESOLUTION_LOG) and a change in the
runtime overlay (config.ORDER_STATE_FILE) that app/tools/orders.py merges into every read. The
source data files are never edited.

The tool does not trust its caller. It re-checks ownership and re-runs the eligibility engine
itself, so the LLM can never cause an action or an amount that the rules did not approve.
"""

import json
import threading
import uuid
from datetime import UTC, datetime
from typing import get_args

from app import config
from app.observability.logging import log_event
from app.tools.eligibility import Recommendation, check_eligibility
from app.tools.fault_injection import injected_fault
from app.tools.orders import get_customer, get_order, read_state, write_state
from app.tools.results import ToolError, ToolOk

# Actions the agent may carry out itself. `update_address` is not here on purpose: the app never
# handles the customer's address (personal data is masked), so that case is only answered.
EXECUTABLE_ACTIONS = ("refund", "refund_partial", "replacement", "store_credit", "cancel_order")
# These also count as a refund for the POL-10 "three refunds in 90 days" rule.
MONEY_ACTIONS = {"refund", "refund_partial", "store_credit"}  # settle a request with money
REFUND_ACTIONS = {"refund", "refund_partial", "cancel_order"}

# One writer at a time, so two requests cannot both pass the duplicate check.
_LOCK = threading.Lock()


def _already_done(order: dict, action: str, request_type: str, item_sku: str | None):
    """Return an already_resolved ToolError if this action was executed before for this request."""
    for done in order.get("resolutions", []):
        # A refund, a partial refund and store credit all settle the same request with money, so
        # asking for another of them is a repeat too (the agent may pick a different one the
        # second time, and nothing is left to pay out).
        same_action = done["action"] == action or (
            done["action"] in MONEY_ACTIONS and action in MONEY_ACTIONS
        )
        if same_action and (done["request_type"], done["item_sku"]) == (request_type, item_sku):
            return ToolError(
                code="already_resolved", message=f"Already executed as {done['resolution_id']}."
            )
    return None


def execute_resolution(
    order_id: str,
    customer_id: str,
    request_type: str,
    action: str,
    amount: float | None = None,
    item_sku: str | None = None,
    error_origin: str | None = None,
    staff_authorized: bool = False,
) -> ToolOk | ToolError:
    """Carry out an approved resolution (mock) and record it.

    Args:
        order_id: Order the resolution is for.
        customer_id: The logged-in customer; must own the order.
        request_type: The request that was decided (for example "return"); the rules are re-run.
        action: One of EXECUTABLE_ACTIONS; it must be an option the rules allow.
        amount: Money to refund or credit; None uses the rules' maximum. It may not exceed it.
        item_sku: Which item, if the request was about one item.
        error_origin: "brightcart" or "customer", for personalisation mistakes.
        staff_authorized: True only when a staff member decided an escalated case (D51). It lifts
            the agent-authority checks (approval level, allowed options and the rules' amount
            ceiling); the order must still exist, belong to the customer, not repeat an executed
            request, and the amount may not exceed what is left to refund on the order.

    Returns:
        ToolOk with resolution_id, action and amount, or a ToolError: not_allowed when the rules
        do not approve it, already_resolved for a repeat, invalid_input for bad arguments.
    """
    if fault := injected_fault("execute_resolution"):
        return fault
    if action not in EXECUTABLE_ACTIONS or action not in get_args(Recommendation):
        return ToolError(code="not_allowed", message=f"'{action}' is not an executable action.")
    # --- Step 1: re-run the rules (this also checks that the order exists and is the customer's) ---
    elig = check_eligibility(order_id, request_type, customer_id, item_sku, error_origin)
    if isinstance(elig, ToolError):
        return elig
    rules = elig.data
    max_amount = rules.get("max_amount") or 0.0
    oid = order_id.strip().upper()
    # --- Step 2: a repeat of an executed request is "already resolved", not a new decision ---
    if repeat := _already_done(get_order(oid), action, request_type, item_sku):
        return repeat
    # --- Step 3: the rules must approve this action, inside agent authority, for this amount ---
    if staff_authorized:
        # A staff member decided: the ceiling is what is still refundable on the order.
        order = get_order(oid)
        ceiling = round(
            sum(i["price"] for i in order["items"]) + order["shipping_paid"]
            - order["refunded_amount"], 2,
        )  # fmt: skip
    else:
        if not rules["eligible"] or rules["approval_level"] != "agent":
            return ToolError(
                code="not_allowed", message="The rules do not approve this resolution."
            )
        if action not in rules["allowed_recommendations"]:
            return ToolError(code="not_allowed", message=f"The rules do not allow '{action}'.")
        ceiling = max_amount
    amount = max_amount if amount is None else amount
    if amount <= 0 or amount > ceiling + 0.005:
        return ToolError(code="not_allowed", message="The amount is outside what the rules allow.")
    amount = round(amount, 2)
    with _LOCK:
        order = get_order(oid)
        # Checked again inside the lock, so two requests at once cannot both pass.
        if repeat := _already_done(order, action, request_type, item_sku):
            return (
                repeat  # --- Step 4: carry it out: the order change first, then the ledger line ---
            )
        record = {
            "resolution_id": f"RES-{uuid.uuid4().hex[:8].upper()}",
            "ts": datetime.now(UTC).isoformat(),
            "order_id": oid,
            "request_type": request_type,
            "item_sku": item_sku,
            "action": action,
            "amount": amount,
            "policy_ids": rules["policy_ids"],
        }
        state = read_state()
        changes = dict(state["orders"].get(oid, {}))
        changes["refunded_amount"] = round(order["refunded_amount"] + amount, 2)
        changes["resolutions"] = [*order.get("resolutions", []), record]
        if action == "cancel_order":
            changes["status"] = "cancelled"
        if action in REFUND_ACTIONS:
            changes["refund_date"] = config.AS_OF_DATE.isoformat()
            customer = dict(state["customers"].get(customer_id, {}))
            # get_customer already includes earlier executed changes, so just add one.
            current = (get_customer(customer_id) or {}).get("refunds_last_90_days", 0)
            customer["refunds_last_90_days"] = current + 1
            state["customers"][customer_id] = customer
        state["orders"][oid] = changes
        try:
            write_state(state)
            config.RESOLUTION_LOG.parent.mkdir(parents=True, exist_ok=True)
            with config.RESOLUTION_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            return ToolError(code="unavailable", message="The resolution could not be recorded.")
    log_event("resolution_executed", status=action)
    return ToolOk(
        data={"resolution_id": record["resolution_id"], "action": action, "amount": amount}
    )
