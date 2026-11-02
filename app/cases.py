"""Case records shared by the customer, staff and developer pages (D51).

Where it sits: every customer message becomes a case with one `status` field, kept in a small JSON
file (config.CASES_FILE) that the three pages read. The customer page shows the status inline in the chat, in an open-requests line and in My requests;
the staff page lists the escalated cases and lets a staff member finish them; the developer page
reads the saved debug block.

Status flow: `processing` -> `completed` (resolved by the chatbot) or `escalated` -> `in_review`
-> `completed` (resolved by staff). The file is written whole through an atomic swap, under one
lock, so a page never reads a half-written file. This is safe for ONE server process only (the same
limit as the Qdrant folder); several workers would need a file lock.

The case record keeps the customer's message with personal data masked, and no email, address or
phone number. The first name appears only through the saved hand-off payload.
"""

import json
import os
import threading
import time
import uuid
from datetime import UTC, datetime

from app import config
from app.agent.render import render_reply
from app.agent.schemas import ResolutionResult
from app.followup import (
    ADDED_INFO,
    NORMAL,
    OPEN_STATUSES,
    STATUS_CHECK,
    UNRELATED,
    Route,
    reply_for,
    route_message,
)
from app.guardrails.pii import mask_pii
from app.observability.logging import log_event
from app.tools.eligibility import check_eligibility
from app.tools.orders import get_customer, get_order
from app.tools.resolution import EXECUTABLE_ACTIONS, execute_resolution
from app.tools.results import ToolError

STATUSES = ("processing", "escalated", "in_review", "completed")
DECISIONS = ("approve", "modify", "reject")
# What a request type would be resolved with when a staff member approves it: the action that
# fits the intent. Intents that are not here (safety, fraud, questions...) have no default action.
INTENT_ACTIONS = {
    "return": "refund", "damaged": "refund", "defective": "refund", "wrong_item": "refund",
    "late": "refund", "lost": "refund", "warranty": "refund", "price_adjustment": "refund",
    "cancel": "cancel_order",
}  # fmt: skip
# What the customer is told once staff carried out an action ({amount} is money).
STAFF_SENTENCES = {
    "refund": "A refund of {amount} has been processed.",
    "refund_partial": "A partial refund of {amount} has been processed.",
    "replacement": "A replacement has been arranged (value {amount}).",
    "store_credit": "Store credit of {amount} has been added to your account.",
    "cancel_order": "Your order has been cancelled and {amount} will be refunded.",
}

# RLock: staff_decide holds it while it calls helpers that take it again.
_LOCK = threading.RLock()


class CaseError(Exception):
    """A staff decision cannot be applied (unknown case, already completed, bad input)."""


