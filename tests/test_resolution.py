"""Tests for the mock execution (D50): the tool, the graph step and the hand-off payload.

The agent carries out what policy lets a support agent approve. It never executes an
escalation, and every escalation carries a structured hand-off for staff. The LLM is a scripted
fake, so there is no network and every run is predictable.
"""

import json

import pytest

from app import config
from app.agent.graph import Agent
from app.tools import orders
from app.tools.resolution import execute_resolution
from tests.test_agent import FakeLLM, _env, draft, und  # noqa: F401 - _env is an autouse fixture


def state() -> dict:
    """Read the runtime overlay written by the tool (empty when nothing was executed)."""
    return orders.read_state()


def ledger() -> list[dict]:
    """Read the resolution ledger as a list of records."""
    if not config.RESOLUTION_LOG.exists():
        return []
    return [json.loads(x) for x in config.RESOLUTION_LOG.read_text(encoding="utf-8").splitlines()]


# ---- the tool -------------------------------------------------------------------------


def test_executes_an_approved_refund_and_updates_the_order():
    """An approved refund writes one ledger line and raises the order's refunded amount."""
    out = execute_resolution("ORD-1001", "C-001", "return", "refund", 2750.0)
    assert out.ok and out.data["action"] == "refund" and out.data["amount"] == 2750.0
    (line,) = ledger()
    assert line["order_id"] == "ORD-1001" and line["resolution_id"] == out.data["resolution_id"]
    assert orders.get_order("ORD-1001")["refunded_amount"] == 2750.0
    # The cached source record is untouched: only the overlay changed.
    assert orders._orders()["ORD-1001"]["refunded_amount"] == 0.0
    assert orders.get_customer("C-001")["refunds_last_90_days"] == 1


def test_the_same_resolution_cannot_run_twice():
    """A repeat of the same action for the same request is refused, so nobody is paid twice."""
    first = execute_resolution("ORD-1001", "C-001", "return", "refund", 2750.0)
    again = execute_resolution("ORD-1001", "C-001", "return", "refund", 2750.0)
    assert first.ok and not again.ok and again.code == "already_resolved"


def test_another_money_action_for_a_settled_request_is_also_a_repeat():
    """A refund already paid, then a partial refund or store credit asked: "already resolved",
    not "not_allowed" (which used to hand the case to staff as a tool failure)."""
    assert execute_resolution("ORD-1001", "C-001", "return", "refund", 2750.0).ok
    for action in ("refund_partial", "store_credit"):
        again = execute_resolution("ORD-1001", "C-001", "return", action)
        assert again.code == "already_resolved"
    assert len(ledger()) == 1


def test_amount_above_what_the_rules_allow_is_refused():
    """The tool re-runs the rules, so a bigger amount from the model is not executed."""
    out = execute_resolution("ORD-1001", "C-001", "return", "refund", 15000.0)
    assert not out.ok and out.code == "not_allowed" and ledger() == []


def test_escalation_cases_and_other_customers_orders_are_not_executed():
    """Above the agent limit, or someone else's order: nothing is executed."""
    big = execute_resolution("ORD-1021", "C-001", "return", "refund", 3000.0)
    other = execute_resolution("ORD-1001", "C-002", "return", "refund", 300.0)
    assert not big.ok and big.code == "not_allowed"
    assert not other.ok and other.code == "not_found"  # looks like a missing order (D55)
    assert ledger() == [] and state() == {"orders": {}, "customers": {}}


def test_update_address_is_not_an_executable_action():
    """The app never handles an address, so that action is refused by the tool."""
    out = execute_resolution("ORD-1001", "C-001", "change_address", "update_address")
    assert not out.ok and out.code == "not_allowed"


# ---- the graph ------------------------------------------------------------------------


def test_agent_executes_then_a_repeat_is_answered_without_a_second_refund():
    """The first request is executed; the same request again executes nothing and is not escalated."""
    # Second time the rules say "already refunded" (inform), so the scripted model answers that.
    llm = FakeLLM(
        [und("return", "ORD-1001"), und("return", "ORD-1001")],
        lambda c, n: (
            draft("refund", amount=2750.0)
            if c["eligibility_result"]["recommendation"] != "inform"
            else draft("inform", reply="Hi {{first_name}}, this order was already refunded.")
        ),
    )
    agent = Agent(llm)
    first = agent.resolve("return ORD-1001", "C-001")
    again = agent.resolve("return ORD-1001 again", "C-001")
    assert first.execution and not again.execution
    assert again.recommendation == "inform" and not again.escalation
    assert "already refunded" in again.customer_reply_draft
    assert len(ledger()) == 1


