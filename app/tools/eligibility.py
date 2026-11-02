"""Deterministic eligibility rules engine.

Deterministic means the same input always gives the same answer; there is no LLM guessing here.
Where it sits: after the agent knows the order and the request type, `check_eligibility` says
what policy allows (refund, replacement, deny...). The LLM never overrules this result.

All dates use config.AS_OF_DATE ("today", fixed so tests and demos are repeatable).
Rules come from data/policies (POL-01 to POL-10); policy ids are returned with each result.

How the file is laid out:
    1. Types and result model.
    2. Small helpers (date maths, money rounding, result builder).
    3. One handler per request type (`_return`, `_cancel`...).
    4. `HANDLERS` table, the always-escalate override (POL-10) and `check_eligibility`.
"""

from datetime import date, timedelta
from typing import Literal, get_args

from pydantic import BaseModel

from app import config
from app.tools.fault_injection import injected_fault
from app.tools.orders import check_order_access, get_customer
from app.tools.results import ToolError, ToolOk

# What the customer is asking for (the LLM's intent without "policy_question" and "other").
RequestType = Literal[
    "return",
    "damaged",
    "defective",
    "wrong_item",
    "late",
    "lost",
    "cancel",
    "change_address",
    "warranty",
    "price_adjustment",
    "refund_status",
]
# What we advise the human agent to do.
Recommendation = Literal[
    "refund",
    "refund_partial",
    "replacement",
    "store_credit",
    "cancel_order",
    "update_address",
    "deny",
    "inform",
    "need_info",
    "escalate",
]

# Requests that can lead to money going back to the customer (POL-10 triggers apply).
MONEY_REQUESTS = {
    "return",
    "damaged",
    "defective",
    "wrong_item",
    "late",
    "lost",
    "warranty",
    "price_adjustment",
}


class EligibilityResult(BaseModel):
    """What policy allows for one order and one request type."""

    order_id: str
    request_type: str
    eligible: bool
    recommendation: Recommendation  # the single best option
    allowed_recommendations: list[Recommendation]  # options the draft may choose from
    reason_codes: list[str]  # short ids such as "FINAL_SALE", used for audit and tests
    policy_ids: list[str]
    max_amount: float | None = None  # most we may refund (the amount is a ceiling)
    approval_level: Literal["agent", "manager"] = "agent"
    requires_evidence: list[str] = []
    next_steps: list[str] = []
    escalation_reason: str | None = None  # reason_code for escalate_to_human
    urgency: Literal["normal", "urgent"] = "normal"


# --- Helpers ---------------------------------------------------------------------------


def _days_since(iso: str | None) -> int | None:
    """Whole days from an ISO date (YYYY-MM-DD) to "today"; None if there is no date."""
    if not iso:
        return None
    return (config.AS_OF_DATE - date.fromisoformat(iso)).days


def _business_days_since(iso: str) -> int:
    """Count Monday-to-Friday days after an ISO date, up to "today". Holidays are ignored."""
    d, count = date.fromisoformat(iso), 0
    while d < config.AS_OF_DATE:
        d += timedelta(days=1)
        if d.weekday() < 5:  # Monday is 0 and Friday is 4, so < 5 means a weekday
            count += 1
    return count


def _money(x: float) -> float:
    """Round to cents and never go below zero."""
    return round(max(x, 0.0), 2)


def _out(order, request_type, rec, *, eligible=False, allowed=None, **kw) -> EligibilityResult:
    """Build an EligibilityResult, filling in sensible defaults.

    Args:
        order: The order dict.
        request_type: The request being decided.
        rec: The recommendation.
        eligible: True when policy lets us help the customer.
        allowed: Other allowed recommendations; defaults to just `rec`.
        **kw: `reasons` and `policies` lists, plus any other EligibilityResult field.
    """
    return EligibilityResult(
        order_id=order["order_id"],
        request_type=request_type,
        eligible=eligible,
        recommendation=rec,
        allowed_recommendations=allowed or [rec],
        reason_codes=kw.pop("reasons", []),
        policy_ids=kw.pop("policies", []),
        **kw,
    )


