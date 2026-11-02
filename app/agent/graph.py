"""LangGraph orchestrator: the brain that turns a customer message into a recommendation.

Where it sits: the API calls `Agent.resolve()` for each customer message and `Agent.review()`
when a human approves, edits or escalates the draft. Everything between those two calls
happens in this file.

LangGraph models the work as a graph. A node is one step (a Python function) and an edge is
the arrow to the next step. All nodes share one `state` dict that each node adds to.

    understand -> clarify | flag | gather -> decide -> draft -> execute -> review

    understand  LLM reads the message (intent, order id, warning flags).
    clarify     Ask the customer for a missing fact.
    flag        Hand over to a human (fraud, safety...).
    gather      Call the tools: order lookup, eligibility rules, policy search.
    decide      If the rules say "escalate", stop here.
    draft       LLM writes the reply, then output guardrails check it.
    execute     Carries out an approved in-policy resolution (refund, replacement, store credit,
                cancellation); never runs for an escalation or a flagged case.
    review      Human gate, only for cases policy keeps for a person (recommendation
                "escalate"). `interrupt` pauses the graph until a person responds.

The deterministic `check_eligibility` result is authoritative. The LLM only understands the
message and drafts the wording; the output check forces the draft to agree with the engine.
Every tool returns ToolOk/ToolError, so no node can crash on a tool failure.
A result that stays inside policy and agent authority is carried out by the agent itself (the
`execute` node calls the mock `execute_resolution` tool) and needs no human decision. Only a
policy escalation (POL-10) or a policy gap goes to a person, with a structured hand-off payload,
and it is never executed; see ResolutionResult.requires_human_approval.
"""

import contextvars
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app import config
from app.agent.limits import LimitExceeded, RequestLimiter
from app.agent.llm import LLMError
from app.agent.memory import SessionStore
from app.agent.schemas import (
    Draft,
    EscalationInfo,
    ExecutionInfo,
    HandoffCustomer,
    HandoffPayload,
    HandoffReason,
    HandoffRequest,
    ResolutionResult,
    Understanding,
)
from app.agent.validation import validate_message
from app.cache.llm_cache import make_llm
from app.cache.semantic import cacheable_message, get_semantic_cache
from app.guardrails.hub import get_hub
from app.guardrails.input_rules import (
    HUMAN_WORDS,
    SAFETY_WORDS,
    check_input,
    untrusted_text_rule,
)
from app.guardrails.output_rules import OutputGuard
from app.guardrails.types import GuardEvent, InputVerdict
from app.observability import tracing
from app.observability.logging import log_event, request_id_var
from app.tools.orders import get_customer
from app.tools.registry import TOOLS
from app.tools.resolution import EXECUTABLE_ACTIONS
from app.tools.results import ToolError

# Intents that are about a specific order, so they need an order id and the order tools.
ORDER_INTENTS = {
    "return", "damaged", "defective", "wrong_item", "late", "lost", "cancel",
    "change_address", "warranty", "price_adjustment", "refund_status",
}  # fmt: skip
# Recommendations that move money; only these may carry an amount.
MONEY_RECOMMENDATIONS = {"refund", "refund_partial", "replacement", "store_credit", "cancel_order"}
# The three things a human reviewer can do with an escalated case.
REVIEW_ACTIONS = ("approve", "edit", "escalate")
# Escalation codes that are a technical fallback, not a policy reason (everything else is POL-10
# or another policy; "policy_gap" means no policy covers the case).
SYSTEM_REASONS = {"tool_failure", "low_confidence"}
# The policies behind each escalation code, listed in the hand-off when the rules engine gave none.
REASON_POLICIES = {
    "fraud": ["POL-10"], "legal_threat": ["POL-10"], "safety_hazard": ["POL-04", "POL-10"],
    "harassment": ["POL-10"], "human_requested": ["POL-10"], "chargeback_open": ["POL-10"],
    "refund_frequency": ["POL-10"], "above_agent_limit": ["POL-02", "POL-10"],
    "late_damage_claim": ["POL-04", "POL-10"], "policy_gap": ["POL-10"],
}  # fmt: skip
# What the customer is told once an action has been carried out, per action; {amount} is money.
EXECUTED_SENTENCES = {
    "refund": "Your refund of {amount} has been processed.",
    "refund_partial": "A partial refund of {amount} has been processed.",
    "replacement": "Your replacement has been arranged (value {amount}).",
    "store_credit": "Store credit of {amount} has been added to your account.",
    "cancel_order": "Your order has been cancelled and {amount} will be refunded.",
}
# Placeholder the LLM writes instead of a name; render_reply() fills it in later.
FIRST_NAME = "{{first_name}}"
# Who made a personalisation mistake (a typo in an engraving, say). The engine trusts this value,
# so we only accept it when the customer's own words match one of these patterns (a regex is a
# text pattern; \b means "word boundary", so "i typed" does not match inside another word).
ERROR_CUES = {
    "customer": re.compile(
        r"\bi (typed|entered|wrote|spelled|spelt|misspelled|made|put|chose|gave)\b|"
        r"\bmy (mistake|fault|error|typo|bad)\b|\bmy own\b",
        re.IGNORECASE,
    ),
    "brightcart": re.compile(
        r"\byou (printed|engraved|spelled|spelt|misspelled|made|got|put|used|sent)\b|"
        r"\b(your|brightcart'?s) (mistake|fault|error)\b|\b(printed|engraved) (it )?(wrong|incorrectly|differently)\b|"
        r"\bnot what i (typed|entered|ordered|wrote)\b|\bdifferent (name|text|spelling)\b",
        re.IGNORECASE,
    ),
}
# A human request with no reason stays that: no order, item or intent is guessed for it.
NO_REASON_SUMMARY = "Customer asked to talk to a human; no reason given."
# Shown to the LLM instead of an order note that looks like a prompt-injection attempt.
NOTE_WITHHELD = "[order note withheld: it contained instruction-like text]"


_FILLER = {
    "i", "want", "wanna", "to", "please", "pls", "can", "could", "would", "may", "like", "need", "a",
    "an", "the", "with", "me", "my", "now", "you", "get", "give", "let", "have", "some", "of", "for",
    "right", "asap", "thanks", "thank", "hi", "hello", "hey", "yes", "ok", "okay", "again", "just",
}  # fmt: skip


