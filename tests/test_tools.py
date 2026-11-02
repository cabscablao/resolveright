"""Tests for the agent's tools: order lookup, the eligibility rules engine and escalation.

The tools are plain code (no LLM). The eligibility engine decides what the agent may recommend,
so it must be exactly right. Tools also must hide private data and never change stored data.
"""

import hashlib
import json

import pytest

from app import config
from app.tools.eligibility import check_eligibility
from app.tools.escalation import escalate_to_human
from app.tools.orders import get_order, lookup_order
from app.tools.registry import TOOLS

ORDERS = json.loads((config.DATA_DIR / "orders.json").read_text(encoding="utf-8"))
OWNER = {o["order_id"]: o["customer_id"] for o in ORDERS}


def elig(order_id, request_type, **kw):
    """Run the eligibility check as the order's real owner and return its data.

    Using the real owner avoids the ownership block, so tests focus on the rules.
    """
    res = check_eligibility(order_id, request_type, OWNER[order_id], **kw)
    assert res.ok, res
    return res.data


def test_an_order_already_refunded_in_full_is_an_inform_not_a_refund():
    """ORD-1001 after its 2750 refund (price 2950 less the 200 fee): nothing is left to pay out."""
    from app.tools.resolution import execute_resolution

    assert execute_resolution("ORD-1001", "C-001", "return", "refund", 2750.0).ok
    data = elig("ORD-1001", "return")
    assert data["recommendation"] == "inform" and data["max_amount"] == 0.0
    assert data["reason_codes"] == ["ALREADY_REFUNDED"]


# Each row is one seed order (an "archetype") with its expected outcome:
# order_id, request_type, kwargs, expected recommendation, expected max_amount (None = skip).
ARCHETYPES = [
    ("ORD-1001", "return", {}, "refund", 2750.0),
    ("ORD-1002", "return", {}, "deny", None),
    ("ORD-1003", "return", {}, "refund", 2300.0),
    ("ORD-1004", "return", {}, "deny", None),
    ("ORD-1005", "return", {}, "deny", None),
    ("ORD-1006", "defective", {}, "replacement", 1500.0 + 200.0),
    ("ORD-1007", "damaged", {}, "replacement", 3150.0),
    ("ORD-1008", "damaged", {}, "escalate", None),
    ("ORD-1009", "defective", {}, "escalate", None),
    ("ORD-1010", "lost", {}, "replacement", 2150.0),
    ("ORD-1011", "late", {}, "inform", None),
    ("ORD-1012", "late", {}, "refund", 250.0),
    ("ORD-1013", "cancel", {}, "cancel_order", None),
    ("ORD-1014", "cancel", {}, "deny", None),
    ("ORD-1015", "change_address", {}, "update_address", None),
    ("ORD-1016", "wrong_item", {}, "replacement", 1850.0),
    ("ORD-1017", "warranty", {}, "replacement", 4350.0),
    ("ORD-1018", "warranty", {}, "deny", None),
    ("ORD-1019", "return", {}, "deny", None),
    ("ORD-1020", "price_adjustment", {}, "refund", 1300.0),
    ("ORD-1021", "return", {}, "escalate", None),
    ("ORD-1022", "return", {}, "refund", 1150.0),
    ("ORD-1024", "return", {}, "refund_partial", 1800.0),
    ("ORD-1025", "return", {}, "refund", 2350.0),
    ("ORD-1026", "refund_status", {}, "inform", None),
    ("ORD-1027", "return", {}, "deny", None),
    ("ORD-1028", "wrong_item", {}, "need_info", None),
    ("ORD-1029", "return", {"item_sku": "SKU-FR1"}, "refund_partial", 1650.0),
    ("ORD-1029", "return", {}, "need_info", None),
    ("ORD-1030", "return", {}, "escalate", None),
]


@pytest.mark.parametrize("order_id,rtype,kw,rec,amount", ARCHETYPES)
def test_archetype(order_id, rtype, kw, rec, amount):
    """Each seed order gets the planned recommendation, policy citation and refund cap."""
    d = elig(order_id, rtype, **kw)
    assert d["recommendation"] == rec
    assert rec in d["allowed_recommendations"]
    assert d["policy_ids"], "every result cites at least one policy"
    if amount is not None:
        assert d["max_amount"] == pytest.approx(amount)


def test_every_archetype_order_covered():
    """Every seed order is tested above, so no order is left unchecked by accident."""
    covered = {a[0] for a in ARCHETYPES} | {
        "ORD-1023",
        "ORD-1022",
    }  # 1023 (safety) and 1022 (refund frequency, own test)
    assert covered == set(OWNER)