def _select_items(order, item_sku):
    """Return the one item matching `item_sku`, or every item when no SKU is given."""
    if item_sku:
        return [i for i in order["items"] if i["sku"] == item_sku]
    return order["items"]


def _remaining_value(order) -> float:
    """Item total minus what was already refunded: the most money still at stake."""
    return sum(i["price"] for i in order["items"]) - order["refunded_amount"]


# ---- handlers: one per request type -------------------------------------------------
# Each handler takes (order, request_type, ...) and returns an EligibilityResult.
# They accept **_ so check_eligibility can pass the same arguments to all of them.


def _return(order, rt, item_sku, **_):
    """Return request (POL-01, POL-02, POL-03): window, item exclusions, fees, amount."""
    # --- Step 1: the order must have arrived ---
    if order["status"] != "delivered":
        return _out(order, rt, "deny", reasons=["NOT_DELIVERED"], policies=["POL-01"],
                    next_steps=["A return can be requested only after delivery."])  # fmt: skip
    # --- Step 2: work out which item(s) the customer means ---
    items = _select_items(order, item_sku)
    if not items:
        return _out(order, rt, "need_info", reasons=["ITEM_NOT_IN_ORDER"], policies=["POL-01"],
                    next_steps=["Ask which item on the order is being returned."])  # fmt: skip
    if len(order["items"]) > 1 and not item_sku:
        return _out(order, rt, "need_info", reasons=["WHICH_ITEM"], policies=["POL-01"],
                    next_steps=["Ask which item on the order is being returned."])  # fmt: skip
    # --- Step 3: items that can never be returned (POL-03 exclusions) ---
    for it in items:
        blocked = None
        if it["category"] == "gift_card":
            blocked = ("GIFT_CARD", ["POL-03", "POL-09"])
        elif it["final_sale"]:
            blocked = ("FINAL_SALE", ["POL-03"])
        elif it["personalised"]:
            blocked = ("PERSONALISED_ITEM", ["POL-03"])
        elif it["category"] == "consumable" and it["opened"]:
            blocked = ("OPENED_CONSUMABLE", ["POL-03"])
        elif it["category"] == "hygiene" and it["opened"]:
            blocked = ("HYGIENE_ITEM", ["POL-03"])
        if blocked:
            return _out(order, rt, "deny", reasons=[blocked[0]], policies=blocked[1],
                        next_steps=["Explain the rule. Ask whether the item is damaged or "
                                    "defective (POL-04 exceptions)."])  # fmt: skip
    # --- Step 4: return window (counted from the delivery date) ---
    days = _days_since(order["delivered_date"])
    if days > config.RETURN_WINDOW_DAYS:
        return _out(order, rt, "deny", reasons=["OUTSIDE_RETURN_WINDOW"], policies=["POL-01"],
                    next_steps=["Explain the 30-day window. Ask whether the item is damaged or "
                                "defective."])  # fmt: skip
    # --- Step 5: refund amount. A flat return-shipping fee is deducted; it is higher for orders
    # delivered outside Metro Manila (POL-01). ---
    outside_metro = order["shipping_region"] != config.METRO_MANILA
    fee = (
        config.RETURN_FEE_OUTSIDE_METRO_MANILA if outside_metro else config.RETURN_FEE_METRO_MANILA
    )
    # Never refund more than is left after earlier refunds.
    base = min(sum(i["price"] for i in items), _remaining_value(order))
    # "Partial" if a refund was already paid or only some items come back.
    partial = order["refunded_amount"] > 0 or len(items) < len(order["items"])
    reasons = ["WITHIN_RETURN_WINDOW"]
    steps = ["Refund goes to the original payment method 5-7 business days after receipt."]
    if outside_metro:
        reasons.append("OUTSIDE_METRO_MANILA_FEE")
    steps.append(
        f"A flat {config.peso(fee)} return-shipping fee is deducted from the refund "
        f"({'delivered outside' if outside_metro else 'delivered within'} Metro Manila)."
    )
    if order["refunded_amount"] > 0 and _money(base - fee) <= 0:
        # An earlier refund already paid out everything the rules allow: say so, do not execute.
        return _out(order, rt, "inform", eligible=True, reasons=["ALREADY_REFUNDED"],
                    policies=["POL-01", "POL-02"], max_amount=0.0,
                    next_steps=["This order was already refunded, so nothing is left to pay "
                                "out. Tell the customer; do not refund again."])  # fmt: skip
    if order["refunded_amount"] > 0:
        reasons.append("PARTIAL_REFUND_ALREADY_ISSUED")
    return _out(order, rt, "refund_partial" if partial else "refund", eligible=True,
                allowed=["refund", "refund_partial", "store_credit"], reasons=reasons,
                policies=["POL-01", "POL-02"], max_amount=_money(base - fee),
                next_steps=steps)  # fmt: skip