def _human_summary(message: str, order_id: str | None) -> str:
    """One neutral line for staff about a request for a person, from this message only.

    With no reason in the message the line says so; nothing is guessed (owner rule, D62). When the
    customer did give a reason, their own (already PII-masked) words are quoted.
    """
    rest = HUMAN_WORDS.sub(" ", message)
    words = [w for w in re.findall(r"[a-z0-9'-]+", rest.lower()) if w not in _FILLER]
    if order_id and order_id.lower() in words:
        words.remove(order_id.lower())
    if not words:
        return NO_REASON_SUMMARY
    return f"Customer asked to talk to a human. Their words: {message.strip()[:300]}"


class ReviewError(Exception):
    """The review request is invalid (unknown case, already reviewed, bad action)."""


class AgentState(TypedDict, total=False):
    """The shared dict passed from node to node. `total=False` means every key is optional.

    Each node returns only the keys it changes; LangGraph merges them into the state.
    """

    message: str
    customer_id: str
    context: str
    case: dict
    understanding: Understanding
    order: dict | None
    eligibility: dict | None
    chunks: list[dict]
    trace: list[dict]
    result: ResolutionResult
    notices: list[str]
    guard_events: list[dict]


def _record(state: AgentState, event: GuardEvent) -> None:
    """Log a guardrail firing and keep it on the result for the UI badge and the evidence logs."""
    event.log()
    state["guard_events"].append(event.as_dict())


def _load_prompt(name: str) -> str:
    """Read a prompt template from the prompts folder (name without the .md extension)."""
    return (config.ROOT_DIR / "prompts" / f"{name}.md").read_text(encoding="utf-8").strip()


def _timed(stage: str, fn):
    """Wrap a node function so its run time is logged.

    Args:
        stage: Name used in the log line.
        fn: The node function to wrap.

    Returns:
        A function with the same inputs and outputs as `fn`.
    """

    def run(state):
        start = time.perf_counter()
        out = fn(state)
        log_event("stage_done", stage=stage, latency_ms=round((time.perf_counter() - start) * 1000))
        return out

    return run


def _call(state: AgentState, tool: str, **kwargs) -> Any:
    """Run a tool, retry once on timeout, and record it in the visible tool trace.

    Args:
        state: The graph state; its "trace" list gets one entry per call.
        tool: Name in the TOOLS registry.
        **kwargs: Arguments passed to the tool.

    Returns:
        The tool's ToolOk or ToolError.
    """
    result = TOOLS[tool](**kwargs)
    # A timeout is often a one-off, so try exactly once more. Other errors will not change.
    if isinstance(result, ToolError) and result.code == "timeout":
        log_event("tool_retry", tool=tool, error=result.code)
        result = TOOLS[tool](**kwargs)
    entry = {"tool": tool, "args": kwargs, "ok": result.ok}
    if isinstance(result, ToolError):
        entry["error"] = result.code
    state["trace"].append(entry)
    log_event("tool_call", tool=tool, status="ok" if result.ok else result.code)
    return result


def _policies_for(state: AgentState, reason: str) -> list[str]:
    """Policies behind an escalation: the rules engine's ids plus the ones tied to the reason."""
    elig = state.get("eligibility") or {}
    return sorted(set(elig.get("policy_ids", [])) | set(REASON_POLICIES.get(reason, [])))


def _facts(state: AgentState, elig: dict | None) -> list[str]:
    """Plain list of the order facts and rule results gathered so far (for the reviewer)."""
    facts = []
    if state.get("order"):
        facts = [f"order {state['order']['order_id']}: status {state['order']['status']}"]
    if elig:
        facts.append("eligibility: " + ", ".join(elig["reason_codes"]))
    return facts


def _build_handoff(
    info: EscalationInfo | None,
    customer_id: str,
    explanation: str,
    *,
    intent: str = "other",
    order_id: str | None = None,
    item_sku: str | None = None,
    summary: str = "",
    policies: list[str] | None = None,
    facts: list[str] | None = None,
) -> HandoffPayload | None:
    """Build the structured hand-off for staff: who, what they wanted, and why it escalates.

    Args:
        info: The recorded escalation; without one there is nothing to hand over.
        customer_id: The customer's id. The first name is added; no email, address or phone.
        explanation: Plain sentence on why the case goes to a person.
        intent: What the customer wanted (an Intent value).
        order_id: The order the case is about, if any.
        item_sku: The item, if the request was about one item.
        summary: Neutral one-line summary of the request (no personal data).
        policies: Policy ids that triggered or apply.
        facts: Order facts and rule results already gathered.

    Returns:
        A HandoffPayload, or None when `info` is None. `executed` is always False.
    """
    if info is None:
        return None
    code = info.reason_code
    kind = "system_fallback" if code in SYSTEM_REASONS else (
        "policy_gap" if code == "policy_gap" else "policy"
    )  # fmt: skip
    return HandoffPayload(
        handoff_id=info.escalation_id,
        customer=HandoffCustomer(
            customer_id=customer_id,
            first_name=(get_customer(customer_id) or {}).get("first_name"),
        ),
        request=HandoffRequest(
            intent=intent, order_id=order_id, item_sku=item_sku, summary=summary
        ),
        reason=HandoffReason(reason_code=code, reason_type=kind, explanation=explanation),
        policies=sorted(set(policies or [])),
        urgency=info.urgency,
        facts_found=facts or [],
        executed=False,
        actions_taken=[],
        ts=datetime.now(UTC).isoformat(),
    )


def _escalate(state: AgentState, reason: str, urgency: str, summary: str) -> EscalationInfo | None:
    """Record an escalation through the tool; return its info, or None if the tool failed.

    Args:
        state: The graph state.
        reason: An escalation reason code.
        urgency: "normal" or "urgent".
        summary: Short case summary (must not contain personal data).
    """
    u = state.get("understanding")
    res = _call(
        state, "escalate_to_human", case_summary=summary, reason_code=reason, urgency=urgency,
        customer_id=state.get("customer_id"), order_id=u.order_id if u else None,
        policy_ids=_policies_for(state, reason),
    )  # fmt: skip
    if isinstance(res, ToolError):
        return None
    return EscalationInfo(
        escalation_id=res.data["escalation_id"], reason_code=reason, urgency=urgency
    )


