"""Tests for follow-ups on a case that waits for staff (processing flow Scenarios 3 and 4, D58, D59).

No network: the agent is a stub that counts its calls, or the scripted fake LLM from test_agent.
The key rule: a message about a held case never reaches the agent, so the chatbot cannot decide it.
conftest.py gives every test its own empty case file.
"""

import gradio as gr
import pytest

from app import cases, followup
from app.agent.graph import _local_result
from app.followup import Route, route_message
from tests.test_agent import _env  # noqa: F401 - autouse fixture
from tests.test_cases import escalated_case, refund_agent

CUSTOMER = "C-001"


class CountingAgent:
    """Stands in for the Agent: answers every message with a fixed reply and counts the calls."""

    def __init__(self):
        self.calls = []

    def resolve(self, message, customer_id, session_id=None, case_id=None):
        """Record the call and return a plain answer."""
        self.calls.append(message)
        return _local_result(
            "need_info", "General answer.", "test", clarifying_question="General answer."
        )


def held(**kw):
    """A minimal held case record for the router tests."""
    base = {
        "case_id": "CASE-0000AAAA", "order_id": "ORD-1021", "status": "escalated",
        "transcript": [{"role": "user", "content": "I want to return the ergonomic chair"}],
    }  # fmt: skip
    return {**base, **kw}


# ---- the router (flow doc tables 5.6 and 6.5) ------------------------------------------------


@pytest.mark.parametrize(
    "message,kind",
    [
        ("Any update on my return?", followup.STATUS_CHECK),
        ("any news about ORD-1021", followup.STATUS_CHECK),
        ("Status of CASE-0000AAAA please", followup.STATUS_CHECK),
        ("The chair arrived with a cracked leg", followup.ADDED_INFO),
        ("Also the box was torn, ORD-1021", followup.ADDED_INFO),
        ("Just refund me now!", followup.REPEAT),
        ("Refund ORD-1021 asap", followup.REPEAT),
        ("What's your shipping policy?", followup.UNRELATED),
        ("Do you refund expensive items?", followup.UNRELATED),
        ("I want to return the mouse from ORD-1002", followup.UNRELATED),
        ("How long does it take?", followup.AMBIGUOUS),
    ],
)
def test_router_table(message, kind):
    """Each documented example goes to its route while the case is escalated."""
    route = route_message(message, [held()])
    assert route.kind == kind and route.open_case_found


def test_router_with_no_open_case_is_normal():
    """Without a case waiting for staff nothing changes: the normal pipeline."""
    assert route_message("Any update on my return?", []) == Route(followup.NORMAL)


def test_order_ids_match_in_any_case_and_a_second_id_with_the_held_one_stays_related():
    """The held order id is matched ignoring case, and naming it makes the message related."""
    assert route_message("update on ord-1021", [held()]).kind == followup.STATUS_CHECK
    assert route_message("ORD-1002 and ORD-1021 any update", [held()]).kind == followup.STATUS_CHECK


def test_templates_never_state_a_decision():
    """The fixed replies name the case and never promise or reveal an outcome."""
    for kind in (followup.STATUS_CHECK, followup.REPEAT, followup.ADDED_INFO, followup.AMBIGUOUS):
        text = followup.reply_for(Route(kind, held(), True))
        assert "CASE-0000AAAA" in text
        assert "approved" not in text.lower() and "will be refunded" not in text.lower()


# ---- handle_message with a real escalated case ---------------------------------------------


def snapshot(case_id):
    """The saved case as a comparable value."""
    return repr(cases.get_case(case_id))


def test_status_check_leaves_the_case_untouched_and_skips_the_agent():
    """Scenario 3, rows 1 and 3: a status reply, no new case, no agent call, case unchanged."""
    case = escalated_case()
    before, count = snapshot(case["case_id"]), len(cases.list_cases())
    agent = CountingAgent()
    for text in ("Any update on my return?", "Just refund me now!"):
        result = cases.handle_message(agent, text, CUSTOMER, "s1")
        assert result.case_id == case["case_id"] and case["case_id"] in result.customer_reply_draft
    assert agent.calls == [] and snapshot(case["case_id"]) == before
    assert len(cases.list_cases()) == count


