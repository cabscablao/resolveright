"""Staff review page (/ui-staff): the queue of escalated cases and the staff decision (D51).

Where it sits in the flow: the customer chat saves every case in the shared case file
(app/cases.py). Cases the policy keeps for a person (or no policy covers) arrive here as
`escalated`. A staff member opens one (status `in_review`), reads the hand-off packet and the
chatbot's recommendation, then approves, modifies or rejects it. Approve and modify carry out the
resolution (a mock: ledger line and runtime order change) with staff authority; reject executes
nothing. The case becomes `completed` and the customer page shows the staff reply.

Only the case list is refreshed by the timer, never the form, so text being typed is not erased.
When the customer writes again about a case that waits for staff (D58), the list shows a "New msg"
marker; Reload case brings the message into the details without touching the form.
This page has no login (a documented limitation: docs/limitations.md).
"""

import gradio as gr

from app import cases, config
from app.agent.schemas import ResolutionResult
from app.tools.resolution import EXECUTABLE_ACTIONS
from app.ui.gradio_app import format_evidence, format_handoff
from app.ui.theme import header

QUEUE_HEADERS = ["Case", "Customer", "Order", "Reason", "Status", "New msg", "Created"]
ACTION_CHOICES = ["none", *EXECUTABLE_ACTIONS]


def queue_rows() -> list[list[str]]:
    """Rows for the case list: escalated and in-review cases, newest first."""
    return [
        [c["case_id"], c["customer_id"], c["order_id"] or "-", c["escalation_reason"] or "-",
         c["status"], "💬" if c.get("has_new_customer_message") else "", c["created_at"]]
        for c in cases.list_cases(("escalated", "in_review"))
    ]  # fmt: skip


def case_ids() -> list[str]:
    """Ids of the open cases, for the "Open case" dropdown."""
    return [c["case_id"] for c in cases.list_cases(("escalated", "in_review"))]


def refresh_queue(current):
    """Reload the case list and the dropdown choices; keep the chosen case.

    Args:
        current: The case id chosen in the dropdown, or None.

    Returns:
        (list rows, dropdown update). The dropdown value is kept only while the case is open.
    """
    ids = case_ids()
    return queue_rows(), gr.update(choices=ids, value=current if current in ids else None)


def format_transcript(case: dict) -> str:
    """The conversation of the case as Markdown (customer text is already masked)."""
    return "\n\n".join(
        f"**{'Customer' if t['role'] == 'user' else 'Chatbot'}:** {t['content']}"
        for t in case["transcript"]
    )


def format_recommendation(case: dict) -> str:
    """The chatbot's recommendation and the action staff would approve, as Markdown."""
    rec = case.get("chatbot_recommendation") or {}
    lines = [f"**Chatbot recommendation:** {str(rec.get('action', '-')).replace('_', ' ')}"]
    if rec.get("suggested_action"):
        lines.append(
            f"**Approve would carry out:** {rec['suggested_action'].replace('_', ' ')} of "
            f"{config.peso(rec['suggested_amount'])}"
        )
    else:
        lines.append("**Approve would carry out:** nothing (reply only)")
    return "\n\n".join(lines)


def format_details(case: dict) -> str:
    """The details panel of a case: hand-off packet, conversation and evidence, as Markdown."""
    result = ResolutionResult.model_validate(case["debug"]["result"])
    parts = [
        f"### {case['case_id']} · {case['status'].replace('_', ' ')}",
        format_handoff(result, with_json=False) if result.handoff else "_No hand-off packet._",
        "**Conversation**\n\n" + format_transcript(case),
        format_evidence(result),
    ]
    return "\n\n".join(parts)


def reload_case(case_id):
    """Reload button: re-read one case into the details panel, leaving the decision form alone.

    A customer message that arrived while the case was open shows up in the conversation, and the
    "New msg" marker is cleared. The note, reply and action boxes keep what the staff member typed.

    Args:
        case_id: The case chosen in the dropdown.

    Returns:
        (details Markdown, recommendation Markdown); both unchanged when no case is chosen.
    """
    case = cases.clear_new_message(case_id) if case_id else None
    if not case or not case.get("debug", {}).get("result"):
        return gr.skip(), gr.skip()
    return format_details(case), format_recommendation(case)


