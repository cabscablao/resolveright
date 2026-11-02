"""Run scripted scenarios through the real agent and score them (tool metrics, prompt metrics).

This is a library used by eval/eval_tools.py and eval/compare_prompts.py. You do not run it
directly. It reads eval/tool_scenarios.jsonl and returns result rows (callers save them).
A "scenario" is a fixed customer message plus what a correct outcome looks like.

A scenario passes the *tool check* when every expected tool was called and no forbidden tool was.
It *completes the task* when the recommendation is one of the expected ones and every extra
constraint holds (amount, approval level, citations, urgency, wording rules).
The per-run quality columns used by the prompt comparison are computed here too.
"""

import json
import os
import re
import time

from app import cases, config
from app.agent.graph import Agent
from app.agent.limits import RequestLimiter
from app.agent.llm import OpenAILLM
from app.tools import orders, policy_search
from eval.common import RecordingLLM, estimate_cost, load_jsonl, mean

# Fixed by policy text: the two return fees, the agent limit and the free-shipping threshold.
KNOWN_AMOUNTS = {
    config.RETURN_FEE_METRO_MANILA,
    config.RETURN_FEE_OUTSIDE_METRO_MANILA,
    config.AGENT_APPROVAL_LIMIT,
    config.FREE_SHIPPING_MIN,
    config.STANDARD_SHIPPING_FEE,
}
# Matches a money amount in text, such as "₱12", "₱ 1,200", "PHP 6.95" or a stray "$50".
# Group 1 is the number.
MONEY = re.compile(r"(?:₱|PHP|Php|\$)\s?(\d[\d,]*(?:\.\d{1,2})?)")


def _case_json(user: str) -> dict:
    """Extract the case-information JSON that the agent put inside a draft prompt.

    Args:
        user: The full user prompt text sent to the model.

    Returns:
        The parsed JSON dict, or an empty dict if the markers are missing or the JSON is broken.
    """
    try:
        return json.loads(user.split("Case information (JSON):\n")[1].split("\n\nYour previous")[0])
    except (IndexError, json.JSONDecodeError):
        return {}


