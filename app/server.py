"""ASGI entry point: the API plus the three Gradio pages.

ASGI is the standard interface between Python web apps and servers such as uvicorn. This file
joins the FastAPI app (app/api/main.py) and the Gradio pages into one app, so a single process
serves all of them (one process only: the case file and the Qdrant folder allow a single writer):
  /ui        customer chat (app/ui/gradio_app.py)
  /ui-staff  staff review of escalated cases (app/ui/staff_ui.py)
  /ui-dev    developer debug, read-only (app/ui/dev_ui.py)

Run: uv run uvicorn app.server:app --port 8000   (customer chat at /ui, API docs at /docs)
"""

import gradio as gr
from fastapi.responses import RedirectResponse

from app.api.main import app as api_app
from app.ui.dev_ui import build_dev_ui
from app.ui.gradio_app import build_ui
from app.ui.staff_ui import build_staff_ui
from app.ui.theme import look


@api_app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Send visitors of the bare address to the chat UI (hidden from the API docs)."""
    return RedirectResponse("/ui")


# `app` is the name uvicorn looks for. Mounting puts each Gradio page under its own path.
# All three pages share one look: the theme, the CSS and dark mode (D56).
# Each page also sets its own browser tab title (the mounted page has no <title> tag by itself).
app = api_app
for page, path in (
    (build_ui(), "/ui"),
    (build_staff_ui(), "/ui-staff"),
    (build_dev_ui(), "/ui-dev"),
):
    app = gr.mount_gradio_app(app, page, path=path, **look(page.title))