def _fault_claim(order, rt, item_sku, **kw):
    """damaged, defective and wrong_item (POL-04, with warranty fallback for defects)."""
    # --- Step 1: the order must have arrived ---
    if order["status"] != "delivered":
        return _out(order, rt, "deny", reasons=["NOT_DELIVERED"], policies=["POL-04"],
                    next_steps=["Claims can be made only after delivery."])  # fmt: skip
    days = _days_since(order["delivered_date"])
    # If the SKU matches nothing, fall back to the whole order rather than failing.
    items = _select_items(order, item_sku) or order["items"]
    # --- Step 2: personalised wrong items depend on who made the mistake (POL-03) ---
    if rt == "wrong_item" and any(i["personalised"] for i in items):
        origin = kw.get("error_origin")
        if origin not in ("brightcart", "customer"):
            return _out(order, rt, "need_info", reasons=["ERROR_ORIGIN_UNKNOWN"],
                        policies=["POL-03"],
                        next_steps=["Ask the customer for the text they entered, to learn who "
                                    "made the error."])  # fmt: skip
        if origin == "customer":
            return _out(order, rt, "deny", reasons=["CUSTOMER_ERROR_PERSONALISED"],
                        policies=["POL-03"])  # fmt: skip
    # --- Step 3: late claims (the day counts are the claim windows in the policy) ---
    if rt == "damaged" and days > config.DAMAGE_REPORT_DAYS:
        return _out(order, rt, "escalate", reasons=["LATE_DAMAGE_CLAIM"], policies=["POL-04"],
                    escalation_reason="late_damage_claim",
                    next_steps=["Do not approve or promise anything; escalate with a summary."])  # fmt: skip
    # A defect after 30 days is handled as a warranty claim instead.
    if rt == "defective" and days > 30:
        return _warranty(order, "defective", item_sku)
    if rt == "wrong_item" and days > 30:
        return _out(order, rt, "escalate", reasons=["LATE_WRONG_ITEM_CLAIM"], policies=["POL-04"],
                    escalation_reason="other")  # fmt: skip
    # --- Step 4: in-time claim. Items plus shipping paid come back, capped by what is left
    # after earlier refunds. ---
    amount = min(
        sum(i["price"] for i in items) + order["shipping_paid"],
        _remaining_value(order) + order["shipping_paid"],
    )
    evidence = ["photos of item and packaging"] if rt == "damaged" else []
    steps = ["Offer a replacement or a refund (customer's choice)."]
    if rt != "defective":
        steps.append("Provide a free return label. The return-shipping fee does not apply.")
    reasons = {"damaged": "DAMAGE_REPORTED_IN_TIME", "defective": "DEFECT_REPORTED_IN_TIME",
               "wrong_item": "WRONG_ITEM_SENT"}[rt]  # fmt: skip
    return _out(order, rt, "replacement", eligible=True,
                allowed=["replacement", "refund", "store_credit"], reasons=[reasons],
                policies=["POL-04", "POL-02"] + (["POL-03"] if any(
                    i["final_sale"] for i in items) else []),
                max_amount=_money(amount), requires_evidence=evidence, next_steps=steps)  # fmt: skip