def _terminal(state: AgentState, rec: str, rationale: str, reply: str, **kw) -> ResolutionResult:
    """Build a final result for a case that ends without an LLM draft ("terminal" = last step).

    Args:
        state: The graph state (supplies intent, trace, notices and guard events).
        rec: The recommendation, for example "need_info" or "escalate".
        rationale: Why, in one sentence for the human reviewer.
        reply: Customer-facing text.
        **kw: Extra ResolutionResult fields.
    """
    return ResolutionResult(
        recommendation=rec,
        rationale=rationale,
        customer_reply_draft=reply,
        intent=state["understanding"].intent if state.get("understanding") else "other",
        tool_trace=state["trace"],
        prompt_version=config.PROMPT_VERSION,
        notices=state.get("notices", []),
        guardrail_events=state.get("guard_events", []),
        **kw,
    )


def _escalation_result(state, reason, urgency, rationale, reply=None, **kw) -> ResolutionResult:
    """Record an escalation and build the result that hands the case to a human.

    Args:
        state: The graph state.
        reason: Escalation reason code.
        urgency: "normal" or "urgent".
        rationale: Why we escalate; also goes into the case summary.
        reply: Customer text; a polite default is used when omitted.
        **kw: Extra ResolutionResult fields (for example policy_citations).
    """
    u = state.get("understanding")
    order_id = u.order_id if u else None
    summary = f"Intent: {u.intent if u else 'unknown'}. Order: {order_id or 'n/a'}. {rationale}"
    info = _escalate(state, reason, urgency, summary)
    reply = reply or (
        f"Hi {FIRST_NAME}, thank you for reaching out. I've passed your case to a specialist "
        "on our team who will review it and follow up with you. I'm sorry for the wait."
    )
    handoff = _build_handoff(
        info, state.get("customer_id", ""), rationale,
        intent=u.intent if u else "other", order_id=order_id,
        item_sku=u.item_sku if u else None, summary=u.summary if u else "",
        policies=_policies_for(state, reason), facts=_facts(state, state.get("eligibility")),
    )  # fmt: skip
    return _terminal(
        state, "escalate", rationale, reply, escalation=info, handoff=handoff,
        approval_level="manager", proposed_action="Hand the case to a human specialist.",
        confidence=1.0, **kw,
    )  # fmt: skip


def _order_error(state: AgentState, err: ToolError) -> ResolutionResult:
    """Turn a failed order lookup into a safe result. Each error type has its own response."""
    # Case 1: no such order on this account. This also covers someone else's order (the tool
    # returns the same "not_found", D55), so the reply never says whether the id exists.
    if err.code in ("not_found", "invalid_input"):
        q = (
            "I couldn't find that order on your account. Please check the order number, or I can "
            "help with one of your other orders."
        )
        return _terminal(
            state, "need_info", f"Order lookup failed: {err.code}.",
            f"Hi {FIRST_NAME}, {q}", clarifying_question=q, confidence=1.0,
        )  # fmt: skip
    # Case 2: order system down. Answer from policy only, say so, and recommend a human.
    state["notices"] = ["Could not verify the order. Nothing below is based on order data."]
    pol = _call(state, "search_policy", query=state["understanding"].summary)
    # Only the top 2 chunks: this is just context for the reviewer, not a full answer.
    chunks = [] if isinstance(pol, ToolError) else pol.data["chunks"][:2]
    state["chunks"] = chunks
    cites = [{"doc_id": c["doc_id"], "section": c["section"], "chunk_id": c["chunk_id"]}
             for c in chunks]  # fmt: skip
    topics = ", ".join(sorted({c["title"] for c in chunks})) or "our policies"
    res = _escalation_result(
        state, "tool_failure", "normal",
        f"Order lookup failed ({err.code}); order not verified. Relevant policy: {topics}.",
        reply=(
            f"Hi {FIRST_NAME}, I'm sorry, I can't check your order right now, so I can't confirm "
            "what applies to it. I've asked a specialist to follow up with you."
        ),
        policy_citations=cites,
    )  # fmt: skip
    return res


def _check(d: Draft, allowed: list[str]) -> list[str]:
    """Check the LLM draft against basic rules and return a list of problems (empty is good).

    Args:
        d: The draft from the LLM.
        allowed: Recommendations the eligibility engine permits.
    """
    problems = []
    if d.recommendation not in allowed:
        problems.append(f"recommendation '{d.recommendation}' is not one of {allowed}")
    if not d.customer_reply_draft.strip():
        problems.append("customer_reply_draft is empty")
    if d.recommendation == "need_info" and not d.clarifying_question:
        problems.append("need_info requires a clarifying_question")
    return problems


def _finalise(state, d: Draft, elig: dict | None, status: str) -> ResolutionResult:
    """Turn an accepted LLM draft into the final ResolutionResult, applying safety clean-ups.

    Args:
        state: The graph state.
        d: The accepted draft.
        elig: The eligibility result as a dict, or None for policy-only questions.
        status: Guardrail status so far, "passed" or "repaired".
    """
    # --- Step 1: keep only citations that point at chunks we really retrieved ---
    chunk_ids = {c["chunk_id"] for c in state["chunks"]}
    citations = [c for c in d.policy_citations if c.chunk_id in chunk_ids]
    if len(citations) != len(d.policy_citations):
        log_event("citation_removed", status=f"{len(d.policy_citations) - len(citations)} invalid")
    # --- Step 2: the amount may never exceed the engine's limit, and only money
    # recommendations carry an amount at all ---
    amount = d.amount
    if elig and d.recommendation in MONEY_RECOMMENDATIONS:
        cap = elig.get("max_amount")
        if cap is not None:
            amount = cap if amount is None else min(amount, cap)
    else:
        amount = None
    # --- Step 3: list the facts we used so the reviewer can check them ---
    facts = _facts(state, elig)
    # --- Step 4: assemble the result; confidence is clamped into the 0..1 range ---
    return ResolutionResult(
        recommendation=d.recommendation,
        proposed_action=d.proposed_action,
        amount=amount,
        approval_level=elig["approval_level"] if elig else "agent",
        rationale=d.rationale,
        policy_citations=citations,
        order_facts_used=facts,
        clarifying_question=d.clarifying_question,
        customer_reply_draft=d.customer_reply_draft,
        confidence=max(0.0, min(1.0, d.confidence)),
        guardrail_status=status,
        guardrail_events=state.get("guard_events", []),
        intent=state["understanding"].intent,
        tool_trace=state["trace"],
        prompt_version=config.PROMPT_VERSION,
        notices=state.get("notices", []),
    )


