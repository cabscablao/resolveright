"""Tests for the case records and the staff flow (D51): status, hand-off, staff decision, pages.

The agent uses the scripted fake LLM from test_agent, so there is no network. conftest.py gives
every test its own empty case file, ledger and order overlay.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import cases, config
from app.agent.graph import Agent
from app.agent.limits import LimitExceeded, RequestLimiter
from app.api import main
from app.tools import orders
from tests.test_agent import FakeLLM, _env, draft, und  # noqa: F401 - _env is an autouse fixture
from tests.test_resolution import ledger


def refund_agent(order="ORD-1001"):
    """An agent whose fake LLM always understands a return of `order` and drafts a refund."""
    return Agent(FakeLLM(und("return", order), lambda c, n: draft("refund")))


def escalated_case():
    """Run the above-the-limit return (ORD-1021, C-001) and return its case record."""
    result = cases.process_message(refund_agent("ORD-1021"), "Return ORD-1021", "C-001")
    return cases.get_case(result.case_id)


# ---- the shared file ---------------------------------------------------------------------


def test_store_round_trip_filter_and_discard():
    """Cases are saved, listed newest first, filtered by status, and can be discarded."""
    a = cases._new_record("CASE-A", "C-001", "hello")
    b = cases._new_record("CASE-B", "C-002", "hi")
    b["status"], b["created_at"] = "escalated", "2999-01-01T00:00:00+00:00"
    cases.save_case(a)
    cases.save_case(b)
    assert [c["case_id"] for c in cases.list_cases()] == ["CASE-B", "CASE-A"]
    assert [c["case_id"] for c in cases.list_cases(("escalated",))] == ["CASE-B"]
    cases.update_case("CASE-A", status="completed")
    assert cases.get_case("CASE-A")["status"] == "completed"
    cases.discard_case("CASE-A")
    assert cases.get_case("CASE-A") is None and cases.get_case(None) is None
    assert not config.CASES_FILE.with_suffix(".tmp").exists()  # the atomic swap leaves no temp file
    with pytest.raises(cases.CaseError):
        cases.update_case("CASE-NOPE", status="completed")


def test_unreadable_file_means_no_cases():
    """A corrupt case file is treated as empty instead of crashing the pages."""
    config.CASES_FILE.write_text("{not json", encoding="utf-8")
    assert cases.list_cases() == []


def test_reset_store_deletes_the_case_file():
    """The reset removes the case file (and a temp leftover); a missing file is fine (D53)."""
    escalated_case()
    config.CASES_FILE.with_suffix(".tmp").write_text("{}", encoding="utf-8")
    assert config.CASES_FILE.exists()
    assert cases.reset_store() == 1
    assert not config.CASES_FILE.exists() and not config.CASES_FILE.with_suffix(".tmp").exists()
    assert cases.list_cases() == []
    assert cases.reset_store() == 0


@pytest.mark.parametrize("enabled,kept", [(True, False), (False, True)])
def test_server_start_deletes_cases_unless_disabled(monkeypatch, enabled, kept):
    """Starting the app clears last session's cases; the flag lets a developer keep them."""
    monkeypatch.setattr(config, "RESET_CASES_ON_STARTUP", enabled)
    monkeypatch.setattr(main, "ensure_policies_ingested", lambda: None)
    monkeypatch.setattr(main.policy_search, "warm_up", lambda: None)
    escalated_case()
    with TestClient(main.app):  # entering the client runs the start-up
        assert config.CASES_FILE.exists() is kept


@pytest.mark.parametrize("enabled,kept", [(True, False), (False, True)])
def test_server_start_deletes_order_state_and_ledger_unless_disabled(monkeypatch, enabled, kept):
    """No refund history across restarts (D60): overlay and ledger go with the cases."""
    monkeypatch.setattr(config, "RESET_CASES_ON_STARTUP", enabled)
    monkeypatch.setattr(main, "ensure_policies_ingested", lambda: None)
    monkeypatch.setattr(main.policy_search, "warm_up", lambda: None)
    config.ORDER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.ORDER_STATE_FILE.write_text('{"orders": {}, "customers": {}}', encoding="utf-8")
    config.RESOLUTION_LOG.write_text("{}\n", encoding="utf-8")
    with TestClient(main.app):
        assert config.ORDER_STATE_FILE.exists() is kept
        assert config.RESOLUTION_LOG.exists() is kept