def _numbers(text: str) -> set[float]:
    """Find every number in a text.

    Args:
        text: Any text.

    Returns:
        The numbers as floats, with thousands commas removed ("1,200" becomes 1200.0).
    """
    return {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def _ungrounded_amounts(rec: RecordingLLM, result) -> list[float]:
    """Find peso amounts in the written text that appear nowhere in the case information.

    Such amounts are "ungrounded": the model may have invented them.

    Args:
        rec: The recording LLM that holds the drafts.
        result: The agent's final result object.

    Returns:
        The sorted list of suspicious amounts (empty if none, or if no draft was made).
    """
    if not rec.drafts:
        return []
    user, _ = rec.drafts[-1]
    # Allowed numbers come from the case information only. Text after the "rejected" marker is
    # feedback to the model about its earlier answer, so it is cut off.
    allowed = _numbers(user.split("Your previous answer was rejected")[0]) | KNOWN_AMOUNTS
    allowed |= {round(a * 1.1, 2) for a in list(allowed)}  # store credit carries a 10% bonus
    text = " ".join([result.customer_reply_draft, result.rationale, result.proposed_action or ""])
    found = {float(m.replace(",", "")) for m in MONEY.findall(text)}
    return sorted(a for a in found if a not in allowed)


def _invalid_citations(rec: RecordingLLM) -> int:
    """Count citations the model wrote that point to a chunk it was not given.

    Args:
        rec: The recording LLM that holds the drafts.

    Returns:
        The number of invalid citations in the last draft (0 if there was no draft).
    """
    if not rec.drafts:
        return 0
    user, draft = rec.drafts[-1]
    given = {c["chunk_id"] for c in _case_json(user).get("policy_chunks", [])}
    return sum(1 for c in draft.policy_citations if c.chunk_id not in given)


def _check(sc: dict, result, tools: list[str]) -> tuple[bool, bool, list[str]]:
    """Check one result against the scenario's expectations.

    Args:
        sc: The scenario dict (expected tools, expected recommendations, extra constraints).
        result: The agent's final result object.
        tools: Names of the tools the agent actually called.

    Returns:
        A tuple (tool_ok, task_ok, failures). `failures` lists a plain-English reason for each miss.
    """
    # --- Step 1: tool check. Every expected tool called, no forbidden tool called ---
    fails = []
    missing = [t for t in sc["expected_tools"] if t not in tools]
    forbidden = [t for t in sc["forbidden_tools"] if t in tools]
    if missing:
        fails.append(f"missing tools {missing}")
    if forbidden:
        fails.append(f"forbidden tools {forbidden}")
    tool_ok = not missing and not forbidden

    # --- Step 2: task check. Each optional rule is applied only if the scenario defines it ---
    task = []
    if result.recommendation not in sc["expected_recommendations"]:
        task.append(
            f"recommendation {result.recommendation} not in {sc['expected_recommendations']}"
        )
    # A tolerance of 0.01 (one cent) avoids false failures from float rounding.
    if "amount" in sc and (result.amount is None or abs(result.amount - sc["amount"]) > 0.01):
        task.append(f"amount {result.amount} != {sc['amount']}")
    if sc.get("approval") and result.approval_level != sc["approval"]:
        task.append(f"approval {result.approval_level} != {sc['approval']}")
    if sc.get("citations") and not result.policy_citations:
        task.append("no policy citation")
    if sc.get("cites_docs_any") and not {c.doc_id for c in result.policy_citations} & set(
        sc["cites_docs_any"]
    ):
        task.append(f"no citation of {sc['cites_docs_any']}")
    if sc.get("urgent") and not (result.escalation and result.escalation.urgency == "urgent"):
        task.append("not urgent")
    reply = result.customer_reply_draft.lower()
    if sc.get("reply_contains_any") and not any(w in reply for w in sc["reply_contains_any"]):
        task.append(f"reply lacks any of {sc['reply_contains_any']}")
    for bad in sc.get("reply_must_not_contain", []):
        if bad.lower() in (reply + result.rationale.lower()):
            task.append(f"reply reveals '{bad}'")
    if sc.get("notice_contains") and not any(
        sc["notice_contains"] in n.lower() for n in result.notices
    ):
        task.append("missing notice")
    # Safety rule for every scenario: a person must take every case that policy escalates, and
    # no other case may claim it needs one.
    if result.requires_human_approval != (result.recommendation == "escalate"):
        task.append("requires_human_approval does not match the recommendation")
    # D50: only an in-policy, non-escalated result may be executed.
    if result.execution and (result.recommendation == "escalate" or result.escalation):
        task.append("an escalated case was executed")
    return tool_ok, not task, fails + task


def run_one(sc: dict, prompt_version: str, llm: OpenAILLM) -> dict:
    """Run one scenario through the agent and build its result row.

    Args:
        sc: The scenario dict.
        prompt_version: Which draft prompt to use, for example "v1_baseline".
        llm: The real model wrapper (shared, so token usage is counted across scenarios).

    Returns:
        A dict with the outcome, checks, token counts and latency.
    """
    # --- Step 1: set up the run ---
    config.PROMPT_VERSION = prompt_version
    # Every scenario starts from the unchanged data, so an earlier executed refund on the same
    # order cannot change the next scenario.
    orders.write_state({"orders": {}, "customers": {}})
    config.CASES_FILE.unlink(missing_ok=True)
    rec = RecordingLLM(llm)
    # Snapshot the token counters so we can subtract them later (the counters only grow).
    before = dict(llm.usage)
    # Fault injection is a test switch that makes a tool fail on purpose.
    os.environ["FAULT_INJECTION"] = sc.get("fault", "")
    # The huge limits (10**6) switch the rate limiter off so it cannot disturb the test.
    agent = Agent(rec, limiter=RequestLimiter(per_minute=10**6, daily_budget=10**6))

    # --- Step 2: run the agent and time it ---
    start = time.perf_counter()
    try:
        result = cases.process_message(agent, sc["message"], sc["customer"])
    finally:
        # Always clear the fault switch so it cannot leak into the next scenario.
        os.environ["FAULT_INJECTION"] = ""
    latency = round(time.perf_counter() - start, 2)

    # --- Step 3: score the result ---
    tools = [t["tool"] for t in result.tool_trace]
    tool_ok, task_ok, fails = _check(sc, result, tools)
    # D51: both paths must end with the right case status (escalated waits for staff, everything
    # else is completed by the chatbot).
    case = cases.get_case(result.case_id) or {}
    want = ("escalated", None) if result.recommendation == "escalate" else ("completed", "chatbot")
    if (case.get("status"), case.get("resolved_by")) != want:
        task_ok = False
        fails = [
            *fails,
            f"case status {case.get('status')} / {case.get('resolved_by')}, wanted {want}",
        ]
    invalid = _invalid_citations(rec)
    ungrounded = _ungrounded_amounts(rec, result)
    ptok = llm.usage["prompt_tokens"] - before["prompt_tokens"]
    ctok = llm.usage["completion_tokens"] - before["completion_tokens"]
    return {
        "id": sc["id"],
        "kind": sc.get("kind", "tool"),
        "mvp": sc.get("mvp", False),
        "designed_failure": sc.get("designed_failure", False),
        "message": sc["message"],
        "prompt_version": prompt_version,
        "recommendation": result.recommendation,
        "expected": sc["expected_recommendations"],
        "amount": result.amount,
        "tools_called": tools,
        "tool_ok": tool_ok,
        "task_ok": task_ok,
        "failures": fails,
        "guardrail_status": result.guardrail_status,
        "invalid_citations": invalid,
        "ungrounded_amounts": ungrounded,
        "citations": [c.chunk_id for c in result.policy_citations],
        "rationale": result.rationale,
        "reply": result.customer_reply_draft,
        "notices": result.notices,
        "latency_s": latency,
        "prompt_tokens": ptok,
        "completion_tokens": ctok,
        "llm_calls": llm.usage["calls"] - before["calls"],
    }


def run_set(
    prompt_version: str, kinds=("tool", "policy"), only: list[str] | None = None
) -> list[dict]:
    """Run the selected scenarios with one prompt version.

    They run one after another, not in parallel, because fault injection is a global setting.

    Args:
        prompt_version: Which draft prompt to use.
        kinds: Which scenario kinds to include ("tool" and/or "policy").
        only: Optional list of scenario ids; if given, run just those.

    Returns:
        One result row per scenario (see run_one).
    """
    # Write to separate eval log files so the test run does not pollute real logs.
    config.ESCALATION_LOG = config.ROOT_DIR / "logs" / "eval_escalations.jsonl"
    config.AUDIT_LOG = config.ROOT_DIR / "logs" / "eval_audit.jsonl"
    config.RESOLUTION_LOG = config.ROOT_DIR / "logs" / "eval_resolutions.jsonl"
    config.ORDER_STATE_FILE = config.ROOT_DIR / "logs" / "eval_order_state.json"
    config.CASES_FILE = config.ROOT_DIR / "logs" / "eval_cases.json"
    policy_search.warm_up()
    llm = OpenAILLM()
    scenarios = [s for s in load_jsonl("tool_scenarios.jsonl") if s.get("kind", "tool") in kinds]
    if only:
        scenarios = [s for s in scenarios if s["id"] in only]
    rows = []
    for sc in scenarios:
        row = run_one(sc, prompt_version, llm)
        rows.append(row)
        flag = "ok  " if row["task_ok"] and row["tool_ok"] else "MISS"
        print(
            f"{flag} {row['id']} {prompt_version}: {row['recommendation']} {row['failures'] or ''}"
        )
    return rows


def summarize(rows: list[dict], model: str | None = None) -> dict:
    """Aggregate metrics for one prompt version.

    Args:
        rows: Result rows from run_set.
        model: Model name used for the cost estimate (default: the configured LLM_MODEL).

    Returns:
        A dict of rates (0..1), counts, mean latency, mean tokens and estimated cost in USD.
    """
    model = model or config.LLM_MODEL

    def rate(subset, key):
        """Share of rows in `subset` whose `key` is truthy, or None for an empty subset."""
        return round(sum(1 for r in subset if r[key]) / len(subset), 3) if subset else None

    tools = [r for r in rows if r["kind"] == "tool"]
    drafted = [r for r in rows if r["llm_calls"] > 1]  # understand + at least one draft call
    return {
        "n": len(rows),
        "tool_selection_accuracy": rate(tools, "tool_ok"),
        "task_completion_tools": rate(tools, "task_ok"),
        "task_completion_policy": rate([r for r in rows if r["kind"] == "policy"], "task_ok"),
        "task_completion_all": rate(rows, "task_ok"),
        "designed_failure_tool_accuracy": rate(
            [r for r in tools if r["designed_failure"]], "tool_ok"
        ),
        "designed_failure_task_completion": rate(
            [r for r in tools if r["designed_failure"]], "task_ok"
        ),
        "mvp_task_completion": rate([r for r in tools if r["mvp"]], "task_ok"),
        "first_pass_schema_ok": (
            round(sum(1 for r in drafted if r["guardrail_status"] == "passed") / len(drafted), 3)
            if drafted
            else None
        ),
        "fallbacks": sum(1 for r in rows if r["guardrail_status"] == "fallback"),
        "citation_validity": (
            round(sum(1 for r in drafted if r["invalid_citations"] == 0) / len(drafted), 3)
            if drafted
            else None
        ),
        "runs_with_ungrounded_amounts": sum(1 for r in rows if r["ungrounded_amounts"]),
        "mean_latency_s": mean([r["latency_s"] for r in rows]),
        "mean_tokens": mean([r["prompt_tokens"] + r["completion_tokens"] for r in rows]),
        "est_cost_usd": estimate_cost(
            model,
            sum(r["prompt_tokens"] for r in rows),
            sum(r["completion_tokens"] for r in rows),
        ),
    }
