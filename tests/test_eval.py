"""Offline tests for the evaluation scoring code. No LLM calls.

The eval scripts judge the agent with automatic checks (right tools, right amount, no leaks).
These tests make sure those checks themselves are correct, using hand-built results.
"""

import json

from app.agent.schemas import Citation, Draft, EscalationInfo, ResolutionResult
from eval import scenarios
from eval.common import load_jsonl


def result(rec="refund", **kw):
    """Build a valid agent result with safe defaults; tests override only what they care about."""
    base = {"rationale": "why", "customer_reply_draft": "Hi {{first_name}}, ok.", "confidence": 0.9}
    return ResolutionResult(recommendation=rec, **{**base, **kw})


def sc(**kw):
    """Build a minimal scenario dict (the test's expected outcome) with optional extra rules."""
    base = {
        "id": "X",
        "expected_tools": [],
        "forbidden_tools": [],
        "expected_recommendations": ["refund"],
    }
    return {**base, **kw}


def test_golden_set_shape():
    """The RAG golden set has the planned size and mix, and each row has the fields scoring needs.

    Unanswerable questions must have no source policy, and only those.
    """
    golden = load_jsonl("golden_rag.jsonl")
    assert len(golden) == 25
    assert sum(g["type"] == "unanswerable" for g in golden) == 5
    assert sum(g["type"] == "multi_document" for g in golden) >= 2
    for g in golden:
        assert {"question", "reference_answer", "reference_policy_ids", "type"} <= set(g)
        assert (g["type"] == "unanswerable") == (g["reference_policy_ids"] == [])


def test_scenario_file_shape():
    """The scenario file has the planned counts, so a broken edit is caught early."""
    rows = load_jsonl("tool_scenarios.jsonl")
    tools = [r for r in rows if r.get("kind", "tool") == "tool"]
    human = [r for r in rows if r.get("kind") == "human"]  # D62: human-request scenarios H01, H02
    assert len(tools) == 23 and len(human) == 2 and len(rows) - len(tools) - len(human) == 10
    assert sum(r.get("mvp", False) for r in tools) == 15
    assert {r["id"] for r in tools if r.get("designed_failure")} == {f"S{n}" for n in range(19, 24)}


def test_tool_check_flags_missing_and_forbidden_tools():
    """The check fails when a needed tool was not called or a forbidden tool was called."""
    s = sc(expected_tools=["lookup_order"], forbidden_tools=["check_eligibility"])
    assert scenarios._check(s, result(), ["lookup_order"])[0] is True
    ok, _, fails = scenarios._check(s, result(), ["check_eligibility"])
    assert not ok and any("missing" in f for f in fails) and any("forbidden" in f for f in fails)


def test_task_check_constraints():
    """The task check passes only if amount, citation and recommendation all match."""
    s = sc(amount=2750.0, citations=True, approval="agent")
    cite = [Citation(doc_id="POL-01", section="s", chunk_id="POL-01#c0")]
    assert scenarios._check(s, result(amount=2750.0, policy_citations=cite), [])[1] is True
    assert scenarios._check(s, result(amount=80.0, policy_citations=cite), [])[1] is False
    assert scenarios._check(s, result(amount=2750.0), [])[1] is False  # No citation given.
    assert scenarios._check(sc(), result("deny"), [])[1] is False  # Wrong recommendation.


def test_urgent_and_reveal_constraints():
    """Safety cases must be escalated as urgent, and the reply must not leak a forbidden phrase.

    The leak check is case-insensitive ("Table Lamp" matches "table lamp").
    """
    esc = EscalationInfo(escalation_id="E1", reason_code="safety_hazard", urgency="urgent")
    s = sc(
        expected_recommendations=["escalate"], urgent=True, reply_must_not_contain=["table lamp"]
    )
    assert scenarios._check(s, result("escalate", escalation=esc), [])[1] is True
    assert scenarios._check(s, result("escalate"), [])[1] is False
    leaky = result("escalate", escalation=esc, customer_reply_draft="Hi, your Table Lamp order")
    assert scenarios._check(s, leaky, [])[1] is False


def _recorder(case: dict, draft: Draft):
    """Fake recorder that replays one saved LLM exchange (the case JSON the LLM saw, and its draft).

    The scoring code reads this recording, so no real LLM is needed.
    """
    rec = scenarios.RecordingLLM(inner=None)
    rec.drafts.append(("Case information (JSON):\n" + json.dumps(case), draft))
    return rec


def _draft(cites=(), reply="Hi {{first_name}}"):
    """Build a valid LLM draft with optional citations and reply text."""
    return Draft(
        recommendation="refund", proposed_action=None, amount=None, rationale="r",
        policy_citations=list(cites), clarifying_question=None, customer_reply_draft=reply,
        confidence=0.9, covered_by_policy=True,
    )  # fmt: skip


def test_ungrounded_amounts_detects_invented_money():
    """Money amounts in the reply must come from the case data; invented ones are reported.

    An invented refund figure could mislead a customer.
    """
    case = {"eligibility_result": {"max_amount": 2750.0}, "order": {"price": 2950.0}}
    good = _recorder(case, _draft())
    ok = result(customer_reply_draft="Hi, a refund of ₱2,750.00 after the ₱200 fee.")
    assert scenarios._ungrounded_amounts(good, ok) == []
    bad = result(customer_reply_draft="Hi, we will refund ₱4,000.00 and add ₱300.")
    assert scenarios._ungrounded_amounts(good, bad) == [300.0, 4000.0]


def test_select_items_filters_by_id_and_rejects_unknown_ids():
    from eval.rag_eval import select_items

    golden = [{"id": "G01"}, {"id": "G02"}, {"id": "SDG03"}]
    assert [g["id"] for g in select_items(golden, "SDG03, G01")] == ["G01", "SDG03"]
    assert len(select_items(golden, "", 2)) == 2
    assert select_items(golden) == golden
    try:
        select_items(golden, "NOPE")
    except SystemExit as exc:
        assert "NOPE" in str(exc)
    else:
        raise AssertionError("an unknown id must stop the run")


def test_invalid_citations_counts_chunks_not_given():
    """A citation to a policy chunk the LLM was never shown counts as invalid (a made-up source)."""
    case = {"policy_chunks": [{"chunk_id": "POL-01#c0"}]}
    cites = [
        Citation(doc_id="POL-01", section="s", chunk_id="POL-01#c0"),
        Citation(doc_id="POL-02", section="s", chunk_id="POL-02#c9"),
    ]
    assert scenarios._invalid_citations(_recorder(case, _draft(cites))) == 1


def test_summarize_rates():
    """The summary turns per-scenario rows into the right rates and the mean token count."""

    def row(kind, tool_ok, task_ok, calls=2, status="passed", invalid=0):
        """Build one scenario result row with fixed latency and token counts."""
        return {
            "kind": kind, "mvp": False, "designed_failure": False, "tool_ok": tool_ok,
            "task_ok": task_ok, "llm_calls": calls, "guardrail_status": status,
            "invalid_citations": invalid, "ungrounded_amounts": [], "latency_s": 2.0,
            "prompt_tokens": 100, "completion_tokens": 50,
        }  # fmt: skip

    s = scenarios.summarize(
        [
            row("tool", True, True),
            row("tool", False, True, status="repaired"),
            row("policy", True, False),
        ],
        model="gpt-4o-mini",
    )
    assert s["tool_selection_accuracy"] == 0.5
    assert s["task_completion_all"] == 0.667
    assert s["first_pass_schema_ok"] == 0.667
    assert s["mean_tokens"] == 150.0