def test_address_change_is_allowed_on_a_normal_processing_order():
    """A processing order with no fraud indicator may have its address changed (POL-07)."""
    d = elig("ORD-1015", "change_address")
    assert d["recommendation"] == "update_address" and d["approval_level"] == "agent"


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"items": [{"price": 12000.0}]}, "ABOVE_AGENT_LIMIT"),
        ({"chargeback_open": True}, "CHARGEBACK_OPEN"),
    ],
)
def test_address_change_with_fraud_indicator_escalates(monkeypatch, change, reason):
    """An address change on a risky order goes to a human instead of being approved (POL-10 item 10)."""
    from app.tools import eligibility

    base = dict(get_order("ORD-1015"))
    base["items"] = [{**base["items"][0], **change.pop("items", [{}])[0]}]
    order = {**base, **change}
    monkeypatch.setattr(eligibility, "check_order_access", lambda oid, cid: order)
    res = check_eligibility("ORD-1015", "change_address", "C-002")
    assert res.ok
    d = res.data
    assert d["recommendation"] == "escalate" and d["escalation_reason"] == "fraud"
    assert "ADDRESS_CHANGE_FRAUD_RISK" in d["reason_codes"] and reason in d["reason_codes"]
    assert d["approval_level"] == "manager" and "POL-10" in d["policy_ids"]


def test_address_change_with_three_recent_refunds_escalates(monkeypatch):
    """Three refunds in 90 days on the account is a fraud indicator for an address change."""
    from app.tools import eligibility

    monkeypatch.setattr(
        eligibility, "get_customer", lambda cid: {"refunds_last_90_days": 3, "first_name": "X"}
    )
    d = elig("ORD-1015", "change_address")
    assert d["recommendation"] == "escalate" and "THREE_REFUNDS_IN_90_DAYS" in d["reason_codes"]


def test_three_recent_refunds_escalate_a_return(monkeypatch):
    """POL-10: 3 refunds in 90 days send a return to a human (D57: tested with a patched customer,
    because both seed customers have 0 refunds)."""
    from app.tools import eligibility

    monkeypatch.setattr(
        eligibility, "get_customer", lambda cid: {"refunds_last_90_days": 3, "first_name": "X"}
    )
    d = elig("ORD-1022", "return")
    assert d["recommendation"] == "escalate" and d["escalation_reason"] == "refund_frequency"


def test_address_change_after_shipment_is_still_denied():
    """After shipment the address can no longer change: a plain deny, not an escalation (POL-07)."""
    assert elig("ORD-1014", "change_address")["recommendation"] == "deny"


def test_escalation_reasons():
    """Risky cases are escalated with the right reason, so the right human team sees them."""
    assert elig("ORD-1008", "damaged")["escalation_reason"] == "late_damage_claim"
    assert elig("ORD-1030", "return")["escalation_reason"] == "chargeback_open"
    d = elig("ORD-1021", "return")
    assert d["escalation_reason"] == "above_agent_limit" and d["approval_level"] == "manager"
    assert "ABOVE_AGENT_LIMIT" in elig("ORD-1009", "defective")["reason_codes"]


def test_boundary_days():
    """Day 29 of the 30-day return window is allowed and day 31 is not (edge-of-window check)."""
    assert elig("ORD-1003", "return")["eligible"] is True  # Day 29 is inside the window.
    assert elig("ORD-1004", "return")["eligible"] is False  # Day 31 is outside it.
    assert "OUTSIDE_RETURN_WINDOW" in elig("ORD-1004", "return")["reason_codes"]


def test_evidence_and_free_label_rules():
    """Damaged items ask for photos, and no return fee is mentioned when the label is free."""
    d = elig("ORD-1007", "damaged")
    assert d["requires_evidence"] and "photos" in d["requires_evidence"][0]
    assert not any("return-shipping fee is deducted" in s for s in d["next_steps"])


def test_return_fee_depends_on_delivery_region():
    """Change-of-mind returns pay a ₱200 fee within Metro Manila and ₱500 outside it (POL-01)."""
    metro = elig("ORD-1001", "return")  # Metro Manila: 2,950 - 200
    assert metro["max_amount"] == 2750.0 and "OUTSIDE_METRO_MANILA_FEE" not in metro["reason_codes"]
    assert any("₱200.00" in s for s in metro["next_steps"])
    province = elig("ORD-1025", "return")  # Bulacan: 2,850 - 500
    assert (
        province["max_amount"] == 2350.0 and "OUTSIDE_METRO_MANILA_FEE" in province["reason_codes"]
    )
    assert any("₱500.00" in s for s in province["next_steps"])


def test_final_sale_defect_still_covered():
    """A defective final-sale item is still covered, because a defect is not a change of mind."""
    assert "POL-03" in elig("ORD-1006", "defective")["policy_ids"]