def _warranty(order, rt, item_sku, **_):
    """Warranty claim (POL-08): the warranty length depends on the item category."""
    if order["status"] != "delivered":
        return _out(order, rt, "deny", reasons=["NOT_DELIVERED"], policies=["POL-08"])
    items = _select_items(order, item_sku) or order["items"]
    days = _days_since(order["delivered_date"])
    # Only the first item is checked when several match.
    item = items[0]
    window = config.WARRANTY_DAYS.get(item["category"])
    # No entry for the category means it has no warranty at all.
    if window is None:
        return _out(order, rt, "deny", reasons=["NO_WARRANTY_FOR_CATEGORY"], policies=["POL-08"])
    if days > window:
        return _out(order, rt, "deny", reasons=["WARRANTY_EXPIRED"], policies=["POL-08"],
                    next_steps=["Explain the warranty ended. Repair services are not covered "
                                "by policy; do not promise any."])  # fmt: skip
    return _out(order, rt, "replacement", eligible=True,
                allowed=["replacement", "refund", "store_credit"],
                reasons=["WARRANTY_VALID"], policies=["POL-08", "POL-02"],
                max_amount=_money(item["price"]),
                requires_evidence=["description of the fault"],
                next_steps=["Accidental damage is excluded; escalate if the cause is unclear."])  # fmt: skip


def _lost(order, rt, **_):
    """Package reported lost (POL-05, POL-06). Not lost yet means we treat it as late."""
    # Marked delivered but the customer says it never came: a human must check the carrier.
    if order["status"] == "delivered":
        return _out(order, rt, "escalate", reasons=["DELIVERED_NOT_RECEIVED"],
                    policies=["POL-06"], escalation_reason="other",
                    next_steps=["Carrier proof of delivery must be checked by a human."])  # fmt: skip
    if order["status"] not in ("shipped", "in_transit"):
        return _out(order, rt, "inform", reasons=["NOT_SHIPPED_YET"], policies=["POL-05"])
    # Days past the promised date; a package counts as lost after LOST_AFTER_DAYS.
    late = _days_since(order["estimated_delivery_date"])
    if late >= config.LOST_AFTER_DAYS:
        amount = sum(i["price"] for i in order["items"]) + order["shipping_paid"]
        return _out(order, rt, "replacement", eligible=True,
                    allowed=["replacement", "refund", "store_credit"],
                    reasons=["PACKAGE_LOST"], policies=["POL-06", "POL-02"],
                    max_amount=_money(amount),
                    next_steps=["Apologise. Offer replacement or refund incl. shipping."])  # fmt: skip
    # Not old enough to be lost: treat it as a late delivery.
    return _late(order, rt)


def _late(order, rt, **_):
    """Late delivery (POL-05). `_late` and `_lost` hand over to each other by days late."""
    if order["status"] == "delivered":
        return _out(order, rt, "inform", reasons=["ALREADY_DELIVERED"], policies=["POL-05"])
    if order["status"] not in ("shipped", "in_transit"):
        return _out(order, rt, "inform", reasons=["NOT_SHIPPED_YET"], policies=["POL-05"])
    late = _days_since(order["estimated_delivery_date"])
    # Very late means the lost rules apply; this cannot loop because `_lost` only calls back
    # here when late < LOST_AFTER_DAYS.
    if late >= config.LOST_AFTER_DAYS:
        return _lost(order, rt)
    # A small delay is normal, so just inform.
    if late <= config.LATE_TOLERANCE_DAYS:
        return _out(order, rt, "inform", reasons=["WITHIN_TOLERANCE"], policies=["POL-05"],
                    next_steps=["Share tracking status and ask the customer to wait."])  # fmt: skip
    if order["shipping_paid"] > 0:
        return _out(order, rt, "refund", eligible=True, allowed=["refund", "inform"],
                    reasons=["LATE_OVER_2_DAYS"], policies=["POL-05", "POL-02"],
                    max_amount=_money(order["shipping_paid"]),
                    next_steps=["Apologise and refund the shipping fee only; the package keeps "
                                "moving."])  # fmt: skip
    return _out(order, rt, "inform", reasons=["LATE_NO_SHIPPING_FEE"], policies=["POL-05"],
                next_steps=["Apologise and share tracking status."])  # fmt: skip


