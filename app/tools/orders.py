"""Mock order system. The source files in data/ are read-only.

Where it sits: the agent's `lookup_order` tool and the eligibility checks read order and
customer data from JSON files in the data folder, standing in for a real order database.
When the agent executes a resolution (app/tools/resolution.py), the change is stored in a small
runtime overlay file (config.ORDER_STATE_FILE) that is merged in whenever an order or customer
is read, so the data files themselves are never edited.
"""

import json
import re
from functools import lru_cache

from app import config
from app.observability.logging import log_event
from app.tools.fault_injection import injected_fault
from app.tools.results import ToolError, ToolOk

# Order ids look like ORD-1234 (exactly four digits).
ORDER_ID_PATTERN = re.compile(r"^ORD-\d{4}$")


@lru_cache(maxsize=1)
def _orders() -> dict[str, dict]:
    """Load orders.json once (cached) and index the orders by order_id."""
    raw = json.loads((config.DATA_DIR / "orders.json").read_text(encoding="utf-8"))
    return {o["order_id"]: o for o in raw}


@lru_cache(maxsize=1)
def _customers() -> dict[str, dict]:
    """Load customers.json once (cached) and index the customers by customer_id."""
    raw = json.loads((config.DATA_DIR / "customers.json").read_text(encoding="utf-8"))
    return {c["customer_id"]: c for c in raw}


def read_state() -> dict:
    """Load the runtime overlay: {"orders": {id: changes}, "customers": {id: changes}}.

    A missing or unreadable file means "no executed changes yet".
    """
    try:
        state = json.loads(config.ORDER_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    return {"orders": state.get("orders", {}), "customers": state.get("customers", {})}


def write_state(state: dict) -> None:
    """Save the runtime overlay (written whole; the file is tiny)."""
    config.ORDER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.ORDER_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def reset_runtime_state() -> int:
    """Delete the order overlay and the resolution ledger, so a new session has no refund history.

    Called once when the server starts, next to the case reset (D53, D60). The audit and
    escalation logs are not touched. A missing file is fine.

    Returns:
        How many files were deleted.
    """
    deleted = 0
    for path in (config.ORDER_STATE_FILE, config.RESOLUTION_LOG):
        if path.exists():
            path.unlink(missing_ok=True)
            deleted += 1
    log_event("order_state_reset", removed=deleted)
    return deleted


def get_order(order_id: str) -> dict | None:
    """Internal helper (no ownership check). Used by other tools, not exposed to the LLM."""
    order = _orders().get(order_id)
    if order is None:
        return None
    changes = read_state()["orders"].get(order_id)
    # Copy, so the cached source record is never changed by an executed resolution.
    return {**order, **changes} if changes else order


def get_customer(customer_id: str) -> dict | None:
    """Return the customer record, or None if the id is unknown."""
    customer = _customers().get(customer_id)
    if customer is None:
        return None
    changes = read_state()["customers"].get(customer_id)
    return {**customer, **changes} if changes else customer


def check_order_access(order_id: str, customer_id: str) -> ToolError | dict:
    """Validate the ID, find the order and enforce ownership. Returns the order or a ToolError.

    Someone else's order gets exactly the same `not_found` error as an order that does not exist,
    so neither the tool trace nor the reply can tell a customer whether an id exists (D55).

    Args:
        order_id: Order id as typed by the customer (case and spaces are tolerated).
        customer_id: The logged-in customer, who must own the order.
    """
    normalised = (order_id or "").strip().upper()
    if not ORDER_ID_PATTERN.match(normalised):
        return ToolError(code="invalid_input", message="Order ID must look like ORD-1234.")
    not_found = ToolError(code="not_found", message=f"Order {normalised} was not found.")
    order = get_order(normalised)
    if order is None:
        return not_found
    if order["customer_id"] != customer_id:
        # Server-side audit only (no order id, no owner): the customer sees a plain "not found".
        log_event("order_access_denied")
        return not_found
    return order


def lookup_order(order_id: str, customer_id: str) -> ToolOk | ToolError:
    """Return order facts without any name, email or address.

    Args:
        order_id: Order id to look up.
        customer_id: The logged-in customer; other customers' orders look "not found".
    """
    if fault := injected_fault("lookup_order"):
        return fault
    order = check_order_access(order_id, customer_id)
    if isinstance(order, ToolError):
        return order
    # Strip the customer id so the LLM only sees order facts.
    facts = {k: v for k, v in order.items() if k != "customer_id"}
    return ToolOk(data=facts)
