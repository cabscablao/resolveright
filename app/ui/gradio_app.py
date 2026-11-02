"""Gradio customer page (/ui) and the formatting helpers the staff and developer pages reuse.

Gradio is a Python library that builds a web page from code. Since D51 the product has three pages
that share one theme (app/ui/theme.py) and one case file (app/cases.py):
  1. Customer chat (this file, /ui): send a message and read the reply. Status is shown inline under
     the reply (for a case staff hold), in one amber open-requests line, and in a collapsed "My
     requests (N)" list (D61). A timer and a Refresh button pick up the staff reply, which arrives
     once as a "Brightcart staff" message. Under Send, the Demo setup accordion (sample picker,
     links to the other two pages) is for testing. There is no customer picker: the page always
     chats as the one demo customer (D57).
  2. Staff review (app/ui/staff_ui.py, /ui-staff): the queue of escalated cases and the decision.
  3. Developer debug (app/ui/dev_ui.py, /ui-dev): read-only view of what the agent did.

Where it sits in the flow: the UI calls the same Agent as the API (no HTTP in between) through
`cases.handle_message`, which first checks for a case waiting for staff (D58), then saves the case.

The agent carries out what policy lets a support agent approve (a mock: a ledger line and a runtime
order change). Only a policy escalation or a policy gap goes to a person: the case is queued for
staff with a structured hand-off, and nothing is executed until staff decide.
Mounted on the FastAPI app at /ui (see app/server.py) and uses the same Agent instance.
"""

import json
import re
import uuid
from datetime import datetime

import gradio as gr

from app import cases, config
from app.agent.graph import _local_result
from app.agent.limits import LimitExceeded
from app.agent.render import render_reply
from app.agent.schemas import ResolutionResult
from app.api.main import get_agent
from app.tools.orders import get_customer, get_order
from app.ui.theme import CSS, THEME, header  # noqa: F401  (CSS and THEME are mounted by server.py)

# Ready-made test messages for the "Sample message" dropdown: name -> message. They are all sent
# as the one demo customer (config.DEMO_CUSTOMER_ID, Nora). Each exercises a different path
# (eligible, over the limit, safety, a missing order, a follow-up while a case waits for staff...).
SAMPLES = {
    "Return within the window (ORD-1001)":
        "Hi, I'd like to return the headphones from order ORD-1001, I changed my mind.",
    "Return above the agent limit (ORD-1021)":
        "I want to return the ergonomic chair on ORD-1021, it doesn't suit me.",
    "Opened consumable (ORD-1027)": "Can I return the protein supplement from ORD-1027? I opened it.",
    "Personalised mug, unclear who erred (ORD-1028)":
        "My mug from ORD-1028 has my name spelled wrong.",
    "Missing order number": "I want to return something.",
    "Overheating power bank (safety)": "My power bank ORD-1023 is overheating and smells burnt.",
    "Late order (ORD-1012)": "My order ORD-1012 still hasn't arrived.",
    "Policy question": "How long do I have to return something after it arrives?",
    "Order does not exist": "Where is my order ORD-9999?",
    "Follow-up: any update (after ORD-1021)": "Any update on my return?",
    "Follow-up: new information (after ORD-1021)": "The chair arrived with a cracked leg.",
    "Follow-up: pressure (after ORD-1021)": "Just refund me now!",
    "Unrelated question while waiting": "What's your shipping policy?",
}  # fmt: skip
# Colour-coded label for each guardrail_status value the agent can return.
BADGES = {
    "passed": "🟢 Output checks passed",
    "repaired": "🟡 Output repaired once (first draft failed a check, second passed)",
    "fallback": "🔴 Output check failed: safe handover",
    "blocked": "🛑 Blocked by an input guardrail",
}

# Model confidence bands: High >= 0.8, Medium >= 0.5, otherwise Low. Shown for information only;
# whether a case goes to a person is decided by policy, not by this number.
HIGH, MEDIUM = 0.8, 0.5

# Policy document titles come from the file names in data/policies (POL-01_returns.md -> Returns).
_POLICY_FILE = re.compile(r"^(POL-\d+)_(.+)\.md$")