def _audit(entry: dict) -> None:
    """Append one human-review decision to the audit log (an audit log is a permanent record)."""
    config.AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    # One JSON object per line, append only, so earlier entries are never rewritten.
    with config.AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def build_graph(llm, checkpointer, guard: OutputGuard | None = None):
    """Build and compile the LangGraph state machine.

    Args:
        llm: Object with `parse(system, user, schema)`; tests pass a fake.
        checkpointer: Saves the state at each step. The review gate needs this to pause and
            resume later (a checkpoint is a saved snapshot of the state).
        guard: Output guardrails; a default OutputGuard is created when omitted.

    Returns:
        The compiled graph, ready for `invoke`.
    """
    guard = guard or OutputGuard()
    understand_prompt = _load_prompt("understand")
    draft_prompt = _load_prompt(config.PROMPT_VERSION)

    # ---- nodes ----------------------------------------------------------------------

    def understand(state: AgentState) -> dict:
        """Node 1: ask the LLM what the customer wants, then correct it with hard rules."""
        # --- Step 1: ask the LLM. The tags mark the message as data, not instructions ---
        user = (
            f"{state.get('context', '')}<customer_message>\n{state['message']}\n</customer_message>"
        )
        try:
            u = llm.parse(understand_prompt, user, Understanding)
        except LLMError as exc:
            log_event("understand_failed", error=str(exc))
            return {"result": _escalation_result(
                state, "tool_failure", "normal", "The assistant could not understand the message."
            )}  # fmt: skip
        message = state["message"]
        if HUMAN_WORDS.search(message) and "human_requested" not in u.flags:
            # POL-10: a request for a person is always honoured, so the rule does not wait for the LLM.
            u.flags.append("human_requested")
            log_event("human_keyword_flag", intent=u.intent)
        case = state.get("case", {})
        if "human_requested" in u.flags:
            # --- Step 2 (human request): use only what THIS message says. Nothing is carried over
            # from earlier turns, so no order, item or reason is assumed for the customer. ---
            order_id = (u.order_id or "").strip().upper()
            u.order_id = order_id if order_id and order_id in message.upper() else None
            u.item_sku = (
                u.item_sku if u.item_sku and u.item_sku.upper() in message.upper() else None
            )
            u.error_origin = None
            if not u.order_id:
                u.intent = "other"
            u.summary = _human_summary(message, u.order_id)
        else:
            # --- Step 2: fill gaps from earlier turns of this conversation ("case" memory) ---
            if u.order_id:
                u.order_id = u.order_id.strip().upper()
            else:  # the customer may be answering a question about an order discussed earlier
                u.order_id = case.get("order_id")
            # A vague follow-up like "yes" keeps the earlier intent.
            if u.intent == "other" and case.get("intent"):
                u.intent = case["intent"]
            u.item_sku = u.item_sku or case.get("item_sku")
            # --- Step 3: do not trust the LLM on facts the rules depend on ---
            if u.error_origin and not ERROR_CUES[u.error_origin].search(message):
                # The engine trusts this value, so keep it only if the customer's own words say so.
                log_event("error_origin_dropped", status=u.error_origin)
                u.error_origin = None
            u.error_origin = u.error_origin or case.get("error_origin")
        if SAFETY_WORDS.search(state["message"]) and "safety_hazard" not in u.flags:
            # Safety must not depend on the LLM noticing it (POL-10: urgent, always escalate).
            u.flags.insert(0, "safety_hazard")
            log_event("safety_keyword_flag", intent=u.intent)
        log_event("understood", intent=u.intent)
        return {"understanding": u}

    def clarify(state: AgentState) -> dict:
        """Node 2: a needed fact is missing, so ask the customer for it (no LLM call)."""
        u = state["understanding"]
        if u.intent == "other":
            q = "Could you tell me a bit more about what you need help with?"
        else:
            q = "Could you share your order number (it looks like ORD-1234) so I can look it up?"
        reply = f"Hi {FIRST_NAME}, happy to help. {q}"
        return {"result": _terminal(
            state, "need_info", "A required fact is missing.", reply,
            clarifying_question=q, confidence=1.0,
        )}  # fmt: skip

    def flag_escalation(state: AgentState) -> dict:
        """Node 3: a warning flag was raised (fraud, safety...), so hand over to a human."""
        u = state["understanding"]
        # Safety hazards are time-critical; every other flag is normal priority.
        urgency = "urgent" if "safety_hazard" in u.flags else "normal"
        # Cite the policy behind the hand-over (for example POL-04 Safety, POL-10) so the
        # reviewer sees it; the top 2 chunks are context only, the flag alone decides.
        pol = _call(state, "search_policy", query=u.summary or state["message"])
        chunks = [] if isinstance(pol, ToolError) else pol.data["chunks"][:2]
        state["chunks"] = chunks
        cites = [{"doc_id": c["doc_id"], "section": c["section"], "chunk_id": c["chunk_id"]}
                 for c in chunks]  # fmt: skip
        return {"result": _escalation_result(
            state, u.flags[0], urgency, f"Flagged by the assistant: {', '.join(u.flags)}.",
            policy_citations=cites,
        )}  # fmt: skip

    def gather(state: AgentState) -> dict:
        """Node 4: call the tools to collect the order, the eligibility result and policy text.

        If something fails badly, it sets "result" so the graph skips straight to review.
        """
        u = state["understanding"]
        out: dict = {"order": None, "eligibility": None, "chunks": []}
        cid = state["customer_id"]
        if u.intent in ORDER_INTENTS:
            # --- Step 1: look up the order (checks ownership too) ---
            order = _call(state, "lookup_order", order_id=u.order_id, customer_id=cid)
            if isinstance(order, ToolError):
                out["result"] = _order_error(state, order)
                out["chunks"] = state.get("chunks", [])
                out["notices"] = state.get("notices", [])
                return out
            out["order"] = order.data
            # --- Step 2: screen the free-text order note for hidden instructions ---
            note = order.data.get("notes")
            if note and (rule := untrusted_text_rule(note)):
                # Indirect injection: order notes are data, never instructions (master prompt 9.1).
                _record(state, GuardEvent("tool_data", f"indirect_{rule}", "drop"))
                out["order"] = {**order.data, "notes": NOTE_WITHHELD}
            # --- Step 3: run the policy rules engine ---
            elig = _call(
                state, "check_eligibility", order_id=u.order_id, request_type=u.intent,
                customer_id=cid, item_sku=u.item_sku, error_origin=u.error_origin,
            )  # fmt: skip
            if isinstance(elig, ToolError):
                out["result"] = _escalation_result(
                    state, "tool_failure", "normal", f"Eligibility check failed: {elig.code}."
                )
                return out
            out["eligibility"] = elig.data
        # --- Step 4: search the policy documents (all intents, including policy questions) ---
        pol = _call(state, "search_policy", query=f"{u.intent.replace('_', ' ')} {u.summary}")
        if isinstance(pol, ToolError):
            # With an eligibility result we can still draft; without any source we must escalate.
            if not out["eligibility"]:
                out["result"] = _escalation_result(
                    state, "tool_failure", "normal", "Policy search was unavailable."
                )
        else:
            out["chunks"] = pol.data["chunks"]
        return out

    def decide(state: AgentState) -> dict:
        """Node 5: if the rules engine says "escalate", end the case here without a draft."""
        elig = state["eligibility"]
        if elig and elig["recommendation"] == "escalate":
            return {"result": _escalation_result(
                state, elig.get("escalation_reason") or "other", elig["urgency"],
                "Policy requires a human decision: " + ", ".join(elig["reason_codes"]) + ".",
            )}  # fmt: skip
        return {}

    def draft(state: AgentState) -> dict:
        """Node 6: the LLM writes the reply; guardrails check it; one repair try is allowed."""
        u, elig = state["understanding"], state["eligibility"]
        # The engine decides what may be recommended; without it, only safe non-money options.
        allowed = elig["allowed_recommendations"] if elig else ["inform", "need_info", "escalate"]
        # --- Step 1: build the case file the LLM sees (no names or addresses) ---
        case = {
            "customer_message": state["message"],
            "intent": u.intent,
            "order": state["order"],
            "eligibility_result": elig,
            "policy_chunks": [
                {k: c[k] for k in ("chunk_id", "doc_id", "section", "text")}
                for c in state["chunks"]
            ],
            "allowed_recommendations": allowed,
        }
        # ensure_ascii=False keeps the peso sign as "₱". With the default, "₱200.00" is sent as the
        # escape ₱200.00 and the model read the escape's last "1" as part of the amount
        # ("1200.00"). The same text is used below to check the amounts in the reply.
        case_text = json.dumps(case, indent=2, default=str, ensure_ascii=False)
        user = "Case information (JSON):\n" + case_text
        chunk_ids = {c["chunk_id"] for c in state["chunks"]}
        status = "passed"
        d = None
        # --- Step 2: draft, check, and retry once with feedback if the check fails ---
        for attempt in range(2):  # first try, then one repair
            try:
                d = llm.parse(draft_prompt, user, Draft)
            except LLMError as exc:
                log_event("draft_failed", error=str(exc))
                return {"result": _escalation_result(
                    state, "tool_failure", "normal", "The assistant could not draft an answer."
                )}  # fmt: skip
            problems = _check(d, allowed)
            content_problems, content_events = guard.content([d.customer_reply_draft, d.rationale])
            problems += content_problems
            # A money figure written without a peso sign must come from the case data (the repair
            # try tells the model which one did not).
            problems += guard.bare_amounts(
                [d.customer_reply_draft, d.rationale], case_text, d.amount
            )
            # "Grounding": every citation must refer to a chunk we really retrieved.
            ungrounded = guard.grounding(d.policy_citations, chunk_ids)
            for event in content_events:
                _record(state, event)
            if ungrounded:
                _record(state, GuardEvent("output", "grounding", "reject"))
            if not problems and not ungrounded:
                break
            if ungrounded and not problems and attempt == 1:
                break  # still mis-cited after the repair: _finalise drops those citations
            if problems:
                log_event("output_check_failed", status="; ".join(problems), attempt=attempt + 1)
            status = "repaired"
            # Tell the LLM what was wrong so the second try can fix it.
            user += (
                "\n\nYour previous answer was rejected: " + "; ".join(problems + ungrounded) + "."
            )
        else:
            # for-else: runs only when both tries failed (the loop never hit `break`).
            log_event("output_fallback")
            res = _escalation_result(
                state, "low_confidence", "normal", "The drafted answer failed the output check."
            )
            return {"result": res.model_copy(update={"guardrail_status": "fallback"})}
        # --- Step 3: a policy question the documents cannot answer goes to a human ---
        if not elig and not d.covered_by_policy:
            return {"result": _escalation_result(
                state, "policy_gap", "normal", "The question is not covered in the policy documents.",
                reply=(
                    f"Hi {FIRST_NAME}, that isn't covered in our policy documents, so I don't "
                    "want to guess. I've asked a specialist to look into it and get back to you."
                ),
            )}  # fmt: skip
        # --- Step 4: mask anything that looks like a leak, then run the soft amount check ---
        d.customer_reply_draft, leaks = guard.mask_leaks(d.customer_reply_draft)
        d.rationale, more = guard.mask_leaks(d.rationale)
        for event in leaks + more:
            _record(state, event)
        odd = guard.unsupported_amounts([d.customer_reply_draft, d.rationale], case_text, d.amount)
        if odd:  # soft check: shown to the reviewer in the logs, does not block
            _record(state, GuardEvent("output", "amount_not_in_case_data", "warn"))
        return {"result": _finalise(state, d, elig, status)}

    def execute(state: AgentState) -> dict:
        """Node 7: carry out an approved in-policy resolution (mock) before the review gate.

        Only a result inside policy and agent authority is executed. An escalation, a flagged
        case (safety, fraud, a request for a human...) or an answer the output check replaced is
        never executed. The tool re-runs the rules itself, so the draft cannot widen them.
        """
        res, u = state["result"], state["understanding"]
        if (
            not config.EXECUTION_ENABLED
            or res.recommendation not in EXECUTABLE_ACTIONS
            or res.escalation
            or u.flags
            or res.guardrail_status == "fallback"
            or not state.get("eligibility")
        ):
            return {}
        out = _call(
            state, "execute_resolution", order_id=u.order_id, customer_id=state["customer_id"],
            request_type=u.intent, action=res.recommendation, amount=res.amount,
            item_sku=u.item_sku, error_origin=u.error_origin,
        )  # fmt: skip
        trace = list(state["trace"])  # the result keeps its own copy of the tool trace
        if isinstance(out, ToolError):
            if out.code == "already_resolved":
                # A repeat request: nothing is executed twice, and nobody needs to be called.
                return {"result": res.model_copy(update={
                    "recommendation": "inform", "amount": None, "proposed_action": None,
                    "rationale": f"This request was already carried out on the order ({out.message}).",
                    "customer_reply_draft": (
                        f"Hi {FIRST_NAME}, this has already been processed on your order, so I "
                        "haven't changed anything. If something looks wrong, tell me what and "
                        "I'll take a look."
                    ),
                    "tool_trace": trace,
                })}  # fmt: skip
            # Anything else is a system fallback (not a policy reason): hand over, execute nothing.
            failed = _escalation_result(
                state, "tool_failure", "normal", f"Executing the resolution failed: {out.code}.",
                reply=(
                    f"Hi {FIRST_NAME}, I'm sorry, I couldn't complete that just now. I've asked a "
                    "specialist to follow up with you."
                ),
                policy_citations=res.policy_citations,
            )  # fmt: skip
            return {"result": failed}
        done = out.data
        sentence = EXECUTED_SENTENCES[done["action"]].format(amount=config.peso(done["amount"]))
        reply = (
            f"{res.customer_reply_draft.rstrip()}\n\n{sentence} Reference: {done['resolution_id']}."
        )
        log_event("resolution_attached", status=done["action"])
        return {"result": res.model_copy(update={
            "execution": ExecutionInfo(**done), "customer_reply_draft": reply,
            "amount": done["amount"], "tool_trace": trace,
        })}  # fmt: skip

    def review(state: AgentState) -> dict:
        """Node 8: the human gate. Pause only when policy keeps the case for a person."""
        res = state["result"]
        # Inside policy and agent authority (an answer, a question, a denial, or a resolution the
        # agent has carried out): no human decision is needed, so the graph just ends.
        if not res.requires_human_approval:
            return {}
        # `interrupt` pauses the graph and saves it in the checkpointer. Agent.review() resumes
        # it later with Command(resume=...), and the resume value comes back as `decision`.
        decision = interrupt({"recommendation": res.recommendation, "amount": res.amount})
        action, edited = decision["action"], decision.get("edited_reply")
        # Map the reviewer's action to the status shown in the UI.
        update: dict = {"review_status": {"approve": "approved", "edit": "edited",
                                          "escalate": "escalated"}[action]}  # fmt: skip
        if action == "edit":
            update["customer_reply_draft"] = edited
        if action == "escalate" and not res.escalation:
            info = _escalate(
                state, "other", "normal",
                f"Agent escalated a {res.recommendation} recommendation for intent {res.intent}.",
            )  # fmt: skip
            update["escalation"] = info
        # Audit log of the human decision. An escalated case is never executed by the agent; staff
        # carry out whatever they decide.
        _audit({
            "ts": datetime.now(UTC).isoformat(), "case_id": decision["case_id"],
            "action": action, "reviewer": decision.get("reviewer", "agent"),
            "recommendation": res.recommendation, "amount": res.amount,
            "intent": res.intent, "edited_reply_chars": len(edited) if edited else 0,
            "note": "Human decision on an escalated case. The agent executed nothing.",
        })  # fmt: skip
        log_event("review_done", review_action=action, case_id=decision["case_id"])
        return {"result": res.model_copy(update=update)}

    # ---- routing ----------------------------------------------------------------------

    def after_understand(state: AgentState) -> str:
        """Edge rule after `understand`: pick the next node by name."""
        # A result already exists when the LLM call failed, so go straight to review.
        if state.get("result"):
            return "review"
        u = state["understanding"]
        # Flags come first: safety and fraud must never be answered as a normal case.
        if u.flags:
            return "flag"
        # Nothing to look up without a clear intent or, for order intents, an order id.
        if u.intent == "other" or (u.intent in ORDER_INTENTS and not u.order_id):
            return "clarify"
        return "gather"

    def to_review_if_done(next_node: str):
        """Make an edge rule: go to review if a result is ready, else on to `next_node`."""
        return lambda state: "review" if state.get("result") else next_node

    # ---- wiring: register the nodes, then draw the edges ----
    g = StateGraph(AgentState)
    for name, fn in [
        ("understand", understand), ("clarify", clarify), ("flag", flag_escalation),
        ("gather", gather), ("decide", decide), ("draft", draft), ("execute", execute),
        ("review", review),
    ]:  # fmt: skip
        g.add_node(name, _timed(name, fn))
    g.add_edge(START, "understand")
    # Conditional edge: the function picks one of the listed next nodes.
    g.add_conditional_edges(
        "understand",
        after_understand,
        {"review": "review", "flag": "flag", "clarify": "clarify", "gather": "gather"},
    )
    g.add_conditional_edges(
        "gather", to_review_if_done("decide"), {"review": "review", "decide": "decide"}
    )
    g.add_conditional_edges(
        "decide", to_review_if_done("draft"), {"review": "review", "draft": "draft"}
    )
    # The draft goes through `execute` (which only acts on approved in-policy results) first.
    g.add_edge("draft", "execute")
    g.add_edge("execute", "review")
    # These two end at the review node directly; it pauses only for escalations.
    for node in ("clarify", "flag"):
        g.add_edge(node, "review")
    g.add_edge("review", END)
    return g.compile(checkpointer=checkpointer)