def test_send_shows_the_message_at_once_and_does_not_duplicate_it():
    """Step 1 of Send adds the message and a temporary "Working on it" line; step 2 replaces both."""
    from app.ui import gradio_app as ui

    history, message = ui.show_user_message("Return ORD-1001 please", [])
    assert history == [
        {"role": "user", "content": "Return ORD-1001 please"},
        {"role": "assistant", "content": ui.WORKING},
    ]
    assert message == "Return ORD-1001 please" and "Working on it" in ui.WORKING
    assert ui.show_user_message("   ", [])[0] == []  # a blank message is not added

    # Step 2 drops the two shown entries before handle_send adds the user turn and the reply again.
    final, _, _, _ = ui.handle_send(message, history[:-2], None)
    assert [t["role"] for t in final] == ["user", "assistant"]
    assert final[0]["content"] == "Return ORD-1001 please" and ui.WORKING not in final[1]["content"]


# ---- scenario 1: the chatbot completes ---------------------------------------------------


def test_in_policy_request_is_completed_by_the_chatbot():
    """A refund inside policy ends `completed`, resolved by the chatbot, with the reference."""
    result = cases.process_message(refund_agent(), "Return ORD-1001 please", "C-001")
    case = cases.get_case(result.case_id)
    assert result.case_id.startswith("CASE-") and case["case_id"] == result.case_id
    assert case["status"] == "completed" and case["resolved_by"] == "chatbot"
    assert case["completed_at"] and case["order_id"] == "ORD-1001"
    assert result.execution.resolution_id in case["outcome_summary"]
    assert case["customer_reply"].startswith("Hi Nora")
    assert case["debug"]["result"]["execution"]["action"] == "refund"
    assert [t["role"] for t in case["transcript"]] == ["user", "assistant"]
    assert cases.list_cases(("escalated", "in_review")) == []  # never reaches the staff queue


def test_rate_limited_request_leaves_no_case():
    """A request stopped by a limit raises and does not leave a half-made case behind."""
    agent = Agent(FakeLLM(und("return", "ORD-1001")), limiter=RequestLimiter(daily_budget=0))
    with pytest.raises(LimitExceeded):
        cases.process_message(agent, "Return ORD-1001", "C-001")
    assert cases.list_cases() == []


# ---- scenario 2: escalation and staff ----------------------------------------------------


def test_escalated_case_waits_for_staff_with_the_handoff_and_executes_nothing():
    """Above the agent limit: status `escalated`, hand-off saved, a suggestion, nothing executed."""
    case = escalated_case()
    assert case["status"] == "escalated" and case["resolved_by"] is None
    assert case["escalation_reason"] == "above_agent_limit" and case["reason_type"] == "policy"
    handoff = case["debug"]["result"]["handoff"]
    assert handoff["executed"] is False and handoff["customer"]["customer_id"] == "C-001"
    rec = case["chatbot_recommendation"]
    assert rec["action"] == "escalate" and rec["suggested_action"] == "refund"
    assert rec["suggested_amount"] > config.AGENT_APPROVAL_LIMIT
    assert ledger() == [] and orders.get_order("ORD-1021")["refunded_amount"] == 0.0


def test_a_request_for_a_human_is_escalated_and_not_executed():
    """Even inside policy, asking for a person gives an escalated case and no execution."""
    agent = Agent(
        FakeLLM(und("return", "ORD-1001", flags=["human_requested"]), lambda c, n: draft("refund"))
    )
    result = cases.process_message(agent, "I want a human. Return ORD-1001.", "C-001")
    case = cases.get_case(result.case_id)
    assert case["status"] == "escalated" and case["escalation_reason"] == "human_requested"
    assert ledger() == [] and orders.get_order("ORD-1001")["refunded_amount"] == 0.0


