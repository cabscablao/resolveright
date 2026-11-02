"""Developer debug page (/ui-dev): a read-only view of what the agent did for one case (D51).

Where it sits in the flow: it reads the saved case (app/cases.py), rebuilds the agent's result from
the case's debug block and shows it with the same helpers the customer module defines: Agent
Process (status banner, why, hand-off to staff, evidence), Pipeline, Tools, Retrieval, Guardrails,
Raw values and Raw JSON. It never writes. It replaces the old `?debug=1` developer view in the
customer chat.
"""

import gradio as gr

from app import cases, config
from app.agent.schemas import ResolutionResult
from app.ui.gradio_app import (
    format_banner,
    format_evidence,
    format_pipeline,
    format_raw,
    format_why,
    guardrail_rows,
    retrieval_rows,
    trace_rows,
)  # fmt: skip
from app.ui.theme import header

EMPTY = "<i>Pick a case to see what the agent did.</i>"


def case_choices() -> list[tuple[str, str]]:
    """Dropdown entries (label, case id) for every case, newest first."""
    return [
        (f"{c['case_id']} · {c['status']} · {c['customer_id']}", c["case_id"])
        for c in cases.list_cases()
    ]


def format_staff(case: dict) -> str:
    """The staff decision on a case, as Markdown (empty text while no staff decided)."""
    if not case.get("staff_decision"):
        return "_No staff decision yet._"
    ex = case.get("execution")
    done = (
        f"`{ex['resolution_id']}` ({ex['action']}, {config.peso(ex['amount'])})"
        if ex
        else "nothing"
    )
    return (
        f"**Staff decision:** {case['staff_decision']} · **Executed:** {done}\n\n"
        f"**Note:** {case['staff_note'] or '-'}\n\n**Staff reply:** {case['staff_reply']}"
    )


def show_case(case_id):
    """Fill every section of the page for one case.

    Args:
        case_id: The case chosen in the dropdown, or None.

    Returns:
        Values for: banner, why, evidence, staff decision, pipeline, tools, retrieval, guardrails,
        raw values, case JSON.
    """
    case = cases.get_case(case_id)
    if not case or not case.get("debug", {}).get("result"):
        return EMPTY, "", "", "", "", [], [], [], "", None
    r = ResolutionResult.model_validate(case["debug"]["result"])
    pipeline = f"{format_pipeline(r)} · **Latency:** {case['debug'].get('latency_ms', '-')} ms"
    routing = case["debug"].get("routing")
    if routing:  # the open-case check made before the pipeline (D58)
        pipeline += (
            f"  \n**Routing:** `{routing.get('route')}` · open case found: "
            f"{'yes' if routing.get('open_case_found') else 'no'}"
            + (f" (`{routing['case_id']}`)" if routing.get("case_id") else "")
        )
    return (
        format_banner(r), format_why(r), format_evidence(r), format_staff(case), pipeline,
        trace_rows(r), retrieval_rows(r), guardrail_rows(r),
        f"{format_raw(r)}\n- `status` = `{case['status']}`, `resolved_by` = `{case['resolved_by']}`",
        case,
    )  # fmt: skip


def refresh_dev(current):
    """Timer and Refresh handler: reload the choices and the shown case."""
    ids = [value for _, value in case_choices()]
    keep = current if current in ids else None
    return (gr.update(choices=case_choices(), value=keep), *show_case(keep))


def build_dev_ui() -> gr.Blocks:
    """Build the developer page. Returns the Blocks app, mounted at /ui-dev (app/server.py)."""
    with gr.Blocks(title=f"{config.APP_NAME} Developer Debug") as dev:
        gr.HTML(
            header(
                "🛠️ Developer Debug (for testing only)",
                "Read-only: what the agent did for a case. Nothing here changes a case.",
            )
        )
        with gr.Row():
            selector = gr.Dropdown(case_choices(), label="Case", value=None, scale=4)
            refresh_btn = gr.Button("🔄 Refresh", size="sm", scale=0)
        timer = gr.Timer(5)
        with gr.Accordion("Agent Process", open=True):
            banner = gr.HTML(EMPTY)
            why = gr.Markdown("")
            with gr.Accordion("Evidence", open=False):
                evidence = gr.Markdown("")
            with gr.Accordion("Staff decision", open=False):
                staff = gr.Markdown("")
        with gr.Accordion("Pipeline", open=False):
            pipeline = gr.Markdown("")
        with gr.Accordion("Tools", open=False):
            trace = gr.Dataframe(
                headers=["tool", "arguments", "result"], wrap=True, interactive=False
            )
        with gr.Accordion("Retrieval", open=False):
            retrieval = gr.Dataframe(
                headers=["policy", "section", "chunk_id"], wrap=True, interactive=False
            )
        with gr.Accordion("Guardrails", open=False):
            guards = gr.Dataframe(headers=["layer", "rule", "action"], wrap=True, interactive=False)
        with gr.Accordion("Raw values", open=False, elem_classes="dev-code"):
            raw = gr.Markdown("")
        with gr.Accordion("Raw case JSON", open=False, elem_classes="dev-code"):
            raw_json = gr.JSON()
        gr.Markdown(f"{config.APP_NAME} v{config.APP_VERSION}", elem_classes="footer-note")

        shown = [banner, why, evidence, staff, pipeline, trace, retrieval, guards, raw, raw_json]
        selector.change(show_case, selector, shown)
        refresh_btn.click(refresh_dev, selector, [selector, *shown])
        timer.tick(refresh_dev, selector, [selector, *shown])
        dev.load(refresh_dev, selector, [selector, *shown])
    return dev