def _cancel(order, rt, **_):
    """Cancellation (POL-07): only possible before the order ships."""
    status = order["status"]
    if status == "processing":
        amount = sum(i["price"] for i in order["items"]) + order["shipping_paid"]
        return _out(order, rt, "cancel_order", eligible=True, reasons=["NOT_YET_SHIPPED"],
                    policies=["POL-07"], max_amount=_money(amount),
                    next_steps=["Full refund 5-7 business days after cancellation."])  # fmt: skip
    if status == "cancelled":
        return _out(order, rt, "inform", reasons=["ALREADY_CANCELLED"], policies=["POL-07"])
    reason = "ALREADY_DELIVERED" if status == "delivered" else "ALREADY_SHIPPED"
    return _out(order, rt, "deny", reasons=[reason], policies=["POL-07", "POL-01"],
                next_steps=["Cancellation is refused. Offer a return after delivery (POL-01)."])  # fmt: skip


def _address_change_risk(order) -> list[str]:
    """List the fraud indicators (POL-10) that make an address change need a person.

    Args:
        order: The order dict (status is "processing").

    Returns:
        Reason codes, empty when none apply: the order total is above the agent limit, a
        chargeback is open, or the customer had 3 or more refunds in the last 90 days.
    """
    customer = get_customer(order["customer_id"]) or {}
    total = sum(i["price"] for i in order["items"]) + order["shipping_paid"]
    risk = []
    if total > config.AGENT_APPROVAL_LIMIT:
        risk.append("ABOVE_AGENT_LIMIT")
    if order["chargeback_open"]:
        risk.append("CHARGEBACK_OPEN")
    if customer.get("refunds_last_90_days", 0) >= 3:
        risk.append("THREE_REFUNDS_IN_90_DAYS")
    return risk


def _change_address(order, rt, **_):
    """Address change (POL-07): only possible before the order ships. POL-10 fraud check."""
    if order["status"] == "processing":
        # A changed address is a classic fraud move (parcel redirected to the thief), so
        # high-value orders and accounts with a risk history go to a human (POL-10 item 1).
        risk = _address_change_risk(order)
        if risk:
            return _out(order, rt, "escalate", reasons=["ADDRESS_CHANGE_FRAUD_RISK", *risk],
                        policies=["POL-07", "POL-10"], approval_level="manager",
                        allowed=["escalate"], escalation_reason="fraud",
                        next_steps=["Do not change the address. Escalate with a case summary."])  # fmt: skip
        return _out(order, rt, "update_address", eligible=True, reasons=["NOT_YET_SHIPPED"],
                    policies=["POL-07"],
                    next_steps=["Ask the customer for the complete new address."])  # fmt: skip
    return _out(order, rt, "deny", reasons=["ALREADY_SHIPPED"], policies=["POL-07"],
                next_steps=["The address can no longer be changed after shipment."])  # fmt: skip


def _price_adjustment(order, rt, **_):
    """Price adjustment (POL-09): refund the difference if the shop's own price dropped."""
    age = _days_since(order["order_date"])
    if order["status"] == "cancelled":
        return _out(order, rt, "deny", reasons=["ORDER_CANCELLED"], policies=["POL-09"])
    if age > config.PRICE_ADJUSTMENT_DAYS:
        return _out(order, rt, "deny", reasons=["OUTSIDE_ADJUSTMENT_WINDOW"], policies=["POL-09"])
    # Add up the drop on each item whose current price is lower; final-sale items are excluded.
    diff = sum(
        i["price"] - i["current_price"]
        for i in order["items"]
        if i.get("current_price") is not None
        and i["current_price"] < i["price"]
        and not i["final_sale"]
    )
    if diff <= 0:
        return _out(order, rt, "deny", reasons=["NO_PRICE_DROP"], policies=["POL-09"])
    return _out(order, rt, "refund", eligible=True, reasons=["OWN_PRICE_DROP_WITHIN_14_DAYS"],
                policies=["POL-09", "POL-02"], max_amount=_money(diff),
                next_steps=["Refund the difference to the original payment method."])  # fmt: skip