def test_added_information_is_appended_and_flagged_for_staff():
    """Scenario 3, row 2: transcript grows, the marker is set, the status stays escalated."""
    case = escalated_case()
    agent = CountingAgent()
    result = cases.handle_message(agent, "The chair arrived with a cracked leg", CUSTOMER, "s1")
    saved = cases.get_case(case["case_id"])
    assert agent.calls == [] and result.case_id == case["case_id"]
    assert saved["status"] == "escalated" and saved["has_new_customer_message"] is True
    assert saved["transcript"][-1] == {
        "role": "user",
        "content": "The chair arrived with a cracked leg",
    }
    assert saved["debug"]["routing"]["route"] == followup.ADDED_INFO
    assert saved["debug"]["result"]  # the hand-off for the staff page is still there
    assert len(cases.list_cases()) == 1


def test_added_information_is_masked_like_any_message():
    """A phone number typed in a follow-up is masked before it is saved."""
    case = escalated_case()
    cases.handle_message(CountingAgent(), "It arrived cracked, call me on 0917 123 4567", CUSTOMER)
    assert "0917 123 4567" not in cases.get_case(case["case_id"])["transcript"][-1]["content"]


def test_unrelated_question_runs_the_pipeline_and_the_open_case_stays_as_it_was():
    """Scenario 4: the shipping question is answered by the agent, in its own new case."""
    case = escalated_case()
    before = snapshot(case["case_id"])
    agent = CountingAgent()
    result = cases.handle_message(agent, "What's your shipping policy?", CUSTOMER, "s1")
    assert agent.calls == ["What's your shipping policy?"]
    assert result.case_id != case["case_id"] and snapshot(case["case_id"]) == before
    new = cases.get_case(result.case_id)
    assert new["debug"]["routing"] == {
        "open_case_found": True, "route": followup.UNRELATED, "case_id": case["case_id"],
    }  # fmt: skip


def test_a_request_about_another_order_makes_a_new_case():
    """Scenario 4: "return the mouse from ORD-1013" is its own case, the held one is unchanged."""
    case = escalated_case()
    before = snapshot(case["case_id"])
    result = cases.handle_message(refund_agent("ORD-1013"), "Return ORD-1013", CUSTOMER, "s1")
    assert result.case_id != case["case_id"] and snapshot(case["case_id"]) == before
    assert {c["case_id"] for c in cases.list_cases()} == {case["case_id"], result.case_id}


def test_unclear_message_asks_a_question_and_changes_nothing():
    """ "How long does it take?" gets the clarifying question, without the agent or a case change."""
    case = escalated_case()
    before, agent = snapshot(case["case_id"]), CountingAgent()
    result = cases.handle_message(agent, "How long does it take?", CUSTOMER)
    assert agent.calls == [] and "general question" in result.customer_reply_draft
    assert snapshot(case["case_id"]) == before


def test_a_message_with_a_safety_word_is_never_a_shortcut():
    """A safety report inside a follow-up goes to the full pipeline, which escalates it."""
    escalated_case()
    agent = CountingAgent()
    cases.handle_message(agent, "Any update? The charger is smoking!", CUSTOMER)
    assert len(agent.calls) == 1


def test_a_case_staff_just_finished_is_no_longer_held():
    """If staff complete the case before the message arrives, the message is a normal one."""
    case = escalated_case()
    cases.staff_decide(case["case_id"], "reject", reply="No, sorry.")
    agent = CountingAgent()
    cases.handle_message(agent, "Any update on my return?", CUSTOMER)
    assert len(agent.calls) == 1


def test_other_customers_cases_do_not_count():
    """Only the customer's own open cases hold a message."""
    escalated_case()  # belongs to C-001
    agent = CountingAgent()
    cases.handle_message(agent, "Any update on my return?", "C-002")
    assert len(agent.calls) == 1


# ---- one open escalation per customer and order (D59) -------------------------------------