def open_case(case_id):
    """Open a case: mark it in review and fill the details panel and the decision defaults.

    Args:
        case_id: The case chosen in the dropdown.

    Returns:
        (details Markdown, recommendation Markdown, action default, amount default, reply "",
        note "", result message "").
    """
    if not case_id:
        # The outcome message is kept: submitting clears the dropdown, which lands here.
        return (
            "_Pick an escalated case to see its hand-off packet._", "", "none", None, "", "",
            gr.skip(),
        )  # fmt: skip
    try:
        case = cases.open_case(case_id)
    except cases.CaseError as exc:
        return f"⚠️ {exc}", "", "none", None, "", "", ""
    rec = case.get("chatbot_recommendation") or {}
    return (
        format_details(case), format_recommendation(case),
        rec.get("suggested_action") or "none", rec.get("suggested_amount"), "", "", "",
    )  # fmt: skip


def submit_decision(case_id, decision, action, amount, note, reply):
    """Apply the staff decision and report what happened.

    Args:
        case_id: The open case.
        decision: "approve", "modify" or "reject".
        action: For modify: the action to carry out, or "none".
        amount: For modify: the amount.
        note: Internal note.
        reply: Message to the customer.

    Returns:
        (result Markdown, list rows, dropdown update).
    """
    if not case_id:
        return "⚠️ Pick a case first.", *refresh_queue(None)
    try:
        done = cases.staff_decide(
            case_id, decision, reply=reply or "", note=note or "", action=action,
            amount=float(amount) if amount else None,
        )  # fmt: skip
    except cases.CaseError as exc:
        return f"⚠️ {exc}", *refresh_queue(case_id)
    ref = (
        f" Executed: `{done['execution']['resolution_id']}`."
        if done["execution"]
        else (" Nothing was executed.")
    )
    return (
        f"✅ {case_id} completed ({decision}).{ref} The customer sees your reply.",
        *refresh_queue(None),
    )


def build_staff_ui() -> gr.Blocks:
    """Build the staff page. Returns the Blocks app, mounted at /ui-staff (app/server.py)."""
    with gr.Blocks(title=f"{config.APP_NAME} Staff Review") as staff:
        gr.HTML(
            header(
                f"🧑‍💼 {config.APP_NAME} Staff Review",
                "Review escalated cases, decide, and reply to the customer. Demo: no login.",
            )
        )
        with gr.Row(equal_height=False):
            with gr.Column(scale=1):
                with gr.Row():
                    gr.Markdown("### Escalated cases")
                    refresh_btn = gr.Button("🔄 Refresh", size="sm", scale=0)
                timer = gr.Timer(5)
                queue = gr.Dataframe(
                    headers=QUEUE_HEADERS,
                    value=queue_rows,
                    interactive=False,
                    wrap=True,
                )
                with gr.Row():
                    chosen = gr.Dropdown(choices=case_ids(), label="Open case", value=None, scale=4)
                    reload_btn = gr.Button("↻ Reload case", size="sm", scale=1)
            with gr.Column(scale=2):
                gr.Markdown("### Case details")
                details = gr.Markdown("_Pick an escalated case to see its hand-off packet._")
                recommendation = gr.Markdown("")
                decision = gr.Radio(
                    ["approve", "modify", "reject"], value="approve", label="Staff decision",
                    info="Approve and modify carry out the resolution (mock); reject executes nothing.",
                )  # fmt: skip
                with gr.Row():
                    action = gr.Dropdown(ACTION_CHOICES, value="none", label="Action (modify)")
                    amount = gr.Number(label="Amount (modify)", precision=2)
                note = gr.Textbox(label="Internal note", lines=2)
                reply = gr.Textbox(
                    label="Reply to the customer", lines=3,
                    info="Required for modify and reject. Approve uses a standard reply if empty.",
                )  # fmt: skip
                submit = gr.Button("Submit decision", variant="primary")
                outcome = gr.Markdown("")
        gr.Markdown(f"{config.APP_NAME} v{config.APP_VERSION}", elem_classes="footer-note")

        queue_outputs = [queue, chosen]
        refresh_btn.click(refresh_queue, chosen, queue_outputs)
        timer.tick(refresh_queue, chosen, queue_outputs)
        staff.load(refresh_queue, chosen, queue_outputs)
        # Opening a case fills the details and the decision defaults; the timer never touches them.
        chosen.change(
            open_case, chosen, [details, recommendation, action, amount, reply, note, outcome]
        )
        reload_btn.click(reload_case, chosen, [details, recommendation])
        submit.click(
            submit_decision,
            [chosen, decision, action, amount, note, reply],
            [outcome, queue, chosen],
        )
    return staff
