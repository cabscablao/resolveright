"""Tests for the agent orchestrator (the code that runs one customer request end to end).

The agent understands the message, calls tools, asks the LLM for a draft, checks the draft and
hands it to a human. Here the LLM is replaced by a scripted fake, so there is no network and
every run is predictable. Real-LLM runs live in scripts.run_scenarios.
"""

import json

import pytest

from app import config
from app.agent.graph import Agent
from app.agent.llm import LLMError
from app.agent.schemas import Citation, Draft, Understanding
from app.rag.embeddings import HashEmbeddings, make_embeddings
from app.rag.ingest import ingest
from app.rag.retriever import Retriever
from app.tools import policy_search


class FakeLLM:
    """Scripted stand-in for the real LLM: no network, and the same result every time.

    It returns a fixed Understanding and builds each Draft by calling a function we pass in
    (case JSON -> Draft). It also records what the agent sent it, so tests can check that.
    """

    def __init__(self, understanding, drafter=None):
        """`understanding` may be one object, an exception, or a list used one per request."""
        self.understanding, self.drafter, self.draft_calls = understanding, drafter, []
        self.understand_inputs = []

    def parse(self, system, user, schema):
        """Answer like the real LLM: an Understanding for step 1, a Draft for step 2."""
        if schema is Understanding:
            self.understand_inputs.append(user)
            u = self.understanding
            if isinstance(u, list):
                u = u.pop(0)
            if isinstance(u, Exception):
                raise u
            return u
        self.draft_calls.append(user)
        # The agent sends the case as JSON inside the prompt. Cut it out so the drafter can read it.
        case = json.loads(user.split("Case information (JSON):\n")[1].split("\n\nYour previous")[0])
        return self.drafter(case, len(self.draft_calls))


def und(intent, order_id=None, flags=(), **kw):
    """Build the "understanding" the fake LLM returns, with harmless defaults."""
    base = {"item_sku": None, "error_origin": None, "summary": "summary"}
    return Understanding(intent=intent, order_id=order_id, flags=list(flags), **{**base, **kw})


