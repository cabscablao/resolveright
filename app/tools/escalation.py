"""Mock escalation. Only writes a local record; it never contacts anyone.

An escalation happens only for a reason in the policies (POL-10: fraud, legal, safety, harassment,
chargeback, repeated refunds, a request for a human, amounts above the agent limit, late damage
claims, address-change fraud risk) or when no policy covers the case (`policy_gap`). Two codes
are a system fallback, not a policy reason: `tool_failure` and `low_confidence`.

Where it sits: the agent calls `escalate_to_human` when a case needs a person (fraud, safety,
low confidence...). Here that just appends one JSON line to a log file, standing in for a real
ticketing system.
"""

import uuid
from datetime import UTC, datetime
from typing import Literal, get_args

from pydantic import BaseModel

from app import config
from app.observability.logging import log_event
from app.tools.results import ToolError, ToolOk

# Allowed reasons for escalating; a fixed list keeps reports easy to group.
ReasonCode = Literal[
    "fraud",
    "legal_threat",
    "safety_hazard",
    "harassment",
    "chargeback_open",
    "refund_frequency",
    "human_requested",
    "above_agent_limit",
    "late_damage_claim",
    "policy_gap",
    "tool_failure",
    "low_confidence",
    "other",
]
Urgency = Literal["normal", "urgent"]


class EscalationRecord(BaseModel):
    """One line in the escalation log."""

    escalation_id: str
    ts: str  # UTC time, ISO 8601 text
    reason_code: ReasonCode
    urgency: Urgency
    case_summary: str
    # Ids only, so staff can find the case. Names and other personal data never go in the log.
    customer_id: str | None = None
    order_id: str | None = None
    policy_ids: list[str] = []


def escalate_to_human(
    case_summary: str,
    reason_code: str,
    urgency: str = "normal",
    customer_id: str | None = None,
    order_id: str | None = None,
    policy_ids: list[str] | None = None,
):
    """Record an escalation. `case_summary` must already be PII-masked by the caller.

    PII means personal data such as names, emails and addresses.

    Args:
        case_summary: Short, masked description of the case.
        reason_code: One of the ReasonCode values.
        urgency: "normal" or "urgent".
        customer_id: Id of the customer (an id, not a name), if known.
        order_id: Id of the order the case is about, if any.
        policy_ids: Policies that triggered or apply, for example ["POL-02", "POL-10"].

    Returns:
        ToolOk with the saved record, or ToolError(invalid_input) for bad arguments.
    """
    # Validate by hand because the LLM side may pass any string.
    if reason_code not in get_args(ReasonCode) or urgency not in get_args(Urgency):
        return ToolError(code="invalid_input", message="Unknown reason_code or urgency.")
    if not case_summary or not case_summary.strip():
        return ToolError(code="invalid_input", message="case_summary is required.")
    record = EscalationRecord(
        escalation_id=f"ESC-{uuid.uuid4().hex[:8].upper()}",  # 8 hex chars: short but unique
        ts=datetime.now(UTC).isoformat(),
        reason_code=reason_code,
        urgency=urgency,
        case_summary=case_summary.strip()[:500],  # 500 chars: keeps log lines small
        customer_id=customer_id,
        order_id=order_id,
        policy_ids=policy_ids or [],
    )
    config.ESCALATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    # Append mode: one JSON object per line ("JSON Lines"), easy to read and to grep.
    with config.ESCALATION_LOG.open("a", encoding="utf-8") as f:
        f.write(record.model_dump_json() + "\n")
    log_event("escalation_recorded", status=f"{reason_code}:{urgency}")
    return ToolOk(data=record.model_dump())