def test_opening_a_case_marks_it_in_review():
    """Opening an escalated case sets `in_review`; a completed case is left alone."""
    case = escalated_case()
    assert cases.open_case(case["case_id"])["status"] == "in_review"
    assert cases.open_case(case["case_id"])["status"] == "in_review"
    with pytest.raises(cases.CaseError):
        cases.open_case("CASE-NOPE")


def test_staff_approve_executes_once_and_completes_the_case():
    """Approve carries out the suggested refund with staff authority; a second submit is refused."""
    case = escalated_case()
    done = cases.staff_decide(case["case_id"], "approve", reply="Approved, sorry for the wait.")
    assert done["status"] == "completed" and done["resolved_by"] == "staff"
    assert done["staff_decision"] == "approve" and done["staff_reply_delivered"] is False
    res = done["execution"]
    assert (
        res["action"] == "refund"
        and res["amount"] == case["chatbot_recommendation"]["suggested_amount"]
    )
    assert res["resolution_id"] in done["staff_reply"] and done["staff_reply"].startswith(
        "Approved"
    )
    assert len(ledger()) == 1 and orders.get_order("ORD-1021")["refunded_amount"] == res["amount"]
    with pytest.raises(cases.CaseError, match="already completed"):
        cases.staff_decide(case["case_id"], "approve")
    assert len(ledger()) == 1  # a double click cannot pay twice
    audit = [json.loads(x) for x in config.AUDIT_LOG.read_text(encoding="utf-8").splitlines()]
    assert audit[-1]["case_id"] == case["case_id"] and audit[-1]["resolution_id"]


def test_staff_modify_is_capped_by_what_is_left_to_refund():
    """Modify executes the chosen action and amount, never more than the order has left."""
    case = escalated_case()
    with pytest.raises(cases.CaseError, match="Not executed"):
        cases.staff_decide(
            case["case_id"], "modify", reply="Partial.", action="refund_partial", amount=99999
        )
    assert cases.get_case(case["case_id"])["status"] == "escalated"  # a refused action stays open
    with pytest.raises(cases.CaseError, match="needs a reply"):
        cases.staff_decide(case["case_id"], "modify", action="refund_partial", amount=10)
    done = cases.staff_decide(
        case["case_id"], "modify", reply="A partial refund instead.",
        action="refund_partial", amount=10,
    )  # fmt: skip
    assert done["execution"]["action"] == "refund_partial" and done["execution"]["amount"] == 10.0
    assert orders.get_order("ORD-1021")["refunded_amount"] == 10.0


def test_staff_modify_with_no_action_only_replies():
    """Modify with action "none" completes the case with a reply and executes nothing."""
    case = escalated_case()
    done = cases.staff_decide(case["case_id"], "modify", reply="We will call you.", action="none")
    assert done["execution"] is None and done["staff_reply"] == "We will call you."
    assert ledger() == []


def test_staff_reject_executes_nothing_and_needs_a_reply():
    """Reject needs a reply, executes nothing, and completes the case."""
    case = escalated_case()
    with pytest.raises(cases.CaseError):
        cases.staff_decide(case["case_id"], "reject")
    done = cases.staff_decide(case["case_id"], "reject", reply="Sorry, we cannot do that.")
    assert done["status"] == "completed" and done["staff_decision"] == "reject"
    assert done["execution"] is None and ledger() == []
    with pytest.raises(cases.CaseError):
        cases.staff_decide(case["case_id"], "dance")


def test_staff_cannot_execute_for_another_customers_order():
    """Staff authority lifts the agent limit only: ownership and the order are still checked."""
    case = escalated_case()
    cases.update_case(case["case_id"], customer_id="C-002")  # ORD-1021 belongs to C-001
    with pytest.raises(cases.CaseError, match="Not executed"):
        cases.staff_decide(case["case_id"], "approve")
    assert ledger() == []


def test_someone_elses_order_is_completed_by_the_chatbot_with_no_staff_case():
    """The "not found" reply is a finished chatbot answer: nothing for staff to review (D55)."""
    result = cases.process_message(refund_agent("ORD-1029"), "Return ORD-1029", "C-001")
    case = cases.get_case(result.case_id)
    assert case["status"] == "completed" and case["resolved_by"] == "chatbot"
    assert "couldn't find that order on your account" in case["customer_reply"]
    assert cases.list_cases(("escalated", "in_review")) == [] and ledger() == []