SUBTITLE = (
    "Ask about your orders, returns and refunds. Answers follow Brightcart policy, "
    "special cases are passed to our staff."
)


def policy_names() -> dict[str, str]:
    """Map each policy id to a plain-words title, read from the file names in data/policies.

    Returns:
        A dict such as {"POL-01": "Returns", "POL-04": "Damaged defective wrong"}.
    """
    names = {}
    for path in sorted((config.DATA_DIR / "policies").glob("POL-*.md")):
        match = _POLICY_FILE.match(path.name)
        if match:
            words = match.group(2).replace("_", " ")
            names[match.group(1)] = words[:1].upper() + words[1:]
    return names


def friendly_fact(fact: str) -> str:
    """Turn raw codes in an order fact into plain words.

    Args:
        fact: A fact such as "eligibility: WITHIN_RETURN_WINDOW, ABOVE_AGENT_LIMIT".

    Returns:
        The same fact with each UPPER_SNAKE code in lower-case words, for example
        "eligibility: within return window, above agent limit".
    """
    return re.sub(r"\b[A-Z]{2,}(?:_[A-Z]+)+\b", lambda m: m.group().lower().replace("_", " "), fact)


def confidence_band(r: ResolutionResult) -> str:
    """Label the model confidence as High / Medium / Low, or "Not applicable" for an escalation.

    An escalation is decided by policy (or by a policy gap), so the model's confidence does not
    apply to it.

    Args:
        r: The agent's resolution.

    Returns:
        A short coloured label.
    """
    if r.recommendation == "escalate":
        return "Not applicable"
    if r.confidence >= HIGH:
        return "🟢 High"
    if r.confidence >= MEDIUM:
        return "🟡 Medium"
    return "🔴 Low"


def format_banner(r: ResolutionResult) -> str:
    """Build the HTML status banner at the top of the Agent Process section.

    Args:
        r: The agent's resolution.

    Returns:
        An HTML snippet with the recommendation, amount, escalation flag, status and confidence.
    """
    escalated = r.recommendation == "escalate"
    colour = "#b45309" if escalated else "#15803d"
    amount = f" · {config.peso(r.amount)}" if r.amount is not None else ""
    if escalated:
        status = "Not executed. Escalation message sent to the customer; case queued for staff"
    elif r.execution:
        status = f"Action executed: {r.execution.action.replace('_', ' ')} (ref {r.execution.resolution_id})"
    else:
        status = "No action needed. Reply sent to the customer"
    return (
        f"<div style='border-left:6px solid {colour};padding:8px 12px;border-radius:4px;"
        f"background:rgba(128,128,128,.12)'>"
        f"<div style='font-size:1.25em'><b>Decision: "
        f"{r.recommendation.replace('_', ' ').title()}{amount}</b></div>"
        f"<div>Escalated to human staff: <b>{'Yes' if escalated else 'No'}</b> · "
        f"Status: <b>{status}</b> · Model confidence: <b>{confidence_band(r)}</b></div></div>"
    )


def format_why(r: ResolutionResult) -> str:
    """Build the Markdown explanation: notices, proposed action, why, question, escalation.

    Args:
        r: The agent's resolution.

    Returns:
        Markdown text for the Agent Process section.
    """
    lines = [f"> ⚠️ {notice}" for notice in r.notices]
    if r.proposed_action:
        lines.append(f"**Proposed action:** {r.proposed_action}")
    lines.append(f"**Why:** {r.rationale}")
    if r.clarifying_question:
        lines.append(f"**Question for the customer:** {r.clarifying_question}")
    if r.escalation:
        e = r.escalation
        lines.append(f"**Escalation:** `{e.escalation_id}` ({e.reason_code}, {e.urgency})")
    names = policy_names()
    used = ", ".join(
        dict.fromkeys(f"{c.doc_id} {names.get(c.doc_id, '')}".strip() for c in r.policy_citations)
    )
    lines.append(f"**Policies used:** {used or 'none (no matching policy)'}")
    if r.handoff:
        lines.append(format_handoff(r))
    return "\n\n".join(lines)