def test_lost_vs_late_boundary():
    """A parcel counts as lost only well after its estimate. Before that, the agent just informs."""
    assert elig("ORD-1010", "lost")["eligible"] is True  # 9 days past the estimate.
    d = elig("ORD-1011", "lost")  # Only 1 day late, so not lost yet.
    assert d["recommendation"] == "inform" and d["eligible"] is False


# ---- error handling ----------------------------------------------------------------


def test_lookup_hides_personal_data():
    """Order lookup returns no email, name, customer id or address.

    The LLM sees this output, and personal data must not reach it.
    """
    res = lookup_order("ORD-1001", "C-001")
    assert res.ok
    text = json.dumps(res.data).lower()
    for banned in ("email", "@example", "first_name", "customer_id", "address"):
        assert banned not in text


def test_lookup_errors():
    """Bad or unknown order ids give clear error codes instead of crashing."""
    assert lookup_order("ORD-9999", "C-001").code == "not_found"
    assert lookup_order("ord 12", "C-001").code == "invalid_input"
    assert lookup_order("", "C-001").code == "invalid_input"
    assert lookup_order("ord-1001", "C-001").ok  # Lower case is accepted.


def test_someone_elses_order_looks_like_a_missing_order():
    """Another customer's order returns "not found", the same as an id that does not exist (D55)."""
    res = lookup_order("ORD-1029", "C-001")
    missing = lookup_order("ORD-9999", "C-001")
    assert res.code == missing.code == "not_found"
    assert "Table lamp" not in res.message and "C-002" not in res.message
    assert res.message == missing.message.replace("ORD-9999", "ORD-1029")
    assert check_eligibility("ORD-1029", "return", "C-001").code == "not_found"


def test_unknown_request_type():
    """An unsupported request type is rejected as invalid input."""
    assert check_eligibility("ORD-1001", "teleport", "C-001").code == "invalid_input"


@pytest.mark.parametrize("mode,code", [("timeout", "timeout"), ("disabled", "disabled")])
def test_fault_injection(monkeypatch, mode, code):
    """The FAULT_INJECTION switch breaks one tool on purpose, and only that tool.

    We use it to test how the agent behaves when a tool fails.
    """
    monkeypatch.setenv("FAULT_INJECTION", f"lookup_order:{mode}")
    assert lookup_order("ORD-1001", "C-001").code == code
    assert check_eligibility("ORD-1001", "return", "C-001").ok  # Other tools are unaffected.
    monkeypatch.setenv("FAULT_INJECTION", "")
    assert lookup_order("ORD-1001", "C-001").ok


# ---- escalation tool ---------------------------------------------------------------


def test_escalate_writes_local_record(tmp_path, monkeypatch):
    """Escalating saves a record with an ID, so a human can pick the case up later."""
    # Write to a temp file so tests do not touch the real escalation log.
    monkeypatch.setattr(config, "ESCALATION_LOG", tmp_path / "esc.jsonl")
    res = escalate_to_human("Power bank overheating on ORD-1023", "safety_hazard", "urgent")
    assert res.ok and res.data["escalation_id"].startswith("ESC-")
    lines = (tmp_path / "esc.jsonl").read_text().strip().splitlines()
    assert json.loads(lines[0])["urgency"] == "urgent"


def test_escalate_validation(tmp_path, monkeypatch):
    """Bad escalation input is rejected and nothing is written."""
    monkeypatch.setattr(config, "ESCALATION_LOG", tmp_path / "esc.jsonl")
    assert escalate_to_human("x", "made_up_reason").code == "invalid_input"
    assert escalate_to_human("x", "fraud", "whenever").code == "invalid_input"
    assert escalate_to_human("   ", "fraud").code == "invalid_input"
    assert not (tmp_path / "esc.jsonl").exists()


# ---- source data invariant ---------------------------------------------------------


def _hash_data():
    """Fingerprint every seed data file, so we can tell if any file changed."""
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in config.DATA_DIR.glob("*.json")
    }


def test_tools_never_change_data(tmp_path, monkeypatch):
    """Running the read tools leaves the data files untouched.

    Even the execution tool never edits data/: an executed change goes to a runtime overlay.
    """
    monkeypatch.setattr(config, "ESCALATION_LOG", tmp_path / "esc.jsonl")
    before = _hash_data()
    for oid, rtype, kw, *_ in ARCHETYPES:
        check_eligibility(oid, rtype, OWNER[oid], **kw)
        lookup_order(oid, OWNER[oid])
    assert _hash_data() == before
    assert get_order("ORD-1001")["status"] == "delivered"


def test_registry_has_the_five_tools():
    """The agent has five tools: three read tools, the mock execution tool and the escalation."""
    assert set(TOOLS) == {
        "search_policy",
        "lookup_order",
        "check_eligibility",
        "execute_resolution",
        "escalate_to_human",
    }