class Agent:
    """Public entry point: owns the graph, the sessions, the rate limiter and the caches.

    `resolve()` returns a ResolutionResult. When policy keeps the case for a person (the
    recommendation is "escalate"), review_status is "pending" until a human calls `review()`
    with approve, edit or escalate. Any other result is "not_required".
    """

    def __init__(
        self,
        llm=None,
        sessions: SessionStore | None = None,
        timeout=None,
        limiter: RequestLimiter | None = None,
        guard: OutputGuard | None = None,
        hub=None,
        semantic=None,
    ):
        """Wire up the agent; every argument is optional and tests override them.

        Args:
            llm: LLM object with `parse`; defaults to the cached real one.
            sessions: Conversation memory store.
            timeout: Seconds allowed per request; defaults to the configured deadline.
            limiter: Rate limiter.
            guard: Output guardrails.
            hub: Optional Guardrails AI hub; None unless enabled in config.
            semantic: Semantic cache (reuses answers to similar questions); False turns it off.
        """
        self.sessions = sessions or SessionStore()
        # `semantic=False` turns the semantic tier off; None means "as configured".
        self.semantic = get_semantic_cache() if semantic is None else (semantic or None)
        self.limiter = limiter or RequestLimiter()
        self.timeout = timeout or config.REQUEST_TIMEOUT_SECONDS
        self.hub = hub if hub is not None else get_hub()  # None unless GUARDRAILS_AI_ENABLED
        if guard is None:
            guard = OutputGuard(extra_checks=[self.hub.check_output] if self.hub else [])
        self._graph = build_graph(llm or make_llm(), MemorySaver(), guard)

    # ---- resolve ----------------------------------------------------------------------

    def resolve(
        self,
        message: str,
        customer_id: str,
        session_id: str | None = None,
        callbacks=None,
        case_id: str | None = None,
    ) -> ResolutionResult:
        """Handle one customer message and return a result (pending only if it is an escalation).

        Args:
            message: What the customer typed.
            customer_id: The logged-in customer.
            session_id: Conversation id; a new one is made when omitted.
            callbacks: Optional tracing callbacks (used by evaluation runs).
            case_id: Id of the case record made for this message (D51); a new one when omitted.

        Raises:
            LimitExceeded: If the customer or the daily budget is over its limit.
        """
        # A short id on every log line lets us follow one request through the logs.
        request_id = uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        try:
            return self._resolve(message, customer_id, session_id or request_id, callbacks, case_id)
        finally:
            request_id_var.reset(token)

    def _resolve(
        self, message, customer_id, session_id, callbacks, case_id=None
    ) -> ResolutionResult:
        """The real work of `resolve`, in order: checks, caches, graph run, memory."""
        # --- Step 1: cheap checks before any LLM cost ---
        problem = validate_message(message)
        if problem:
            log_event("input_rejected", status="validation")
            return _local_result("need_info", problem, "validation", clarifying_question=problem)
        try:
            self.limiter.check(customer_id)
        except LimitExceeded as exc:
            log_event("limit_exceeded", status=exc.kind)
            raise
        # --- Step 2: input guardrails ---
        verdict = check_input(
            message, self.hub
        )  # masks PII first; every later step sees masked text only
        for event in verdict.events:
            event.log()
        if verdict.action != "allow":
            return _guard_result(verdict, customer_id)
        # --- Step 3: load the session and try the semantic cache ---
        original, message = message, verdict.message
        session = self.sessions.get(session_id, customer_id)
        # The case id doubles as the checkpoint thread id, so review can find this run later.
        case_id = case_id or f"CASE-{uuid.uuid4().hex[:8].upper()}"
        use_semantic = bool(self.semantic) and cacheable_message(message, original, session)
        if use_semantic and (hit := self.semantic.lookup(message)):
            return self._from_cache(hit, session, message, case_id, customer_id)
        # --- Step 4: run the graph with a deadline ---
        trace_callbacks, trace_meta = (
            ([], {}) if callbacks else tracing.callbacks_for(session_id, customer_id, case_id)
        )
        cfg = {
            # Safety stop against endless loops; the normal path is under 10 steps.
            "recursion_limit": 25,
            "callbacks": callbacks or trace_callbacks,
            "metadata": trace_meta,
            "configurable": {"thread_id": case_id},
        }
        state_in = {
            "message": message, "customer_id": customer_id, "trace": [], "notices": [],
            "guard_events": [e.as_dict() for e in verdict.events],
            "context": _context_block(session), "case": vars(session.case).copy(),
        }  # fmt: skip
        start = time.perf_counter()
        # Run in a worker thread so we can give up after `timeout`. Copying the context keeps
        # the request id in the logs. `pool.shutdown(wait=False)` below means a stuck thread
        # cannot block the answer.
        ctx = contextvars.copy_context()
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(ctx.run, self._graph.invoke, state_in, cfg)
        try:
            future.result(timeout=self.timeout)
        except FutureTimeout:
            log_event("request_timeout", status="deadline")
            return _local_result(
                "escalate", "The request took too long, so it needs a human.", "timeout",
                escalation=True, customer_id=customer_id,
            )  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - last line of defence: the app stays up
            log_event("request_failed", error=type(exc).__name__)
            return _local_result(
                "escalate", "Something went wrong, so it needs a human.", "error",
                escalation=True, customer_id=customer_id,
            )  # fmt: skip
        finally:
            pool.shutdown(wait=False)
        # --- Step 5: read the final state. `snap.next` is non-empty when the graph is paused
        # at the review gate. ---
        snap = self._graph.get_state(cfg)
        result: ResolutionResult = snap.values["result"]
        result = result.model_copy(
            update={
                "case_id": case_id,
                "review_status": "pending" if snap.next else "not_required",
            }
        )
        # --- Step 6: update memory and the cache, then log ---
        self._remember(session, snap.values, message, result)
        if use_semantic:
            self.semantic.store(message, result)  # only if the result passes `storable`
        log_event(
            "request_done", recommendation=result.recommendation, intent=result.intent,
            latency_ms=round((time.perf_counter() - start) * 1000), case_id=case_id,
            cache_hit=False,
        )  # fmt: skip
        return result

    def _from_cache(self, hit, session, message, case_id, customer_id) -> ResolutionResult:
        """Serve a stored policy answer. It is an `inform` answer, so no human decision is needed.

        Args:
            hit: (stored result, similarity score) from the semantic cache.
            session: The current session.
            message: The masked customer message.
            case_id: New case id for this request.
            customer_id: The logged-in customer.
        """
        start = time.perf_counter()
        cached, score = hit
        result = cached.model_copy(
            update={
                "case_id": case_id,
                "review_status": "not_required",
                "served_from_cache": True,
                "notices": ["Answer reused from an earlier identical policy question."],
            }
        )
        self._remember(session, {}, message, result)
        log_event(
            "request_done", recommendation=result.recommendation, intent=result.intent,
            latency_ms=round((time.perf_counter() - start) * 1000), case_id=case_id,
            cache_hit=True, cache_tier="semantic", score=round(score, 3),
        )  # fmt: skip
        return result

    def _remember(self, session, values, message, result) -> None:
        """Save what we learned and the last turns, so a follow-up message has context.

        Args:
            session: The session to update.
            values: Final graph state (empty for a cache hit).
            message: The masked customer message.
            result: The result being returned.
        """
        u = values.get("understanding")
        case = session.case
        if u and "human_requested" not in u.flags:  # a request for a person changes no case facts
            case.order_id, case.intent = u.order_id or case.order_id, u.intent
            case.item_sku, case.error_origin = u.item_sku, u.error_origin
        case.open_question = result.clarifying_question
        self.sessions.add_turn(session, "customer", message)
        said = result.clarifying_question or f"[{result.recommendation}] {result.rationale}"
        # 300 characters is enough context for the next turn without bloating the prompt.
        self.sessions.add_turn(session, "assistant", said[:300])

    # ---- human review -----------------------------------------------------------------

    def review(
        self, case_id: str, action: str, edited_reply: str | None = None, reviewer: str = "agent"
    ) -> ResolutionResult:
        """Resume a paused case with the human's decision.

        Args:
            case_id: The case to review (from `resolve`).
            action: "approve", "edit" or "escalate".
            edited_reply: New reply text; required when the action is "edit".
            reviewer: Who made the decision, for the audit log.

        Returns:
            The result updated with the review status.

        Raises:
            ReviewError: If the action is invalid or no pending case has that id.
        """
        if action not in REVIEW_ACTIONS:
            raise ReviewError(f"action must be one of {REVIEW_ACTIONS}")
        if action == "edit" and not (edited_reply and edited_reply.strip()):
            raise ReviewError("edit needs the edited reply text")
        cfg = {"configurable": {"thread_id": case_id}}
        snap = self._graph.get_state(cfg)
        # No saved state, or nothing left to run, means the case is unknown or already reviewed.
        if not snap.values or not snap.next:
            raise ReviewError("No pending case with that ID (unknown or already reviewed).")
        token = request_id_var.set(case_id)
        try:
            out = self._graph.invoke(
                Command(resume={"action": action, "edited_reply": edited_reply,
                                "reviewer": reviewer, "case_id": case_id}),
                cfg,
            )  # fmt: skip
        finally:
            request_id_var.reset(token)
        return out["result"].model_copy(update={"case_id": case_id})