def _refund_status(order, rt, **_):
    """Where is my refund? (POL-01, POL-07). Overdue refunds go to a human."""
    if not order["refund_date"]:
        return _out(
            order, rt, "inform", reasons=["NO_REFUND_ON_RECORD"], policies=["POL-01", "POL-07"]
        )
    waited = _business_days_since(order["refund_date"])
    # Policy promises the money within 5-7 business days, so more than 7 is overdue.
    if waited > 7:
        return _out(order, rt, "escalate", reasons=["REFUND_OVERDUE"], policies=["POL-07"],
                    escalation_reason="other")  # fmt: skip
    return _out(order, rt, "inform", reasons=["REFUND_WITHIN_TIMELINE"],
                policies=["POL-01", "POL-07"], max_amount=_money(order["refunded_amount"]),
                next_steps=[f"Refund was issued {waited} business day(s) ago. "
                            "Expect it within 5-7 business days."])  # fmt: skip


# Request type -> handler. Three claim types share `_fault_claim`.
HANDLERS = {
    "return": _return,
    "damaged": _fault_claim,
    "defective": _fault_claim,
    "wrong_item": _fault_claim,
    "warranty": _warranty,
    "lost": _lost,
    "late": _late,
    "cancel": _cancel,
    "change_address": _change_address,
    "price_adjustment": _price_adjustment,
    "refund_status": _refund_status,
}


def _apply_always_escalate(order, result: EligibilityResult) -> EligibilityResult:
    """POL-10: chargeback, 3+ refunds in 90 days, and amounts above the agent limit.

    A chargeback is when the customer's bank reverses a payment. These cases always go to a
    human, whatever the handler decided.

    Args:
        order: The order dict.
        result: The handler's result.

    Returns:
        The same result, or a copy changed to "escalate" if a POL-10 trigger applies.
    """
    # Only cases that move money can trigger POL-10.
    if result.request_type not in MONEY_REQUESTS:
        return result
    reasons, code = [], None
    customer = get_customer(order["customer_id"]) or {}
    # --- Trigger 1: an open chargeback ---
    if order["chargeback_open"]:
        reasons.append("CHARGEBACK_OPEN")
        code = "chargeback_open"
    # --- Trigger 2: frequent refunds, a possible abuse signal (3 in 90 days) ---
    if customer.get("refunds_last_90_days", 0) >= 3:
        reasons.append("THREE_REFUNDS_IN_90_DAYS")
        code = code or "refund_frequency"
    # --- Trigger 3: amount over what a front-line agent may approve ---
    if result.eligible and (result.max_amount or 0) > config.AGENT_APPROVAL_LIMIT:
        reasons.append("ABOVE_AGENT_LIMIT")
        code = code or "above_agent_limit"
    if not reasons:
        return result
    # The first trigger found sets the escalation code; the others are still listed as reasons.
    return result.model_copy(
        update={
            "eligible": False,
            "recommendation": "escalate",
            "allowed_recommendations": ["escalate"],
            "reason_codes": result.reason_codes + reasons,
            "policy_ids": sorted(set(result.policy_ids + ["POL-10", "POL-02"])),
            "approval_level": "manager",
            "escalation_reason": code,
            "next_steps": ["Do not approve. Escalate to a human with a case summary."],
        }
    )


def check_eligibility(
    order_id: str,
    request_type: str,
    customer_id: str,
    item_sku: str | None = None,
    error_origin: str | None = None,
) -> ToolOk | ToolError:
    """Decide what Brightcart policy allows for this order and request type.

    Args:
        order_id: Order the customer is asking about.
        request_type: One of RequestType.
        customer_id: The logged-in customer; must own the order.
        item_sku: Which item, if the request is about one item.
        error_origin: "brightcart" or "customer", for personalisation mistakes.

    Returns:
        ToolOk holding an EligibilityResult as a dict, or a ToolError.
    """
    # --- Step 1: test hook, then validate input ---
    if fault := injected_fault("check_eligibility"):
        return fault
    if request_type not in get_args(RequestType):
        return ToolError(code="invalid_input", message=f"Unknown request_type: {request_type}")
    # --- Step 2: the order must exist and belong to this customer ---
    order = check_order_access(order_id, customer_id)
    if isinstance(order, ToolError):
        return order
    # --- Step 3: run the rule for this request type, then the POL-10 override ---
    result = HANDLERS[request_type](
        order, request_type, item_sku=item_sku, error_origin=error_origin
    )
    result = _apply_always_escalate(order, result)
    return ToolOk(data=result.model_dump())