def _now() -> str:
    """Current UTC time as an ISO string without microseconds."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_case_id() -> str:
    """Make a case id like CASE-1A2B3C4D (also the checkpoint id of the agent run)."""
    return f"CASE-{uuid.uuid4().hex[:8].upper()}"


# ---- the shared file ---------------------------------------------------------------------


def _read() -> dict:
    """Load all cases; a missing or unreadable file means no cases yet."""
    try:
        return json.loads(config.CASES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(cases: dict) -> None:
    """Save all cases through a temp file and an atomic swap (never a half-written file)."""
    config.CASES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.CASES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cases, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, config.CASES_FILE)


def reset_store() -> int:
    """Delete the case file (and a leftover temp file), so the staff page starts empty (D53).

    Called once when the server starts. The order overlay and the resolution ledger are not
    touched. A missing file is fine.

    Returns:
        How many cases the file held before it was deleted (0 if it was missing or unreadable).
    """
    with _LOCK:
        removed = len(_read())
        config.CASES_FILE.unlink(missing_ok=True)
        config.CASES_FILE.with_suffix(".tmp").unlink(missing_ok=True)
    log_event("cases_reset", removed=removed)
    return removed


def get_case(case_id: str | None) -> dict | None:
    """Return one case record, or None if the id is empty or unknown."""
    if not case_id:
        return None
    with _LOCK:
        return _read().get(case_id)


def list_cases(statuses: tuple[str, ...] | None = None) -> list[dict]:
    """Return cases, newest first, optionally only those in the given statuses."""
    with _LOCK:
        cases = list(_read().values())
    if statuses:
        cases = [c for c in cases if c["status"] in statuses]
    return sorted(cases, key=lambda c: c["created_at"], reverse=True)


def save_case(case: dict) -> None:
    """Insert or replace one case record (the update time is set here)."""
    with _LOCK:
        cases = _read()
        case["updated_at"] = _now()
        cases[case["case_id"]] = case
        _write(cases)


def update_case(case_id: str, **fields) -> dict:
    """Change fields of one case and return it.

    Raises:
        CaseError: If the case does not exist.
    """
    with _LOCK:
        cases = _read()
        if case_id not in cases:
            raise CaseError(f"Unknown case {case_id}.")
        cases[case_id].update(fields, updated_at=_now())
        _write(cases)
        return cases[case_id]


def discard_case(case_id: str) -> None:
    """Remove a case that never produced an answer (for example a rate-limited request)."""
    with _LOCK:
        cases = _read()
        if cases.pop(case_id, None) is not None:
            _write(cases)


# ---- from message to case ----------------------------------------------------------------


def _new_record(case_id: str, customer_id: str, message: str) -> dict:
    """The record written the moment a message arrives (status `processing`)."""
    masked, _ = mask_pii(message)
    now = _now()
    return {
        "case_id": case_id, "customer_id": customer_id, "order_id": None,
        "status": "processing", "resolved_by": None, "escalation_reason": None,
        "reason_type": None, "chatbot_recommendation": None, "handoff_summary": None,
        "transcript": [{"role": "user", "content": masked}], "customer_reply": None,
        "outcome_summary": None, "staff_decision": None, "staff_note": None,
        "staff_reply": None, "staff_reply_delivered": False, "execution": None,
        "debug": {}, "created_at": now, "updated_at": now, "completed_at": None,
        # Set when the customer writes again while staff hold the case (D58). Staff clear it by
        # opening or reloading the case. `priority` is "normal" until urgent follow-ups exist.
        "priority": "normal", "has_new_customer_message": False,
    }  # fmt: skip


def _order_id_of(result: ResolutionResult) -> str | None:
    """The order the case is about: from the hand-off, else from the first tool call."""
    if result.handoff and result.handoff.request.order_id:
        return result.handoff.request.order_id
    for call in result.tool_trace:
        if call.get("args", {}).get("order_id"):
            return call["args"]["order_id"]
    return None


def suggest_action(
    customer_id: str, order_id: str | None, intent: str, item_sku=None
) -> dict | None:
    """What a staff member would most likely approve for an escalated case.

    Re-runs the rules to get the refundable amount (the escalation replaced the recommendation, but
    the amount stays). Returns None when the intent has no default action or no money is involved.

    Args:
        customer_id: The case's customer.
        order_id: The order, if the case has one.
        intent: The customer's intent (used as the request type).
        item_sku: The item, if the request was about one item.

    Returns:
        {"action": ..., "amount": ...} or None.
    """
    action = INTENT_ACTIONS.get(intent)
    if not action or not order_id:
        return None
    res = check_eligibility(order_id, intent, customer_id, item_sku)
    if isinstance(res, ToolError) or not res.data.get("max_amount"):
        return None
    return {"action": action, "amount": res.data["max_amount"]}


def _finish(case: dict, result: ResolutionResult, started: float) -> dict:
    """Fill the case from the agent's result and set the final status."""
    customer_id = case["customer_id"]
    reply = render_reply(result.customer_reply_draft, customer_id)
    escalated = result.recommendation == "escalate"
    order_id = _order_id_of(result)
    rec = {"action": result.recommendation, "amount": result.amount}
    if escalated:
        h = result.handoff
        suggestion = suggest_action(
            customer_id, order_id, h.request.intent if h else result.intent,
            h.request.item_sku if h else None,
        )  # fmt: skip
        if suggestion:
            rec["suggested_action"], rec["suggested_amount"] = (
                suggestion["action"], suggestion["amount"],
            )  # fmt: skip
    case.update(
        order_id=order_id,
        status="escalated" if escalated else "completed",
        resolved_by=None if escalated else "chatbot",
        escalation_reason=result.escalation.reason_code if result.escalation else None,
        reason_type=result.handoff.reason.reason_type if result.handoff else None,
        chatbot_recommendation=rec,
        handoff_summary=result.handoff.request.summary if result.handoff else None,
        customer_reply=reply,
        outcome_summary=_outcome(result),
        completed_at=None if escalated else _now(),
    )  # fmt: skip
    case["transcript"].append({"role": "assistant", "content": reply})
    case["debug"] = {
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "repaired": result.guardrail_status == "repaired",
        "result": result.model_dump(mode="json"),
    }
    return case