def draft(
    rec, amount=None, citations=(), question=None, reply="Hi {{first_name}}, ok.", covered=True
):
    """Build a valid LLM draft. Tests change only the parts they care about."""
    return Draft(
        recommendation=rec, proposed_action=None, amount=amount, rationale="because",
        policy_citations=list(citations), clarifying_question=question,
        customer_reply_draft=reply, confidence=0.9, covered_by_policy=covered,
    )  # fmt: skip


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    """Give every test its own policy store and log files, so tests never touch real data.

    It uses fake hash-based embeddings (no API call) and points the logs at a temp folder.
    """
    emb = make_embeddings(cache_dir=tmp_path / "c", underlying=HashEmbeddings())
    ingest(config.DATA_DIR / "policies", "baseline", emb, tmp_path / "q")
    policy_search.set_retriever(Retriever(emb, "baseline", tmp_path / "q"))
    monkeypatch.setattr(config, "ESCALATION_LOG", tmp_path / "esc.jsonl")
    monkeypatch.setattr(config, "AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr(config, "PROMPT_VERSION", "v1_baseline")
    yield
    # Clean up so the next test does not reuse this test's store.
    policy_search.set_retriever(None)


def run(llm, message="msg", customer="C-001"):
    """Run one customer message through a new agent and return its result."""
    return Agent(llm).resolve(message, customer)


def test_refund_happy_path_caps_amount_executes_and_needs_no_human():
    """A normal refund is capped at the rules engine's maximum, then carried out by the agent.

    The LLM asks for 15000, but the engine limit (2750.00) wins. Money decisions stay with rules.
    It is under the ₱5,000 agent limit, so policy needs no human decision: the agent executes it.
    """
    llm = FakeLLM(und("return", "ord-1001"), lambda c, n: draft("refund", amount=15000.0))
    r = run(llm)
    assert r.recommendation == "refund" and r.amount == 2750.0  # Capped at the engine maximum.
    assert r.requires_human_approval is False and r.review_status == "not_required"
    assert r.guardrail_status == "passed"
    assert [t["tool"] for t in r.tool_trace] == [
        "lookup_order",
        "check_eligibility",
        "search_policy",
        "execute_resolution",
    ]
    assert r.execution and r.execution.action == "refund" and r.execution.amount == 2750.0
    assert r.execution.resolution_id in r.customer_reply_draft  # the reply carries the reference


def test_missing_order_id_asks_one_question_without_tools():
    """With no order number, the agent asks for it and calls no tools (nothing to look up yet)."""
    r = run(FakeLLM(und("return", None)))
    assert r.recommendation == "need_info" and r.clarifying_question and r.tool_trace == []


def test_engine_escalation_overrides_llm_and_logs(tmp_path):
    """When the rules engine says "escalate", the LLM is not asked to decide, and a record is saved.

    Large refunds need a manager, and the model must not talk its way around that.
    """
    llm = FakeLLM(und("return", "ORD-1021"), lambda c, n: draft("refund"))
    r = run(llm, customer="C-001")
    assert r.recommendation == "escalate" and r.approval_level == "manager"
    assert r.escalation and r.escalation.reason_code == "above_agent_limit"
    assert llm.draft_calls == []  # The LLM was never asked to decide.
    assert (tmp_path / "esc.jsonl").exists()


def test_llm_recommendation_outside_allowed_is_repaired_once():
    """If the LLM picks a forbidden recommendation, the agent asks again once and uses the fix."""

    def drafter(case, n):
        """Fail on the first try (refund is not allowed), then give the allowed answer."""
        return draft("refund") if n == 1 else draft("deny")

    r = run(FakeLLM(und("return", "ORD-1027"), drafter), customer="C-001")
    assert r.recommendation == "deny" and r.guardrail_status == "repaired"


def test_llm_keeps_violating_engine_then_safe_fallback():
    """If the LLM breaks the rules twice, the agent stops retrying and escalates to a human."""
    r = run(FakeLLM(und("return", "ORD-1027"), lambda c, n: draft("refund")), customer="C-001")
    assert r.recommendation == "escalate" and r.guardrail_status == "fallback"


def test_invented_citation_is_removed():
    """A citation to a policy chunk that does not exist is dropped, so no fake sources are shown."""
    bogus = Citation(doc_id="POL-99", section="x", chunk_id="POL-99#c0")
    r = run(FakeLLM(und("policy_question"), lambda c, n: draft("inform", citations=[bogus])))
    assert r.policy_citations == []


def test_valid_citation_is_kept():
    """A citation to a chunk the agent really retrieved is kept."""

    def drafter(case, n):
        """Cite the first policy chunk the agent gave the LLM."""
        ch = case["policy_chunks"][0]
        cite = Citation(doc_id=ch["doc_id"], section=ch["section"], chunk_id=ch["chunk_id"])
        return draft("inform", citations=[cite])

    r = run(FakeLLM(und("policy_question"), drafter))
    assert len(r.policy_citations) == 1 and r.amount is None


def test_other_customers_order_is_not_revealed():
    """Asking about another customer's order gets the plain "not found" reply (D55).

    No escalation, nothing in the reply or trace that tells it apart from a missing order, and the
    order id must not appear in the rationale or reply.
    """
    r = run(FakeLLM(und("return", "ORD-1002")), customer="C-001")
    missing = run(FakeLLM(und("return", "ORD-9999")), customer="C-001")
    assert r.recommendation == "need_info" and r.escalation is None
    assert "ORD-1002" not in r.rationale and "ORD-1002" not in r.customer_reply_draft
    assert r.customer_reply_draft == missing.customer_reply_draft
    assert "I couldn't find that order on your account" in r.customer_reply_draft
    assert "one of your other orders" in r.customer_reply_draft
    assert r.tool_trace[0]["error"] == missing.tool_trace[0]["error"] == "not_found"


def test_guessed_error_origin_is_dropped_so_the_engine_asks_who_made_the_error():
    """If the LLM guesses who made the error, the engine ignores it and asks the customer.

    The message does not say who is at fault, so the answer must not be assumed.
    """
    seen = []

    def drafter(case, n):
        """Record which reason codes the engine produced, then ask a question."""
        seen.append(case["eligibility_result"]["reason_codes"])
        return draft("need_info", question="Who made the error?")

    llm = FakeLLM(und("wrong_item", "ORD-1028", error_origin="customer"), drafter)
    r = run(llm, "The personalised mug has my name spelled wrong.", "C-001")
    assert seen == [["ERROR_ORIGIN_UNKNOWN"]] and r.recommendation == "need_info"


def test_explicit_error_origin_is_kept():
    """If the customer clearly says they made the error, the engine uses that fact."""
    seen = []

    def drafter(case, n):
        """Record which reason codes the engine produced, then deny."""
        seen.append(case["eligibility_result"]["reason_codes"])
        return draft("deny")

    llm = FakeLLM(und("wrong_item", "ORD-1028", error_origin="customer"), drafter)
    run(llm, "I typed the name wrong on the mug, ORD-1028. Can I swap it?", "C-001")
    assert seen == [["CUSTOMER_ERROR_PERSONALISED"]]


def test_safety_keywords_escalate_even_when_the_llm_misses_the_hazard():
    """Words like "overheating" force an urgent escalation, even if the LLM did not flag danger.

    A keyword check is a safety net, because a missed fire risk is the worst outcome.
    """
    r = run(FakeLLM(und("defective", "ORD-1023")), "My power bank is overheating!", "C-001")
    assert r.escalation.urgency == "urgent" and r.escalation.reason_code == "safety_hazard"


def test_safety_keywords_do_not_fire_on_ordinary_messages():
    """Harmless words that only look similar ("system", "late") do not trigger a false alarm."""
    llm = FakeLLM(und("policy_question"), lambda case, n: draft("inform"))
    r = run(llm, "What is your system for tracking returns? My package is late.")
    assert r.recommendation == "inform" and r.escalation is None


def test_safety_flag_escalates_urgently_without_order_tools():
    """When the LLM flags a safety hazard, the agent escalates at once and skips the order tools."""
    r = run(FakeLLM(und("defective", "ORD-1023", flags=["safety_hazard"])), customer="C-001")
    assert r.escalation.urgency == "urgent" and r.escalation.reason_code == "safety_hazard"
    # No order tools; the policy search only supplies the citations shown to the reviewer.
    assert [t["tool"] for t in r.tool_trace] == ["search_policy", "escalate_to_human"]
    assert r.policy_citations, "a safety hand-over must show the policy it follows"


def test_order_tool_timeout_retries_then_escalates(monkeypatch):
    """If the order tool keeps timing out, the agent retries and then hands off to a human."""
    monkeypatch.setenv("FAULT_INJECTION", "lookup_order:timeout")
    r = run(FakeLLM(und("return", "ORD-1001")))
    tools = [t["tool"] for t in r.tool_trace]
    assert tools.count("lookup_order") == 1  # The trace shows only the final outcome.
    assert r.recommendation == "escalate" and r.escalation.reason_code == "tool_failure"


def test_llm_failure_gives_safe_escalation_not_a_crash():
    """If the LLM call fails (for example, rate limited), the customer gets a safe escalation."""
    r = run(FakeLLM(LLMError("RateLimitError")))
    assert r.recommendation == "escalate" and r.escalation.reason_code == "tool_failure"


def test_names_never_reach_the_llm():
    """The customer's name and email are never put in the prompt sent to the LLM.

    The reply uses a {{first_name}} placeholder, which is filled in only after the LLM is done.
    """
    llm = FakeLLM(und("return", "ORD-1001"), lambda c, n: draft("refund"))
    run(llm)
    assert "Nora" not in llm.draft_calls[0] and "email" not in llm.draft_calls[0].lower()


def test_llm_wrapper_drops_temperature_for_reasoning_models():
    """If the API rejects the temperature setting, the wrapper retries once without it.

    Some models (reasoning models) do not accept temperature. All the fake classes below
    copy just enough of the OpenAI client to produce that exact error.
    """
    import openai

    from app.agent.llm import OpenAILLM

    class Msg:
        """Fake chat message: no refusal, and a parsed value of "ok"."""

        refusal, parsed = None, "ok"

    class Resp:
        """Fake API response with one choice and no token usage."""

        choices = (type("C", (), {"message": Msg}),)
        usage = None

    calls = []

    class Completions:
        """Fake completions endpoint: rejects any call that includes temperature."""

        def parse(self, **kw):
            """Record the call, and raise a 400 error if temperature was sent."""
            calls.append(kw)
            if "temperature" in kw:
                raise openai.BadRequestError(
                    "temperature unsupported",
                    response=type("R", (), {"status_code": 400, "headers": {}, "request": None})(),
                    body=None,
                )
            return Resp

    client = type("Cl", (), {"chat": type("Ch", (), {"completions": Completions()})()})()
    llm = OpenAILLM(model="gpt-4o-mini", client=client, sleep=lambda s: None)
    assert llm.parse("s", "u", Draft) == "ok"
    assert "temperature" in calls[0] and "temperature" not in calls[1]
    assert "reasoning_effort" not in calls[0]  # Only reasoning models get it.


def test_reasoning_models_get_minimal_effort_and_no_temperature():
    """Reasoning models (gpt-5) run with minimal effort and no temperature. Normal models do not."""
    from app.agent.llm import OpenAILLM

    llm = OpenAILLM(model="gpt-5-nano", client=object())
    assert llm.temperature is None and llm.reasoning_effort == "minimal"
    assert OpenAILLM(model="gpt-4o-mini", client=object()).reasoning_effort is None


# ---- Weekend 1: human review gate, memory, clarification, degradation -----------------------


def audit_lines(tmp_path):
    """Read the audit log (one JSON record per line). Return an empty list if nothing was logged."""
    f = tmp_path / "audit.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def refund_agent(**kw):
    """Build an agent whose fake LLM always recommends a refund for ORD-1001 (inside policy)."""
    llm = FakeLLM(und("return", "ORD-1001"), lambda c, n: draft("refund"))
    return Agent(llm, **kw)


def escalated_agent(**kw):
    """Build an agent for a case policy keeps for a person.

    ORD-1021 (customer C-001) is above the ₱5,000 agent limit, so the rules engine escalates it
    whatever the LLM drafts. Use it with customer "C-001".
    """
    llm = FakeLLM(und("return", "ORD-1021"), lambda c, n: draft("refund"))
    return Agent(llm, **kw)


def escalation_lines(tmp_path):
    """Count the records in the escalation log (0 if it was never written)."""
    f = tmp_path / "esc.jsonl"
    return len(f.read_text().splitlines()) if f.exists() else 0


def test_escalated_result_is_pending_until_a_human_reviews():
    """An escalated result is "pending" and has a case id, because a human must take it."""
    r = escalated_agent().resolve("Return ORD-1021 please", "C-001")
    assert r.recommendation == "escalate" and r.requires_human_approval is True
    assert r.review_status == "pending" and r.case_id.startswith("CASE-")


def test_in_policy_refund_needs_no_review():
    """A refund inside agent authority is not pending, and review() refuses it."""
    from app.agent.graph import ReviewError

    agent = refund_agent()
    r = agent.resolve("Return ORD-1001 please", "C-001")
    assert r.review_status == "not_required" and r.requires_human_approval is False
    with pytest.raises(ReviewError):
        agent.review(r.case_id, "approve")


@pytest.mark.parametrize("rec", ["deny", "inform", "need_info"])
def test_answers_and_denials_need_no_human(rec):
    """Answering, denying or asking a question changes nothing, so it never waits for a person."""
    from app.agent.schemas import ResolutionResult

    r = ResolutionResult(recommendation=rec, rationale="x", customer_reply_draft="Hi")
    assert r.requires_human_approval is False


def test_only_escalate_requires_a_human():
    """Every recommendation except "escalate" stays inside policy and agent authority."""
    from typing import get_args

    from app.agent.schemas import ResolutionResult
    from app.tools.eligibility import Recommendation

    for rec in get_args(Recommendation):
        r = ResolutionResult(recommendation=rec, rationale="x", customer_reply_draft="Hi")
        assert r.requires_human_approval is (rec == "escalate")


def test_approve_writes_audit_only(tmp_path):
    """Approving an escalated case writes an audit entry and nothing else.

    The agent never executes an escalated case, so approving must not create a new escalation.
    """
    agent = escalated_agent()
    r = agent.resolve("Return ORD-1021 please", "C-001")
    before = escalation_lines(tmp_path)  # The case itself already logged one escalation.
    done = agent.review(r.case_id, "approve", reviewer="agent-7")
    assert done.review_status == "approved"
    (entry,) = audit_lines(tmp_path)
    assert entry["action"] == "approve" and entry["reviewer"] == "agent-7"
    assert "executed nothing" in entry["note"]
    assert escalation_lines(tmp_path) == before  # Approving does not escalate again.


def test_edit_replaces_reply_and_logs_only_its_length(tmp_path):
    """Editing swaps in the human's reply, and the audit log keeps only its length.

    The edited text could hold personal data, so it is not written to the log.
    """
    agent = escalated_agent()
    r = agent.resolve("Return ORD-1021 please", "C-001")
    done = agent.review(r.case_id, "edit", edited_reply="Hello, here is my edited reply.")
    assert done.review_status == "edited"
    assert done.customer_reply_draft == "Hello, here is my edited reply."
    (entry,) = audit_lines(tmp_path)
    assert entry["edited_reply_chars"] == len(
        "Hello, here is my edited reply."
    ) and "edited reply" not in json.dumps(entry)


def test_escalate_action_records_escalation(tmp_path):
    """A human can choose "escalate" in review, and that creates an escalation record."""
    agent = escalated_agent()
    r = agent.resolve("Return ORD-1021 please", "C-001")
    done = agent.review(r.case_id, "escalate")
    assert done.review_status == "escalated" and done.escalation.escalation_id.startswith("ESC-")
    assert (tmp_path / "esc.jsonl").exists()


def test_review_rejects_bad_requests():
    """Review refuses an unknown action, a blank edit, an unknown case and a repeat review."""
    from app.agent.graph import ReviewError

    agent = escalated_agent()
    r = agent.resolve("Return ORD-1021 please", "C-001")
    with pytest.raises(ReviewError):
        agent.review(r.case_id, "delete")
    with pytest.raises(ReviewError):
        agent.review(r.case_id, "edit", edited_reply="  ")
    with pytest.raises(ReviewError):
        agent.review("CASE-NOPE", "approve")
    agent.review(r.case_id, "approve")
    with pytest.raises(ReviewError):  # A case can be reviewed only once.
        agent.review(r.case_id, "approve")


def test_clarifying_question_needs_no_review():
    """A question back to the customer is not a decision, so no human review is needed."""
    r = run(FakeLLM(und("return", None)))
    assert r.review_status == "not_required"


def test_follow_up_answer_uses_case_memory():
    """Turn 1 asks for the order number; turn 2 only says 'ORD-1001' and keeps the intent.

    The agent remembers the earlier message, so the customer does not have to repeat it.
    """
    llm = FakeLLM([und("return", None), und("other", "ORD-1001")], lambda c, n: draft("refund"))
    agent = Agent(llm)
    first = agent.resolve("I want to return something", "C-001", session_id="s1")
    assert first.recommendation == "need_info"
    second = agent.resolve("It's ORD-1001", "C-001", session_id="s1")
    assert second.intent == "return" and second.recommendation == "refund"
    assert "I want to return something" in llm.understand_inputs[1]  # History is passed along.


def test_order_id_is_remembered_across_turns():
    """Once the customer gives an order id, later turns reuse it without asking again."""
    llm = FakeLLM(
        [und("return", "ORD-1001"), und("refund_status", None)], lambda c, n: draft("inform")
    )
    agent = Agent(llm)
    agent.resolve("Return ORD-1001", "C-001", session_id="s2")
    r = agent.resolve("And when will I get my money?", "C-001", session_id="s2")
    assert r.tool_trace[0]["args"]["order_id"] == "ORD-1001"


def test_memory_is_per_customer_and_expires():
    """Memory is private to one customer and is forgotten after the time limit.

    The same session id used by another customer must never see this customer's data.
    A fake clock lets the test jump 30 minutes ahead instantly.
    """
    from app.agent.memory import SessionStore

    now = [0.0]
    store = SessionStore(ttl_seconds=1800, clock=lambda: now[0])
    s = store.get("a", "C-001")
    store.add_turn(s, "customer", "hello")
    s.case.order_id = "ORD-1001"
    assert store.get("a", "C-001").case.order_id == "ORD-1001"
    assert store.get("a", "C-002").case.order_id is None  # Same session id, other customer.
    s = store.get("a", "C-001")
    s.case.order_id = "ORD-1001"
    now[0] = 1801
    assert store.get("a", "C-001").case.order_id is None  # Expired after 30 minutes.


def test_memory_keeps_only_six_turns():
    """Only the last six turns are kept, so prompts stay small and cheap."""
    from app.agent.memory import SessionStore

    store = SessionStore()
    s = store.get("a", "C-001")
    for i in range(10):
        store.add_turn(s, "customer", f"m{i}")
    assert [t[1] for t in s.turns] == [f"m{i}" for i in range(4, 10)]


@pytest.mark.parametrize("text", ["", "   ", "\n\t", "?????!!!", "12345 67890", "x" * 1501])
def test_bad_input_gets_friendly_message_and_no_llm_call(text):
    """Empty, meaningless or too-long input gets a friendly reply, and the LLM is never called.

    This saves cost and avoids sending junk to the model.
    """
    llm = FakeLLM(und("return", "ORD-1001"))
    r = Agent(llm).resolve(text, "C-001")
    assert r.recommendation == "need_info" and r.clarifying_question
    assert llm.understand_inputs == []


def test_all_order_tools_disabled_answers_from_policy_with_notice(monkeypatch):
    """If the order tools are down, the agent escalates, says it could not verify the order,
    and still shows general policy."""
    monkeypatch.setenv("FAULT_INJECTION", "lookup_order:disabled")
    r = run(FakeLLM(und("return", "ORD-1001")))
    assert r.recommendation == "escalate" and r.escalation.reason_code == "tool_failure"
    assert any("Could not verify the order" in n for n in r.notices)
    assert r.policy_citations  # General policy is still shown.
    assert r.amount is None


def test_policy_gap_escalates_instead_of_guessing():
    """If no policy covers the question, the agent escalates rather than make up an answer."""
    llm = FakeLLM(und("policy_question"), lambda c, n: draft("inform", covered=False))
    r = run(llm)
    assert r.recommendation == "escalate" and r.escalation.reason_code == "policy_gap"
    assert "isn't covered" in r.customer_reply_draft


def test_vector_store_down_escalates_policy_question(monkeypatch):
    """If policy search fails, a policy question is escalated instead of answered from nothing."""
    monkeypatch.setenv("FAULT_INJECTION", "search_policy:timeout")
    r = run(FakeLLM(und("policy_question")))
    assert r.recommendation == "escalate" and r.escalation.reason_code == "tool_failure"


def test_overall_deadline_returns_safe_answer(tmp_path):
    """If a request takes longer than its time limit, the agent returns a safe escalation."""
    import time

    class Slow(FakeLLM):
        """Fake LLM that sleeps first, to simulate a slow model."""

        def parse(self, system, user, schema):
            """Wait one second (longer than the 0.2 s limit), then answer as normal."""
            time.sleep(1.0)
            return super().parse(system, user, schema)

    r = Agent(Slow(und("return", "ORD-1001")), timeout=0.2).resolve("Return ORD-1001", "C-001")
    assert r.recommendation == "escalate" and r.escalation is not None


def test_every_log_line_of_a_request_shares_one_request_id(capsys):
    """All log lines of one request share one request id, so support can follow the request."""
    refund_agent().resolve("Return ORD-1001 please", "C-001")
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    ids = {x["request_id"] for x in lines if "request_id" in x}
    assert len(ids) == 1 and any(x["event"] == "stage_done" for x in lines)
    assert any(x["event"] == "request_done" and "latency_ms" in x for x in lines)