def format_handoff(r: ResolutionResult, with_json: bool = True) -> str:
    """Build the "Hand-off to staff" block shown under Agent Process for an escalation.

    Args:
        r: The agent's resolution; it must carry a `handoff`.
        with_json: Also show the structured payload as JSON (the developer view); the staff page
            passes False and shows the table only.

    Returns:
        Markdown with a small table (who, what, why) and, if asked, the payload as JSON.
    """
    h = r.handoff
    who = f"{h.customer.first_name or 'unknown'} ({h.customer.customer_id})"
    want = f"{h.request.intent.replace('_', ' ')}" + (
        f", order {h.request.order_id}" if h.request.order_id else ""
    )
    why = (
        f"{h.reason.reason_code} ({h.reason.reason_type.replace('_', ' ')}): {h.reason.explanation}"
    )
    rows = [
        ("Hand-off ID", h.handoff_id), ("Customer", who), ("Wanted", want),
        ("Summary", h.request.summary or "-"), ("Why it is escalating", why),
        ("Policies applied (rules)", ", ".join(h.policies) or "-"), ("Urgency", h.urgency),
        ("Executed by the agent", "No (nothing was executed)"),
    ]  # fmt: skip
    table = "| Field | Value |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows)
    if not with_json:
        return f"**Hand-off to staff**\n\n{table}"
    payload = json.dumps(h.model_dump(mode="json"), indent=2)
    return f"**Hand-off to staff**\n\n{table}\n\n```json\n{payload}\n```"


def format_evidence(r: ResolutionResult) -> str:
    """List the order facts and the policy sections the answer relied on, in plain words.

    The policy list here is what the model cited from the retrieved passages. It can differ from
    "Policies applied (rules)" in the hand-off table, which comes from the rules engine.

    Args:
        r: The agent's resolution.

    Returns:
        Markdown bullets for the Evidence section.
    """
    parts = []
    if r.order_facts_used:
        parts.append(
            "**Order facts**\n" + "\n".join(f"- {friendly_fact(f)}" for f in r.order_facts_used)
        )
    if r.policy_citations:
        names = policy_names()
        parts.append(
            "**Policy text cited by the assistant (retrieved passages)**\n"
            + "\n".join(
                f"- **{c.doc_id} {names.get(c.doc_id, '')}**, {c.section}".replace("  ", " ")
                for c in r.policy_citations
            )
        )
    return "\n\n".join(parts) or "_No order facts or policies for this case._"


def format_pipeline(r: ResolutionResult) -> str:
    """Build the Pipeline line: output-check status, prompt version and cache flag.

    Args:
        r: The agent's resolution.

    Returns:
        Markdown text for the Pipeline section.
    """
    return (
        f"**Pipeline status:** {BADGES[r.guardrail_status]}  \n"
        f"**Prompt version:** {r.prompt_version or '-'} · "
        f"**From cache:** {'yes' if r.served_from_cache else 'no'}"
    )


def retrieval_rows(r: ResolutionResult) -> list[list[str]]:
    """List the retrieved policy chunks the answer cited.

    Args:
        r: The agent's resolution.

    Returns:
        One row per citation: [policy, section, chunk id].
    """
    return [[c.doc_id, c.section, c.chunk_id] for c in r.policy_citations]


def guardrail_rows(r: ResolutionResult) -> list[list[str]]:
    """List the guardrail events (layer, rule, action). Never contains the customer's text.

    Args:
        r: The agent's resolution.

    Returns:
        One row per event: [layer, rule, action].
    """
    return [[str(e.get("layer", "")), str(e.get("rule", "")), str(e.get("action", ""))]
            for e in r.guardrail_events]  # fmt: skip


def format_raw(r: ResolutionResult) -> str:
    """Show the exact machine values the agent returned, for debugging.

    Args:
        r: The agent's resolution.

    Returns:
        Markdown bullets with the raw confidence and status values.
    """
    return (
        f"- `confidence` = {r.confidence}\n- `recommendation` = `{r.recommendation}`\n"
        f"- `review_status` = `{r.review_status}`\n- `approval_level` = `{r.approval_level}`\n"
        f"- `requires_human_approval` = `{r.requires_human_approval}`\n"
        f"- `execution` = {r.execution.model_dump() if r.execution else None}\n"
        f"- `order_facts_used` = {r.order_facts_used}"
    )