def _outcome(result: ResolutionResult) -> str:
    """One line under the reply on the customer page (empty when there is nothing to add)."""
    if result.recommendation == "escalate":
        return "Passed to a team member. You will hear back here."
    if result.execution:
        e = result.execution
        what = {"refund": "Refund", "refund_partial": "Partial refund", "store_credit": "Store credit",
                "replacement": "Replacement", "cancel_order": "Cancellation"}[e.action]  # fmt: skip
        return f"{what} of {config.peso(e.amount)} recorded. Reference {e.resolution_id}."
    return ""


def process_message(
    agent,
    message: str,
    customer_id: str,
    session_id: str | None = None,
    routing: dict | None = None,
):
    """Make a case, run the agent on the message and save the outcome in the case.

    Args:
        agent: The Agent to run.
        message: What the customer typed.
        customer_id: The logged-in customer.
        session_id: Conversation id for memory.
        routing: The open-case routing decision, saved in the case's debug block (D58).

    Returns:
        The agent's ResolutionResult with `case_id` set to this case. When the agent escalated an
        order that already has an open case, `case_id` is that open case instead (D59).

    Raises:
        LimitExceeded: If a rate or budget limit is hit (the case is discarded).
    """
    case_id = new_case_id()
    save_case(_new_record(case_id, customer_id, message))
    started = time.perf_counter()
    try:
        result = agent.resolve(message, customer_id, session_id=session_id, case_id=case_id)
    except Exception:  # includes LimitExceeded: no answer, so no case
        discard_case(case_id)
        raise
    result = result.model_copy(update={"case_id": case_id})
    with _LOCK:
        case = _finish(get_case(case_id), result, started)
        case["debug"]["routing"] = routing or {"open_case_found": False, "route": "normal"}
        held = _open_case_for(customer_id, case["order_id"], exclude=case_id)
        if result.recommendation == "escalate" and held:
            return _merge_into(case, held, result)
        save_case(case)
    log_event(
        "case_saved", case_id=case_id,
        status="escalated" if result.recommendation == "escalate" else "completed",
    )  # fmt: skip
    return result


# ---- follow-ups on a case that waits for staff (Scenarios 3 and 4, D58 and D59) -----------


def open_cases_of(customer_id: str) -> list[dict]:
    """The customer's cases that wait for staff (escalated or in review), newest first."""
    return [c for c in list_cases(OPEN_STATUSES) if c["customer_id"] == customer_id]


def _open_case_for(customer_id: str, order_id: str | None, exclude: str) -> dict | None:
    """The open case of this customer for this order, other than `exclude` (one per order)."""
    if not order_id:
        return None
    for c in open_cases_of(customer_id):
        if c["order_id"] == order_id and c["case_id"] != exclude:
            return c
    return None


def _merge_into(case: dict, held: dict, result: ResolutionResult) -> ResolutionResult:
    """One open escalation per customer and order: do not leave a second open case (D59).

    The fresh record is closed as a chatbot-completed duplicate that points at the open case, the
    customer's message is added to the open case, and the customer gets the status reply.

    Args:
        case: The new, already filled record (it escalated).
        held: The open case of the same customer and order.
        result: The agent's result for the new message.

    Returns:
        The result with the open case's id and the status reply as the customer's reply.
    """
    route = Route(STATUS_CHECK, held, True)
    reply = reply_for(route)
    case.update(
        status="completed", resolved_by="chatbot", customer_reply=render_reply(reply, case["customer_id"]),
        outcome_summary="Already with our team under " + held["case_id"] + ".",
        merged_into=held["case_id"], completed_at=_now(),
    )  # fmt: skip
    save_case(case)
    with _LOCK:
        fresh = get_case(held["case_id"])
        update_case(
            held["case_id"],
            has_new_customer_message=True,
            transcript=fresh["transcript"] + [case["transcript"][0]],
        )
    log_event("case_merged", case_id=case["case_id"], status=held["case_id"])
    return result.model_copy(update={"case_id": held["case_id"], "customer_reply_draft": reply})