def test_a_request_for_a_human_is_escalated_and_never_executed():
    """ "I want a human" escalates even though the return itself is within policy and tools allow it."""
    llm = FakeLLM(
        und("return", "ORD-1001", flags=["human_requested"]), lambda c, n: draft("refund")
    )
    r = Agent(llm).resolve("I want to talk to a human. Return ORD-1001.", "C-001")
    assert r.recommendation == "escalate" and r.escalation.reason_code == "human_requested"
    assert r.execution is None and r.handoff.executed is False
    assert "execute_resolution" not in [t["tool"] for t in r.tool_trace]
    assert ledger() == [] and orders.get_order("ORD-1001")["refunded_amount"] == 0.0


def test_a_bare_human_request_assumes_no_order_item_or_reason():
    """Owner rule (D62): "talk to human staff" after an order chat carries nothing over, and is
    handed off at once, with no question back."""
    llm = FakeLLM(
        [
            und("damaged", "ORD-1001", item_sku="SKU-X", summary="damaged mug"),
            # The model (wrongly) copies the old order and a made-up summary into the human request.
            und("policy_question", "ORD-1001", item_sku="SKU-X", summary="asks about their order"),
        ],
        lambda c, n: draft("need_info", question="Which item?"),
    )
    agent = Agent(llm)
    agent.resolve("My mug arrived damaged, ORD-1001", "C-001", session_id="h1")
    r = agent.resolve("I want to talk to human staff", "C-001", session_id="h1")
    assert r.recommendation == "escalate" and r.escalation.reason_code == "human_requested"
    assert r.clarifying_question is None
    req = r.handoff.request
    assert req.order_id is None and req.item_sku is None and req.intent == "other"
    assert req.summary == "Customer asked to talk to a human; no reason given."
    assert "ORD-1001" not in r.handoff.model_dump_json()


def test_a_human_request_that_names_an_order_keeps_only_that_order():
    """An order the message itself names is kept; the stated reason is quoted, not guessed."""
    llm = FakeLLM(und("return", "ORD-1001"), lambda c, n: draft("refund"))
    r = Agent(llm).resolve("Please let me speak to a person about my refund on ORD-1001", "C-001")
    assert (
        r.escalation.reason_code == "human_requested" and r.handoff.request.order_id == "ORD-1001"
    )
    assert "refund" in r.handoff.request.summary


def test_the_human_request_does_not_wait_for_the_model_to_flag_it():
    """The keyword rule raises human_requested even when the model returned no flags."""
    from app.guardrails.input_rules import HUMAN_WORDS

    llm = FakeLLM(und("other"), lambda c, n: draft("need_info", question="?"))
    r = Agent(llm).resolve("Can I speak to a real person please", "C-001")
    assert r.escalation.reason_code == "human_requested"
    for text in ("I want to talk to human staff", "connect me to an agent", "need a human"):
        assert HUMAN_WORDS.search(text)
    for text in ("the human-sized mannequin", "I want to return the shirt", "Where is my order"):
        assert not HUMAN_WORDS.search(text)


def test_the_peso_sign_is_sent_to_the_model_as_a_character_not_an_escape():
    """The case JSON must hold "₱200.00", not \\u20b1200.00 (its last "1" was read as "1200.00")."""
    llm = FakeLLM(und("return", "ORD-1001"), lambda c, n: draft("refund", amount=2750.0))
    Agent(llm, semantic=False).resolve("Return ORD-1001", "C-001")
    sent = llm.draft_calls[0]
    assert "\\u20b1" not in sent and "₱200.00" in sent


def test_a_draft_with_an_invented_fee_is_repaired_once_with_feedback():
    """A bare "1200.00" (not in the case) is rejected; the second try with the right fee passes."""
    fees = ["a flat return shipping fee of 1200.00", "a flat return shipping fee of ₱200.00"]
    llm = FakeLLM(
        und("return", "ORD-1001"),
        lambda c, n: draft("refund", amount=2750.0, reply=f"Hi {{{{first_name}}}}, {fees[n - 1]}."),
    )
    r = Agent(llm, semantic=False).resolve("Return ORD-1001", "C-001")
    assert len(llm.draft_calls) == 2 and "1200" in llm.draft_calls[1].split("rejected:")[1]
    assert "₱200.00" in r.customer_reply_draft and "1200" not in r.customer_reply_draft