def test_staff_reply_is_delivered_to_the_customer_once():
    """The customer page gets the staff reply on the first refresh only."""
    case = escalated_case()
    assert cases.deliver_staff_reply(case["case_id"]) is None  # nothing from staff yet
    cases.staff_decide(case["case_id"], "reject", reply="No, sorry.")
    assert cases.deliver_staff_reply(case["case_id"]) == "No, sorry."
    assert cases.deliver_staff_reply(case["case_id"]) is None
    assert cases.get_case(case["case_id"])["staff_reply_delivered"] is True


def test_case_file_has_no_contact_details():
    """The saved case keeps masked text and no email, address or phone."""
    result = cases.process_message(
        refund_agent(), "Return ORD-1001, write to nora.private@example.com", "C-001"
    )
    text = config.CASES_FILE.read_text(encoding="utf-8")
    assert "nora.private@example.com" not in text
    customers = orders._customers()["C-001"]
    for key in ("email", "address", "phone"):
        if customers.get(key):
            assert str(customers[key]) not in text
    assert result.case_id in text


# ---- API ---------------------------------------------------------------------------------


@pytest.fixture
def client():
    """A web client on an agent that returns ORD-1021 above the limit."""
    agent = refund_agent("ORD-1021")
    main.app.dependency_overrides[main.get_agent] = lambda: agent
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_api_case_status_and_staff_review(client):
    """resolve returns the case status; review (approve) completes it; the case endpoint shows it."""
    body = client.post(
        "/api/v1/resolve", json={"customer_id": "C-001", "message": "Return ORD-1021 please"}
    ).json()
    cid = body["case_id"]
    assert body["case_status"] == "escalated"
    assert client.get(f"/api/v1/cases/{cid}").json()["status"] == "escalated"
    done = client.post("/api/v1/review", json={"case_id": cid, "action": "approve"}).json()
    assert done["review_status"] == "approved" and done["execution"]["action"] == "refund"
    shown = client.get(f"/api/v1/cases/{cid}").json()
    assert shown["status"] == "completed" and shown["resolved_by"] == "staff"
    assert client.get("/api/v1/cases/CASE-NOPE").status_code == 404
    again = client.post("/api/v1/review", json={"case_id": cid, "action": "approve"})
    assert again.status_code == 409


def test_api_reject_and_modify(client):
    """reject executes nothing; modify without a reply is a conflict (409)."""
    cid = client.post(
        "/api/v1/resolve", json={"customer_id": "C-001", "message": "Return ORD-1021 please"}
    ).json()["case_id"]
    bad = client.post("/api/v1/review", json={"case_id": cid, "action": "modify"})
    assert bad.status_code == 409
    ok = client.post(
        "/api/v1/review", json={"case_id": cid, "action": "reject", "edited_reply": "No."}
    ).json()
    assert ok["review_status"] == "rejected" and ok["execution"] is None


# ---- pages -------------------------------------------------------------------------------


def test_customer_status_wording_follows_the_processing_flow():
    """Status text follows the wording table of section 8.1; the chatbot is never named."""
    from app.ui.gradio_app import inline_status, status_text

    base = cases._new_record("CASE-X", "C-001", "hi")
    assert "Working on it" in status_text(base)
    esc = {**base, "status": "escalated"}
    assert status_text(esc) == "⏳ With our team · Request · we'll reply here"
    assert status_text({**base, "status": "in_review"}) == "👀 Our team is reviewing · Request"
    done = {**base, "status": "completed", "resolved_by": "chatbot", "outcome_summary": "Refund."}
    assert status_text(done).startswith("✅ Resolved · Request") and "Assistant" not in status_text(
        done
    )
    by_staff = {**done, "resolved_by": "staff"}
    assert status_text(by_staff) == "✅ Resolved by our team · Request"
    # Inline line: an open case shows its status, a chatbot-resolved case only its outcome.
    assert inline_status(None) == "" and inline_status(esc) == f"\n\n*{status_text(esc)}*"
    assert inline_status(done) == "\n\n*Refund.*"
    assert inline_status({**done, "outcome_summary": ""}) == ""