def _item_names(case: dict) -> str:
    """Product names of the case's order, so a message naming the item is seen as related."""
    order = get_order(case["order_id"]) if case.get("order_id") else None
    return " ".join(i["name"] for i in (order or {}).get("items", []))


def handle_message(agent, message: str, customer_id: str, session_id: str | None = None):
    """Route a customer message, then answer it (the entry point of the customer page).

    With no open case this is `process_message`. With one, the message is first checked against it
    (followup.route_message): follow-ups are answered from fixed templates without the agent, and
    anything else goes through the normal pipeline with the open case untouched.

    Args:
        agent: The Agent to run for messages that are not follow-ups.
        message: What the customer typed.
        customer_id: The logged-in customer.
        session_id: Conversation id for memory.

    Returns:
        A ResolutionResult. `case_id` is the held case for a follow-up, else the new case.

    Raises:
        LimitExceeded: As for process_message.
    """
    from app.agent.graph import _local_result
    from app.agent.validation import validate_message
    from app.guardrails.input_rules import SAFETY_WORDS, check_input

    held = open_cases_of(customer_id)
    # A bad, unsafe or attack-like message never takes the shortcut: the full pipeline decides.
    if (
        validate_message(message)
        or SAFETY_WORDS.search(message or "")
        or check_input(message).action != "allow"
    ):
        route = Route(UNRELATED, held[0], True) if held else Route(NORMAL)
    else:
        route = route_message(message, held, _item_names(held[0]) if held else "")
    routing = {
        "open_case_found": route.open_case_found, "route": route.kind,
        "case_id": route.case["case_id"] if route.case else None,
    }  # fmt: skip
    log_event("message_routed", status=route.kind, case_id=routing["case_id"])
    if route.runs_agent:
        return process_message(agent, message, customer_id, session_id, routing=routing)
    with _LOCK:
        case = get_case(route.case["case_id"])
        if not case or case["status"] not in OPEN_STATUSES:  # staff finished it meanwhile
            return process_message(agent, message, customer_id, session_id, routing=routing)
        reply = reply_for(Route(route.kind, case, True))
        if route.kind == ADDED_INFO:
            masked, _ = mask_pii(message)
            debug = {**case.get("debug", {}), "routing": routing}
            update_case(
                case["case_id"], has_new_customer_message=True, debug=debug,
                transcript=case["transcript"] + [{"role": "user", "content": masked}],
            )  # fmt: skip
        # status_check, repeat and ambiguous leave the case exactly as it was.
    result = _local_result("need_info", reply, "followup", clarifying_question=reply)
    return result.model_copy(update={"case_id": case["case_id"]})


# ---- staff ------------------------------------------------------------------------------


def open_case(case_id: str) -> dict:
    """A staff member opens an escalated case: `escalated` becomes `in_review`.

    Raises:
        CaseError: If the case does not exist.
    """
    with _LOCK:
        case = get_case(case_id)
        if case is None:
            raise CaseError(f"Unknown case {case_id}.")
        if case["status"] == "escalated":
            case = update_case(case_id, status="in_review")
        return clear_new_message(case_id) or case


def clear_new_message(case_id: str) -> dict | None:
    """Clear the "New message" marker once staff have seen the case (open or Reload case).

    Returns:
        The case record (None if unknown). Only an open case is changed.
    """
    with _LOCK:
        case = get_case(case_id)
        if case and case.get("has_new_customer_message") and case["status"] in OPEN_STATUSES:
            return update_case(case_id, has_new_customer_message=False)
        return case


def _request_of(case: dict) -> dict:
    """The request details the staff action needs, read from the saved hand-off."""
    handoff = (case.get("debug", {}).get("result") or {}).get("handoff") or {}
    request = handoff.get("request") or {}
    return {
        "intent": request.get("intent") or "other",
        "item_sku": request.get("item_sku"),
        "order_id": case.get("order_id"),
    }