def trace_rows(r: ResolutionResult) -> list[list[str]]:
    """Turn the tool trace into table rows. Shows tools and results only, no hidden reasoning.

    Args:
        r: The agent's resolution.

    Returns:
        One row per tool call: [tool name, non-empty arguments, "ok" or the error].
    """
    return [
        [t["tool"], ", ".join(f"{k}={v}" for k, v in t["args"].items() if v), t.get("error", "ok")]
        for t in r.tool_trace
    ]


OPEN = cases.OPEN_STATUSES
WORKING = "*⏳ Working on it…*"  # temporary bot line under the customer's message (flow 8.1)
INTENT_LABELS = {
    "return": "Return", "damaged": "Damaged item", "defective": "Defective item",
    "wrong_item": "Wrong item", "policy_question": "Question", "other": "Request",
}  # fmt: skip


def ago(stamp: str | None, now: datetime | None = None) -> str:
    """Short relative time for a case timestamp: "just now", "2 min ago", "3 h ago", "2 d ago"."""
    try:
        then = datetime.fromisoformat(stamp or "")
    except ValueError:
        return ""
    secs = max(0, int(((now or datetime.now(then.tzinfo)) - then).total_seconds()))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


def request_name(case: dict) -> str:
    """Name a request by type and item, e.g. "Return – Wireless headphones" (flow 8.1 rule 7).

    The type comes from the saved hand-off or result, the item from the order (the named SKU, or the
    only item). Without an item the type alone is used.
    """
    result = (case.get("debug") or {}).get("result") or {}
    request = (result.get("handoff") or {}).get("request") or {}
    intent = request.get("intent") or result.get("intent") or "other"
    label = INTENT_LABELS.get(intent) or intent.replace("_", " ").capitalize()
    if (result.get("handoff") or {}).get("reason", {}).get("reason_code") == "human_requested":
        label = "Talk to our team"
    order = get_order(case["order_id"]) if case.get("order_id") else None
    items = (order or {}).get("items", [])
    sku = request.get("item_sku")
    item = next((i["name"] for i in items if i["sku"] == sku), None) if sku else None
    if item is None and len(items) == 1:
        item = items[0]["name"]
    return f"{label} – {item}" if item else label


def status_text(case: dict, now: datetime | None = None) -> str:
    """The wording of the status table in section 8.1 (never says who resolved a chatbot case)."""
    name, status, by = request_name(case), case["status"], case["resolved_by"]
    if status == "processing":
        return "⏳ Working on it…"
    if status == "escalated":
        return f"⏳ With our team · {name} · we'll reply here"
    if status == "in_review":
        return f"👀 Our team is reviewing · {name}"
    if by == "staff":
        return f"✅ Resolved by our team · {name}"
    order = f" ({case['order_id']})" if case.get("order_id") else ""
    return f"✅ Resolved · {name}{order} · {ago(case.get('completed_at'), now)}".rstrip(" ·")


def inline_status(case: dict | None) -> str:
    """Italic line under a reply. An open case shows its status; a case the chatbot resolved shows
    no banner, only the one-line outcome (such as the refund reference) when there is one."""
    if not case:
        return ""
    if case["status"] in OPEN:
        return f"\n\n*{status_text(case)}*"
    if case["status"] == "completed" and case.get("outcome_summary"):
        return f"\n\n*{case['outcome_summary']}*"
    return ""


def load_cases(case_ids) -> list[dict]:
    """The case records of this conversation (unknown ids are skipped), oldest first."""
    return [c for c in (cases.get_case(i) for i in case_ids or []) if c]


def render_open_line(found: list[dict]) -> str:
    """One amber line about the open cases only; "" when none (the caller hides the line)."""
    open_ = [c for c in found if c["status"] in OPEN]
    if not open_:
        return ""
    n = len(open_)
    return f"⏳ {n} request{'s' if n > 1 else ''} with our team · {request_name(open_[0])}"