def _context_block(session) -> str:
    """Format earlier turns and known facts as text for the "understand" prompt."""
    if not session.turns:
        return ""
    lines = "\n".join(f"{role}: {text}" for role, text in session.turns)
    facts = {k: v for k, v in vars(session.case).items() if v and k != "customer_id"}
    return (
        f"<conversation_so_far>\n{lines}\n</conversation_so_far>\n"
        f"Known case facts: {json.dumps(facts)}\n"
    )


def _guard_result(verdict: InputVerdict, customer_id: str = "") -> ResolutionResult:
    """Answer built by the input guardrail: a polite refusal, or a human handover. No LLM call.

    Args:
        verdict: The input guardrail's decision ("block" or "escalate" here).
        customer_id: The customer's id, for the hand-off to staff.
    """
    info = None
    if verdict.action == "escalate":
        res = TOOLS["escalate_to_human"](
            case_summary=f"Input guardrail ({verdict.rule}). Customer text withheld from the record.",
            reason_code=verdict.escalation_reason or "other",
            customer_id=customer_id or None,
            policy_ids=REASON_POLICIES.get(verdict.escalation_reason or "", ["POL-10"]),
        )
        if res.ok:
            info = EscalationInfo(
                escalation_id=res.data["escalation_id"],
                reason_code=verdict.escalation_reason or "other",
                urgency="normal",
            )
    log_event(
        "input_blocked" if verdict.action == "block" else "input_escalated", status=verdict.rule
    )
    handoff = _build_handoff(
        info, customer_id, f"Input guardrail '{verdict.rule}' sent the case to a person.",
        summary="Customer text withheld from the record.",
        policies=REASON_POLICIES.get(verdict.escalation_reason or "", ["POL-10"]),
    )  # fmt: skip
    return ResolutionResult(
        recommendation="escalate" if info else "inform",
        rationale=f"Input guardrail '{verdict.rule}': {verdict.action}.",
        customer_reply_draft=verdict.reply or "",
        escalation=info,
        handoff=handoff,
        confidence=1.0,
        guardrail_status="blocked",
        guardrail_events=[e.as_dict() for e in verdict.events],
        prompt_version=config.PROMPT_VERSION,
        review_status="not_required",
        approval_level="manager" if info else "agent",
    )