def staff_decide(
    case_id: str,
    decision: str,
    reply: str = "",
    note: str = "",
    action: str | None = None,
    amount: float | None = None,
    reviewer: str = "staff",
) -> dict:
    """Apply a staff member's decision to an escalated case and complete it.

    approve: carry out the chatbot's suggested action (if it has one) with staff authority.
    modify: carry out the action and amount the staff member chose (action "none" = reply only).
    reject: execute nothing; the reply is required.
    Whatever is executed is a mock (ledger line and runtime order change), and a repeat of an
    already executed request is refused, so a double click cannot pay twice.

    Args:
        case_id: The case to decide.
        decision: "approve", "modify" or "reject".
        reply: Message to the customer (required for modify and reject).
        note: Internal note for the record.
        action: For modify: one of EXECUTABLE_ACTIONS, or "none".
        amount: For modify: money to refund or credit.
        reviewer: Who decided, for the audit line.

    Returns:
        The completed case record.

    Raises:
        CaseError: If the case is unknown or not open, the input is invalid, or the execution is
            refused (the case then stays open).
    """
    from app.agent.graph import _audit  # same audit file as the review gate

    if decision not in DECISIONS:
        raise CaseError(f"decision must be one of {DECISIONS}")
    with _LOCK:
        case = get_case(case_id)
        if case is None:
            raise CaseError(f"Unknown case {case_id}.")
        if case["status"] not in ("escalated", "in_review"):
            raise CaseError("This case is already completed.")
        if decision in ("modify", "reject") and not reply.strip():
            raise CaseError(f"{decision} needs a reply to the customer.")
        rec = case.get("chatbot_recommendation") or {}
        if decision == "approve":
            action, amount = rec.get("suggested_action"), rec.get("suggested_amount")
        elif decision == "reject" or action in (None, "", "none"):
            action = None
        elif action not in EXECUTABLE_ACTIONS:
            raise CaseError(f"action must be one of {EXECUTABLE_ACTIONS} or none")
        executed = None
        if action:
            req = _request_of(case)
            if not req["order_id"]:
                raise CaseError("This case has no order, so nothing can be executed.")
            out = execute_resolution(
                req["order_id"], case["customer_id"], req["intent"], action,
                amount=amount, item_sku=req["item_sku"], staff_authorized=True,
            )  # fmt: skip
            if isinstance(out, ToolError):
                raise CaseError(f"Not executed: {out.message}")
            executed = out.data
        name = (get_customer(case["customer_id"]) or {}).get("first_name") or "there"
        text = reply.strip() or f"Hi {name}, a member of our team has reviewed your request."
        if executed:
            sentence = STAFF_SENTENCES[executed["action"]].format(
                amount=config.peso(executed["amount"])
            )
            text = f"{text}\n\n{sentence} Reference: {executed['resolution_id']}."
        done = update_case(
            case_id, status="completed", resolved_by="staff", staff_decision=decision,
            staff_note=note.strip(), staff_reply=text, staff_reply_delivered=False,
            execution=executed, completed_at=_now(),
        )  # fmt: skip
        _audit({
            "ts": _now(), "case_id": case_id, "action": decision, "reviewer": reviewer,
            "recommendation": rec.get("action"), "amount": executed["amount"] if executed else None,
            "intent": _request_of(case)["intent"], "edited_reply_chars": len(reply.strip()),
            "resolution_id": executed["resolution_id"] if executed else None,
            "note": "Staff decision on an escalated case.",
        })  # fmt: skip
        log_event("staff_decided", case_id=case_id, status=decision)
        return done


def deliver_staff_reply(case_id: str) -> str | None:
    """Return the staff reply once, marking it as delivered to the customer's chat.

    Returns:
        The reply text the first time it is asked for after staff completed the case, else None.
    """
    with _LOCK:
        case = get_case(case_id)
        if not case or case["resolved_by"] != "staff" or case["staff_reply_delivered"]:
            return None
        update_case(case_id, staff_reply_delivered=True)
        return case["staff_reply"]