def test_a_policy_escalation_is_not_executed():
    """Above the agent limit is a policy escalation: no execution, and a complete hand-off."""
    llm = FakeLLM(und("return", "ORD-1021", summary="return a chair"), lambda c, n: draft("refund"))
    r = Agent(llm).resolve("Return ORD-1021", "C-001")
    assert r.recommendation == "escalate" and r.execution is None and ledger() == []
    h = r.handoff
    assert h.handoff_id == r.escalation.escalation_id and h.customer.customer_id == "C-001"
    assert h.customer.first_name
    assert h.request.intent == "return" and h.request.order_id == "ORD-1021"
    assert h.reason.reason_code == "above_agent_limit" and h.reason.reason_type == "policy"
    assert {"POL-02", "POL-10"} <= set(h.policies)
    assert h.executed is False and h.actions_taken == [] and h.ts


def test_handoff_has_no_personal_contact_data_and_the_log_has_no_name():
    """The hand-off carries an id and a first name only; the escalation log holds ids, no name."""
    llm = FakeLLM(und("return", "ORD-1021"), lambda c, n: draft("refund"))
    r = Agent(llm).resolve("Return ORD-1021", "C-001")
    dumped = r.handoff.model_dump_json()
    assert "@" not in dumped and "example.test" not in dumped
    log = config.ESCALATION_LOG.read_text(encoding="utf-8")
    first_name = r.handoff.customer.first_name
    assert first_name not in log and "C-001" in log and "ORD-1021" in log


def test_policy_gap_and_system_fallback_are_labelled():
    """A question no policy covers is a policy gap; a failed tool is a system fallback."""
    gap = Agent(
        FakeLLM(und("policy_question"), lambda c, n: draft("escalate", covered=False))
    ).resolve("Do you sell cars?", "C-001")
    assert gap.handoff.reason.reason_type == "policy_gap"
    assert gap.handoff.reason.reason_code == "policy_gap"


def test_execution_failure_becomes_a_system_fallback_escalation(monkeypatch):
    """If the execution tool fails, nothing is executed and the case goes to staff, labelled so."""
    monkeypatch.setenv("FAULT_INJECTION", "execute_resolution:disabled")
    llm = FakeLLM(und("return", "ORD-1001"), lambda c, n: draft("refund", amount=2750.0))
    r = Agent(llm).resolve("return ORD-1001", "C-001")
    assert r.recommendation == "escalate" and r.execution is None
    assert r.handoff.reason.reason_type == "system_fallback" and ledger() == []


@pytest.mark.parametrize("message", ["Do you accept returns?"])
def test_an_executed_answer_is_never_stored_in_the_semantic_cache(message):
    """Only grounded policy answers with no order data are cacheable, never an executed action."""
    from app.cache.semantic import storable

    llm = FakeLLM(und("return", "ORD-1001"), lambda c, n: draft("refund", amount=2750.0))
    r = Agent(llm).resolve(message, "C-001")
    assert r.execution and storable(r) is False


def test_staff_authority_lifts_the_agent_limit_but_not_ownership_or_the_cap():
    """staff_authorized skips the approval-level check; ownership, repeats and the cap still apply."""
    # ORD-1021 is above the ₱5,000 agent limit, so the agent path refuses it.
    assert execute_resolution("ORD-1021", "C-001", "return", "refund").code == "not_allowed"
    over = execute_resolution(
        "ORD-1021", "C-001", "return", "refund", amount=99999, staff_authorized=True
    )
    assert over.code == "not_allowed"  # more than is left to refund on the order
    other = execute_resolution("ORD-1021", "C-002", "return", "refund", staff_authorized=True)
    assert other.code == "not_found"
    ok = execute_resolution("ORD-1021", "C-001", "return", "refund", staff_authorized=True)
    assert ok.ok and ok.data["amount"] > config.AGENT_APPROVAL_LIMIT
    again = execute_resolution("ORD-1021", "C-001", "return", "refund", staff_authorized=True)
    assert again.code == "already_resolved" and len(ledger()) == 1