def _local_result(
    rec, text, kind, clarifying_question=None, escalation=False, customer_id=""
) -> ResolutionResult:
    """Answer built without the graph (bad input, deadline, unexpected error).

    Args:
        rec: Recommendation, "need_info" or "escalate".
        text: Plain explanation; also used as the rationale.
        kind: Short label for the audit record ("validation", "timeout", "error").
        clarifying_question: Question to show the customer, if any.
        escalation: True to also record an escalation (a system fallback, not a policy reason).
        customer_id: The customer's id, for the hand-off to staff.
    """
    info = None
    if escalation:
        res = TOOLS["escalate_to_human"](
            case_summary=f"Automatic handover ({kind}): {text}",
            reason_code="tool_failure",
            customer_id=customer_id or None,
        )
        if res.ok:
            info = EscalationInfo(
                escalation_id=res.data["escalation_id"],
                reason_code="tool_failure",
                urgency="normal",
            )
    reply = text if clarifying_question else f"Hi {FIRST_NAME}, {text} A specialist will follow up."
    handoff = _build_handoff(info, customer_id, text, summary=f"Automatic handover ({kind}).")
    return ResolutionResult(
        recommendation=rec, rationale=text, customer_reply_draft=reply, escalation=info,
        handoff=handoff,
        clarifying_question=clarifying_question, confidence=1.0,
        guardrail_status="passed", prompt_version=config.PROMPT_VERSION,
        review_status="not_required",
        approval_level="manager" if escalation else "agent",
    )  # fmt: skip