def render_my_requests(found: list[dict], now: datetime | None = None) -> str:
    """Every case, newest first: name, status, time, and the case code small and grey."""
    rows = []
    for c in sorted(found, key=lambda c: c["created_at"], reverse=True):
        css = "open" if c["status"] in OPEN else "done"
        label = status_text(c, now).split(" · ")[0]
        rows.append(
            f'<div class="row"><span>{request_name(c)}</span>'
            f'<span class="{css}">{label} · {ago(c["created_at"], now)}</span>'
            f'<span class="code">{c["case_id"]}</span></div>'
        )
    return "".join(rows) or "<i>No requests yet.</i>"


def panel(case_ids) -> tuple:
    """What changes when cases change: (open line, My requests list, accordion label, stamp)."""
    found = load_cases(case_ids)
    line = render_open_line(found)
    return (
        gr.update(value=line, visible=bool(line)),
        render_my_requests(found),
        gr.update(label=f"My requests ({len(found)})"),
        f"Last updated {datetime.now():%H:%M:%S}" if case_ids else "",
    )


def handle_send(message, history, session_id):
    """Send one customer message, save the case and update the chat.

    The message goes through `cases.handle_message`: a follow-up on a case that waits for staff is
    answered from a template (no agent), anything else runs the normal pipeline.

    Args:
        message: The text the user typed.
        history: The chat so far (list of role/content dicts), or None.
        session_id: The current session id, or None on the first message.

    Returns:
        A tuple (new history, result, reply shown, session id). The reply shown is the reply plus the
        inline status line. `result.case_id` is the case made for this message, or the open case for
        a follow-up (None when a limit stopped it).
    """
    # A new session id on the first message keeps follow-up turns in the same conversation.
    session_id = session_id or uuid.uuid4().hex
    cid = config.DEMO_CUSTOMER_ID
    try:
        result = cases.handle_message(get_agent(), message, cid, session_id)
    except LimitExceeded as exc:
        # Show the rate-limit message in the chat instead of crashing the page.
        result = _local_result("need_info", str(exc), "limit", clarifying_question=str(exc))
    shown = render_reply(result.customer_reply_draft, cid) + inline_status(
        cases.get_case(result.case_id)
    )
    history = list(history or []) + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": shown},
    ]
    return history, result, shown, session_id


def show_user_message(message, history):
    """First step of Send, instant: the customer's message and a temporary "Working on it" line.

    Args:
        message: The text the user typed.
        history: The chat so far (list of role/content dicts), or None.

    Returns:
        (history with the message and the temporary line added, the message). An empty message adds
        nothing (the agent step still answers it, as before).
    """
    message = message or ""
    if message.strip():
        history = list(history or []) + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": WORKING},
        ]
    return history, message


def refresh_customer(case_ids, history):
    """Timer and Refresh handler: update the open line and list, append each new staff reply once.

    Args:
        case_ids: The cases of this conversation, or None.
        history: The chat so far.

    Returns:
        (chat update, open line, My requests list, accordion label, "Last updated" text). The chat is
        left untouched (gr.update()) unless a staff reply arrived, so it does not flicker.
    """
    new = []
    for case_id in case_ids or []:
        reply = cases.deliver_staff_reply(case_id)
        if reply:
            case = cases.get_case(case_id)
            new.append({
                "role": "assistant",
                "content": f"🧑‍💼 **Brightcart staff** · update on {case_id}\n\n{reply}"
                f"\n\n*✅ Resolved by our team · {request_name(case)}*",
            })  # fmt: skip
    chat = list(history or []) + new if new else gr.update()
    return (chat, *panel(case_ids))