def test_request_name_uses_the_type_and_the_item():
    """ "Return – Wireless headphones" from the saved result and the order; type alone otherwise."""
    from app.ui.gradio_app import request_name

    case = cases._new_record("CASE-X", "C-001", "hi")
    case.update(order_id="ORD-1001", debug={"result": {"intent": "return"}})
    assert request_name(case) == "Return – Wireless headphones"
    assert request_name({**case, "order_id": None}) == "Return"
    assert request_name({**case, "debug": {}, "order_id": None}) == "Request"


def test_customer_refresh_appends_the_staff_reply_once():
    """The timer handler adds the staff reply to the chat once, then leaves the chat alone."""
    from app.ui.gradio_app import refresh_customer

    case = escalated_case()
    history = [{"role": "user", "content": "hi"}]
    _, line, listing, label, _ = refresh_customer([case["case_id"]], history)
    assert "1 request with our team" in line["value"] and line["visible"] is True
    assert case["case_id"] in listing and label["label"] == "My requests (1)"
    cases.staff_decide(case["case_id"], "reject", reply="No, sorry.")
    new_history, line, listing, _, stamp = refresh_customer([case["case_id"]], history)
    last = new_history[-1]["content"]
    assert last.startswith(f"🧑‍💼 **Brightcart staff** · update on {case['case_id']}")
    assert "No, sorry." in last and "✅ Resolved by our team" in last
    assert line["visible"] is False and "Resolved by our team" in listing  # leaves the amber line
    assert stamp.startswith("Last updated")
    again = refresh_customer([case["case_id"]], new_history)[0]
    assert not isinstance(again, list)  # gr.update(): the chat is not rewritten
    empty = refresh_customer(None, history)
    assert empty[1]["visible"] is False and empty[3]["label"] == "My requests (0)"


def test_staff_page_lists_opens_and_decides():
    """The staff page lists open cases, fills the details, and submits a decision."""
    from app.ui import staff_ui

    case = escalated_case()
    assert [r[0] for r in staff_ui.queue_rows()] == [case["case_id"]]
    details, rec, action, amount, *_ = staff_ui.open_case(case["case_id"])
    assert "Hand-off to staff" in details and "Conversation" in details
    assert "```json" not in details and "handoff_id" not in details  # table only, no JSON
    assert "Approve would carry out" in rec and action == "refund" and amount > 5000
    assert cases.get_case(case["case_id"])["status"] == "in_review"
    msg, rows, _ = staff_ui.submit_decision(case["case_id"], "reject", "none", None, "", "No.")
    assert msg.startswith("✅") and rows == []
    msg, *_ = staff_ui.submit_decision(case["case_id"], "approve", "none", None, "", "")
    assert msg.startswith("⚠️")  # already completed
    assert staff_ui.submit_decision(None, "approve", "none", None, "", "")[0].startswith("⚠️")


def test_developer_page_is_read_only_and_shows_the_saved_case():
    """The developer page rebuilds every section from the saved case and writes nothing."""
    from app.ui import dev_ui

    case = escalated_case()
    before = config.CASES_FILE.read_text(encoding="utf-8")
    banner, why, _evidence, staff, pipeline, trace, _retrieval, _guards, raw, raw_json = (
        dev_ui.show_case(case["case_id"])
    )
    assert "Escalated to human staff: <b>Yes</b>" in banner and "Hand-off to staff" in why
    assert "No staff decision yet" in staff and "Latency" in pipeline
    assert trace and raw_json["case_id"] == case["case_id"] and "resolved_by" in raw
    assert dev_ui.case_choices()[0][1] == case["case_id"]
    assert dev_ui.show_case(None)[0] == dev_ui.EMPTY
    assert config.CASES_FILE.read_text(encoding="utf-8") == before
    cases.staff_decide(case["case_id"], "reject", reply="No.")
    assert "reject" in dev_ui.show_case(case["case_id"])[3]