def test_a_second_escalation_for_the_same_order_is_merged_into_the_open_case():
    """The agent escalating ORD-1021 again does not leave a second open case."""
    first = escalated_case()
    result = cases.process_message(refund_agent("ORD-1021"), "Please return my chair", CUSTOMER)
    assert result.case_id == first["case_id"] and first["case_id"] in result.customer_reply_draft
    open_ = cases.list_cases(("escalated", "in_review"))
    assert [c["case_id"] for c in open_] == [first["case_id"]]
    assert open_[0]["has_new_customer_message"] is True
    assert open_[0]["transcript"][-1]["content"] == "Please return my chair"
    duplicates = [c for c in cases.list_cases() if c.get("merged_into")]
    assert len(duplicates) == 1 and duplicates[0]["status"] == "completed"


# ---- staff page and customer page --------------------------------------------------------


def test_staff_open_and_reload_clear_the_new_message_marker_and_keep_the_form():
    """The queue shows 💬, Reload case re-reads the transcript, and the form is not an output."""
    from app.ui import staff_ui

    case = escalated_case()
    cases.handle_message(CountingAgent(), "The chair arrived with a cracked leg", CUSTOMER)
    assert [r[staff_ui.QUEUE_HEADERS.index("New msg")] for r in staff_ui.queue_rows()] == ["💬"]
    details, _ = staff_ui.reload_case(case["case_id"])
    assert "cracked leg" in details
    assert cases.get_case(case["case_id"])["has_new_customer_message"] is False
    assert [r[staff_ui.QUEUE_HEADERS.index("New msg")] for r in staff_ui.queue_rows()] == [""]
    cases.handle_message(CountingAgent(), "Also the box was torn", CUSTOMER)
    staff_ui.open_case(case["case_id"])  # opening clears it too
    assert cases.get_case(case["case_id"])["has_new_customer_message"] is False
    assert staff_ui.reload_case(None) == (gr.skip(), gr.skip())


def test_customer_page_lists_every_case_but_the_amber_line_only_open_ones():
    """Two cases: My requests lists both, the open line only the one with staff, staff reply labelled."""
    from app.ui.gradio_app import refresh_customer, render_my_requests, render_open_line

    first = escalated_case()
    second = cases.handle_message(CountingAgent(), "What's your shipping policy?", CUSTOMER)
    ids = [first["case_id"], second.case_id]
    found = [cases.get_case(i) for i in ids]
    listing = render_my_requests(found)
    assert listing.count('class="row"') == 2 and first["case_id"] in listing
    assert second.case_id in listing and "Assistant" not in listing
    assert render_open_line(found).startswith("⏳ 1 request with our team")
    cases.staff_decide(first["case_id"], "reject", reply="No, sorry.")
    history, line, listing, _, _ = refresh_customer(ids, [])
    assert history[-1]["content"].startswith(
        f"🧑‍💼 **Brightcart staff** · update on {first['case_id']}"
    )
    assert "Resolved by our team" in listing and line["visible"] is False


def test_a_human_request_is_named_for_the_customer_and_assumes_no_item():
    """A bare "talk to human staff" is its own case named "Talk to our team", with no item."""
    from app.ui.gradio_app import request_name

    escalated_case()  # an earlier held case (ORD-1021) must not leak into the new one
    agent = CountingAgent()
    assert route_message("I want to talk to human staff", cases.list_cases()).kind == "unrelated"
    from tests.test_agent import FakeLLM, draft, und

    llm = FakeLLM(und("policy_question", "ORD-1021"), lambda c, n: draft("need_info", question="?"))
    from app.agent.graph import Agent

    result = cases.handle_message(Agent(llm), "I want to talk to human staff", CUSTOMER, "s9")
    case = cases.get_case(result.case_id)
    assert agent.calls == [] and case["order_id"] is None
    assert request_name(case) == "Talk to our team"


def test_customer_page_has_no_customer_picker():
    """The page chats as the fixed demo customer (D57): no dropdown labelled Customer."""
    from app.ui.gradio_app import SAMPLES, build_ui

    demo = build_ui()
    labels = [getattr(b, "label", None) for b in demo.blocks.values()]
    assert "Customer" not in labels and "Sample message" in labels
    assert not any(
        "someone else" in name.lower() or "ownership" in name.lower() for name in SAMPLES
    )
    chats = [b for b in demo.blocks.values() if getattr(b, "elem_id", None) == "chat"]
    assert [b.height for b in chats] == [420]
    assert all(isinstance(text, str) for text in SAMPLES.values())