def build_ui() -> gr.Blocks:
    """Build the customer page: layout first, then the click handlers.

    Returns:
        The Blocks app, ready to be mounted on FastAPI at /ui (see app/server.py).
    """
    with gr.Blocks(title="ResolveRight Customer Support") as demo:
        gr.HTML(header("🛍️ ResolveRight Customer Support", SUBTITLE))
        # gr.State keeps per-browser-tab values between clicks.
        session = gr.State(None)
        case_ids = gr.State([])  # every case of this conversation, oldest first
        pending = gr.State("")  # the message being answered (the box is cleared at once)
        busy = gr.State(False)  # True while the agent works, so the timer leaves the page alone
        gr.Markdown(f"Signed in as **{_demo_name()}** ({config.DEMO_CUSTOMER_ID})")
        chat = gr.Chatbot(
            elem_id="chat", height=420, autoscroll=True, show_label=False,
            placeholder="Hi! How can I help with your order today?",
        )  # fmt: skip
        with gr.Row():  # the open-requests line is hidden when no case is open
            open_line = gr.HTML(elem_classes="open-line", visible=False)
            refresh_btn = gr.Button("🔄 Refresh", size="sm", scale=0)
            last_updated = gr.Markdown("")
        with gr.Row():
            box = gr.Textbox(
                show_label=False, lines=1, scale=5, container=False, elem_id="msg-box",
                placeholder="Type your question, e.g. 'I want to return my order'",
            )  # fmt: skip
            send = gr.Button("Send", variant="primary", scale=1)
        with gr.Accordion("My requests (0)", open=False) as my_acc:
            my_requests = gr.HTML(elem_classes="my-requests")
        # Set apart by a dotted divider so it does not look like a chat option.
        with (
            gr.Column(elem_classes="test-section"),
            gr.Accordion(
                "Demo setup to fill in message to be sent above (for testing only)", open=False
            ),
        ):
            sample = gr.Dropdown(
                list(SAMPLES), label="Sample message", value=None,
                info="Pick a sample; the message fills in.",
            )  # fmt: skip
            gr.HTML(
                '<a href="/ui-staff/" target="_blank" rel="noopener">🧑‍💼 Open staff panel ↗</a>'
                " &nbsp;·&nbsp; "
                '<a href="/ui-dev/" target="_blank" rel="noopener">🛠️ Open developer page ↗</a>'
            )
        timer = gr.Timer(5)
        view = [open_line, my_requests, my_acc, last_updated]

        # --- Handlers: functions Gradio calls when the user clicks or types ---
        def pick_sample(name):
            """Fill the message box from the chosen sample."""
            return SAMPLES[name] if name else gr.skip()

        def start_send(message, history):
            """Step 1 of Send (instant): show the message and "Working on it", clear the box."""
            history, message = show_user_message(message, history)
            return history, "", message, True

        def on_send(message, history, session_id, ids):
            """Step 2 of Send: answer, replacing the temporary line with the reply and its status."""
            if (message or "").strip():
                history = list(history or [])[
                    :-2
                ]  # step 1 showed both; handle_send adds them again
            history, result, _, session_id = handle_send(message, history, session_id)
            ids = list(ids or [])
            if result.case_id and result.case_id not in ids:
                ids.append(result.case_id)  # a follow-up reuses the case it belongs to
            return (history, session_id, ids, *panel(ids))

        # --- Wiring: connect each control to its handler ---
        # Send and Enter run three steps: show the message at once (no queue wait), ask the agent,
        # then release the timer (`then` runs even if the agent step failed).
        for trigger in (send.click, box.submit):
            trigger(start_send, [box, chat], [chat, box, pending, busy], queue=False).then(
                on_send, [pending, chat, session, case_ids], [chat, session, case_ids, *view]
            ).then(lambda: False, None, busy)
        sample.change(pick_sample, sample, box)

        def poll(ids, history, is_busy):
            """Refresh and timer handler; while the agent is working it leaves the page alone."""
            if is_busy:
                return (gr.update(),) * 5
            return refresh_customer(ids, history)

        refresh_btn.click(poll, [case_ids, chat, busy], [chat, *view])
        timer.tick(poll, [case_ids, chat, busy], [chat, *view])
    return demo


def _demo_name() -> str:
    """First name of the demo customer, for the "Signed in as" line."""
    return (get_customer(config.DEMO_CUSTOMER_ID) or {}).get("first_name") or "customer"
