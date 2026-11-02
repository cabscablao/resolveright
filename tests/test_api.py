"""Tests for the web layer: the REST API, the Gradio UI helpers, rate limits and startup.

The agent behind the API is built with the scripted fake LLM from test_agent, so there is
no network and the answers are predictable. The tests check HTTP codes and the shape of replies.
"""

import pytest
from fastapi.testclient import TestClient

from app.agent.graph import Agent
from app.api import main
from tests.test_agent import FakeLLM, _env, draft, und  # noqa: F401  (_env is an autouse fixture)


@pytest.fixture
def client():
    """A test web client whose agent uses the fake LLM (always: return ORD-1001, refund).

    The agent dependency is swapped out so no real LLM is called. The swap is undone afterwards.
    """
    llm = FakeLLM([und("return", "ORD-1001")] * 5, lambda c, n: draft("refund"))
    agent = Agent(llm)
    main.app.dependency_overrides[main.get_agent] = lambda: agent
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


@pytest.fixture
def escalated_client():
    """Like `client`, but for ORD-1021 (customer C-001): above the agent limit, so a human is needed."""
    llm = FakeLLM([und("return", "ORD-1021")] * 5, lambda c, n: draft("refund"))
    agent = Agent(llm)
    main.app.dependency_overrides[main.get_agent] = lambda: agent
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_health_reports_status_and_version(client):
    """The health endpoint reports status, version, model and the state of its parts."""
    body = client.get("/health").json()
    assert body["version"] and body["model"] and body["status"] in ("ok", "degraded")
    assert "vector_store" in body
    # conftest.py switches both caches off for tests, and /health must say so.
    assert body["cache"] == {"llm_exact": "off", "semantic": "off"}


def test_resolve_returns_schema_with_name_filled_in(client):
    """A resolve call returns the full result, with the customer's first name in the preview.

    The stored draft keeps the {{first_name}} placeholder, so the LLM never sees the real name.
    """
    r = client.post(
        "/api/v1/resolve", json={"customer_id": "C-001", "message": "Return ORD-1001 please"}
    )
    assert r.status_code == 200
    body = r.json()
    # A refund under the ₱5,000 agent limit stays inside policy, so no human decision is needed.
    assert body["recommendation"] == "refund" and body["requires_human_approval"] is False
    assert body["review_status"] == "not_required" and body["case_id"].startswith("CASE-")
    assert "{{first_name}}" in body["customer_reply_draft"]
    assert body["customer_reply_preview"].startswith("Hi Nora")


def test_above_the_agent_limit_requires_a_human(escalated_client):
    """Above the ₱5,000 agent limit the API escalates, says a human is needed and waits."""
    esc = escalated_client.post(
        "/api/v1/resolve", json={"customer_id": "C-001", "message": "Return ORD-1021 please"}
    ).json()
    assert esc["recommendation"] == "escalate" and esc["requires_human_approval"] is True
    assert esc["review_status"] == "pending"


def test_reviewing_an_in_policy_case_is_a_conflict(client):
    """A case that needed no human decision cannot be approved afterwards (409)."""
    case = client.post(
        "/api/v1/resolve", json={"customer_id": "C-001", "message": "Return ORD-1001 please"}
    ).json()["case_id"]
    assert (
        client.post("/api/v1/review", json={"case_id": case, "action": "approve"}).status_code
        == 409
    )


def test_unknown_customer_is_404(client):
    """A customer id that does not exist gives "not found", not a server error."""
    r = client.post("/api/v1/resolve", json={"customer_id": "C-999", "message": "hello there"})
    assert r.status_code == 404


def test_empty_message_gets_friendly_answer_not_an_error(client):
    """A blank message gets a polite "need more info" answer, not an HTTP error."""
    r = client.post("/api/v1/resolve", json={"customer_id": "C-001", "message": "   "})
    assert r.status_code == 200 and r.json()["recommendation"] == "need_info"


def test_review_flow_and_double_review_conflict(escalated_client):
    """A human can approve an escalated case once. A second review is a conflict (409)."""
    client = escalated_client
    case = client.post(
        "/api/v1/resolve", json={"customer_id": "C-001", "message": "Return ORD-1021 please"}
    ).json()["case_id"]
    ok = client.post("/api/v1/review", json={"case_id": case, "action": "approve"})
    assert ok.status_code == 200 and ok.json()["review_status"] == "approved"
    again = client.post("/api/v1/review", json={"case_id": case, "action": "approve"})
    assert again.status_code == 409


def test_review_with_invalid_action_is_422(client):
    """An action outside the allowed list is refused by input validation (422)."""
    r = client.post("/api/v1/review", json={"case_id": "CASE-1", "action": "refund_now"})
    assert r.status_code == 422


def test_customers_list_has_no_contact_details(client):
    """The customer list shows only id and first name, never email or address."""
    rows = client.get("/api/v1/customers").json()
    assert len(rows) == 2 and set(rows[0]) == {"customer_id", "first_name"}


def test_openapi_docs_available(client):
    """The auto-generated API spec is served, so other teams can read the contract."""
    assert client.get("/openapi.json").status_code == 200


# ---- Gradio UI helpers -------------------------------------------------------------------


def test_ui_builds_and_mounts():
    """The full server mounts the Gradio UI at /ui and redirects the home page to it."""
    from app.server import app as full_app

    assert TestClient(full_app).get("/ui").status_code == 200
    assert TestClient(full_app).get("/", follow_redirects=False).status_code in (302, 307)


def test_ui_escalation_message_shows_in_chat_at_once(monkeypatch):
    """An escalation reply appears in the chat immediately; nothing waits for a person.

    The debug banner says it was escalated and shows "Not applicable" instead of a confidence.
    The UI calls `get_agent()` directly (not through FastAPI), so the fake agent is patched in.
    """
    from app.ui import gradio_app as ui

    llm = FakeLLM(und("return", "ORD-1021"), lambda c, n: draft("refund"))
    agent = Agent(llm)
    monkeypatch.setattr(ui, "get_agent", lambda: agent)
    history, result, preview, sid = ui.handle_send("Return ORD-1021 please", [], None)
    assert sid and result.recommendation == "escalate"
    assert history[-1] == {"role": "assistant", "content": preview} and preview
    banner = ui.format_banner(result)
    assert "Escalated to human staff: <b>Yes</b>" in banner
    assert "Model confidence: <b>Not applicable</b>" in banner
    assert "Not executed" in banner
    assert ui.trace_rows(result)[0][0] == "lookup_order"
    assert "Waiting" not in preview
    # The Agent Process section carries the structured hand-off for staff (D50).
    why = ui.format_why(result)
    assert "Hand-off to staff" in why and "above_agent_limit" in why and "C-001" in why
    assert "Executed by the agent | No" in why and "@" not in why


def test_ui_in_policy_refund_is_executed_and_shows_the_reference(monkeypatch):
    """An in-policy refund is carried out; the chat and the banner show the reference."""
    from app.ui import gradio_app as ui

    agent = Agent(FakeLLM(und("return", "ORD-1001"), lambda c, n: draft("refund")))
    monkeypatch.setattr(ui, "get_agent", lambda: agent)
    _, result, preview, _ = ui.handle_send("Return ORD-1001 please", [], None)
    assert preview.startswith("Hi Nora") and result.review_status == "not_required"
    banner = ui.format_banner(result)
    assert "Escalated to human staff: <b>No</b>" in banner and "Not applicable" not in banner
    assert result.execution and result.execution.resolution_id in preview
    assert f"Action executed: refund (ref {result.execution.resolution_id})" in banner
    assert "Hand-off to staff" not in ui.format_why(result)


def test_ui_confidence_bands_and_friendly_text():
    """Confidence shows High / Medium / Low; raw codes and policy ids turn into plain words."""
    from app.agent.graph import _local_result
    from app.ui import gradio_app as ui

    r = _local_result("refund", "ok", "test")
    for value, label in ((0.9, "High"), (0.8, "High"), (0.6, "Medium"), (0.2, "Low")):
        r.confidence = value
        assert label in ui.confidence_band(r)
    r.recommendation = "escalate"
    assert ui.confidence_band(r) == "Not applicable"
    assert ui.friendly_fact("eligibility: WITHIN_RETURN_WINDOW, ABOVE_AGENT_LIMIT") == (
        "eligibility: within return window, above agent limit"
    )
    assert ui.policy_names()["POL-01"] == "Returns"


def test_customer_page_has_no_developer_view_and_keeps_demo_setup():
    """The developer view moved to /ui-dev (D51): the chat has no debug checkbox or hidden zone."""
    from app.ui import gradio_app as ui

    blocks = list(ui.build_ui().blocks.values())
    labels = [getattr(b, "label", "") for b in blocks]
    assert "Demo setup to fill in message to be sent above (for testing only)" in labels
    assert "Show developer view" not in labels
    # Only the amber open-requests line starts hidden (it shows when a case is open).
    hidden = [b for b in blocks if getattr(b, "visible", True) is False]
    assert [getattr(b, "elem_classes", None) for b in hidden] == [["open-line"]]
    html = " ".join(str(getattr(b, "value", "")) for b in blocks)
    assert "/ui-staff/" in html and "/ui-dev/" in html  # links inside Demo setup


def test_three_pages_are_mounted():
    """The server serves the customer chat, the staff page and the developer page."""
    from app.server import app as full_app

    c = TestClient(full_app)
    assert all(c.get(path).status_code == 200 for path in ("/ui", "/ui-staff", "/ui-dev"))


def test_each_page_sets_its_browser_tab_title():
    """The mounted pages have no <title> tag, so a script sets it (the tab showed the address)."""
    from app.server import app as full_app

    c = TestClient(full_app)
    titles = {"/ui": "ResolveRight Customer Support"}
    for path, title in titles.items():
        js = c.get(path + "/config").json()["js"]
        assert f'const t = "{title}";' in js and "document.title" in js, path
    # The staff and developer pages set theirs too, from the title given to gr.Blocks.
    for path in ("/ui-staff", "/ui-dev"):
        assert "document.title" in c.get(path + "/config").json()["js"], path


def test_all_three_pages_open_in_dark_mode_with_readable_message_box():
    """Every page gets the dark switch (D56), and the message box has an explicit light text colour."""
    from app.server import app as full_app
    from app.ui.theme import CSS, FORCE_DARK_JS

    c = TestClient(full_app)
    for path in ("/ui", "/ui-staff", "/ui-dev"):
        assert c.get(path + "/config").json()["js"].startswith(FORCE_DARK_JS), path
    # Gradio 6 injects the value as a <script>, so it must be called, not just defined (it was not).
    assert FORCE_DARK_JS.startswith("(") and FORCE_DARK_JS.rstrip().endswith(")();")
    assert "classList.add('dark')" in FORCE_DARK_JS and "MutationObserver" in FORCE_DARK_JS
    box = CSS.split(".dark #msg-box textarea {\n  color:")[1].split("}")[0]
    assert "#F1F5F9" in box and "caret-color" in box
    # Gradio rewrites ".dark a, .dark b" so that only the first selector still matches, and the
    # light rules would win (pale box, light text). Dark rules must be single selectors.
    assert not [ln for ln in CSS.splitlines() if ln.startswith(".dark") and "," in ln.split("{")[0]]
    assert "--msg-bg" in CSS and "var(--msg-bg" in CSS


def test_ui_header_and_footer_wording():
    """The header carries the customer subtitle; the old decision-support notice is gone."""
    from app.api.main import NOTICE
    from app.ui import gradio_app as ui

    demo = ui.build_ui()
    texts = [str(getattr(b, "value", "")) for b in demo.blocks.values()]
    assert any(ui.SUBTITLE in t and "ResolveRight Customer Support" in t for t in texts)
    assert not any("Demo only: no refunds" in t for t in texts)
    assert not any(NOTICE in t for t in texts)
    assert ui.SUBTITLE.startswith("Ask about your orders, returns and refunds.")


# ---- Weekend 2: limits, tracing switch, startup ingest -------------------------------------


def test_rate_limit_per_customer_then_recovers():
    """A customer who sends too many requests per minute is blocked, then allowed again later.

    This protects cost and stability. A fake clock lets us "wait" a minute instantly.
    """
    from app.agent.limits import LimitExceeded, RequestLimiter

    now = [0.0]
    lim = RequestLimiter(per_minute=3, daily_budget=100, clock=lambda: now[0])
    for _ in range(3):
        lim.check("C-001")
    with pytest.raises(LimitExceeded) as err:
        lim.check("C-001")
    assert err.value.kind == "rate_limit" and err.value.retry_after >= 1
    lim.check("C-002")  # Other customers are not affected.
    now[0] = 61
    lim.check("C-001")  # The one-minute window has passed.


def test_daily_budget_stops_everyone_and_resets_next_day():
    """A shared daily budget stops all customers when used up, and resets on the next day.

    A fake "today" lets us move to the next day without waiting.
    """
    from datetime import date

    from app.agent.limits import LimitExceeded, RequestLimiter

    day = [date(2026, 11, 2)]
    lim = RequestLimiter(per_minute=100, daily_budget=2, today=lambda: day[0])
    lim.check("a")
    lim.check("b")
    with pytest.raises(LimitExceeded) as err:
        lim.check("c")
    assert err.value.kind == "daily_budget"
    day[0] = date(2026, 11, 3)
    lim.check("c")


def test_api_returns_429_with_retry_after():
    """At the limit, the API answers 429 with a Retry-After header that says when to retry."""
    from app.agent.limits import RequestLimiter

    llm = FakeLLM([und("return", "ORD-1001")] * 3, lambda c, n: draft("refund"))
    agent = Agent(llm, limiter=RequestLimiter(per_minute=1))
    main.app.dependency_overrides[main.get_agent] = lambda: agent
    try:
        c = TestClient(main.app)
        body = {"customer_id": "C-001", "message": "Return ORD-1001 please"}
        assert c.post("/api/v1/resolve", json=body).status_code == 200
        r = c.post("/api/v1/resolve", json=body)
        assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    finally:
        main.app.dependency_overrides.clear()


def test_ui_shows_limit_message_instead_of_crashing(monkeypatch):
    """When the daily budget is used up, the UI shows a friendly message instead of an error."""
    from app.agent.limits import RequestLimiter
    from app.ui import gradio_app as ui

    agent = Agent(FakeLLM(und("return", "ORD-1001")), limiter=RequestLimiter(daily_budget=0))
    monkeypatch.setattr(ui, "get_agent", lambda: agent)
    _, result, preview, _ = ui.handle_send("Return ORD-1001", [], None)
    assert "budget" in preview and result.recommendation == "need_info"


def test_tracing_is_off_in_tests_and_when_keys_missing(monkeypatch):
    """Tracing is on only when it is enabled AND both keys exist, and ids are hashed.

    Hashing the customer id keeps real ids out of the tracing tool.
    """
    from app.observability import tracing

    assert tracing.enabled() is False  # conftest sets LANGFUSE_ENABLED=false.
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    assert tracing.enabled() is False
    assert tracing.callbacks_for("s", "C-001", "CASE-1") == ([], {})
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    assert tracing.enabled() is True
    assert tracing.hash_id("C-001") != "C-001" and len(tracing.hash_id("C-001")) == 10


def test_startup_ingest_runs_when_store_empty_and_never_raises(monkeypatch):
    """On startup, policies load if the store is empty. A failure must not stop the app."""
    calls = []
    monkeypatch.setattr(main, "vector_store_status", lambda: "empty")
    monkeypatch.setattr("app.rag.ingest.ingest", lambda: calls.append(1) or {})
    main.ensure_policies_ingested()
    assert calls == [1]

    def boom():
        """Fake ingest that fails (for example, a missing API key)."""
        raise RuntimeError("no key")

    monkeypatch.setattr("app.rag.ingest.ingest", boom)
    main.ensure_policies_ingested()  # Error is swallowed; /health will show "degraded".


def test_startup_ingest_refreshes_a_non_empty_index(monkeypatch):
    """With an index already present, start-up still runs ingest so changed policies are embedded."""
    calls = []
    monkeypatch.setattr(main, "vector_store_status", lambda: "ok")
    monkeypatch.setattr(
        "app.rag.ingest.ingest",
        lambda: calls.append(1) or {"docs_embedded": 1, "docs_skipped": 9},
    )
    main.ensure_policies_ingested()
    assert calls == [1]

    calls.clear()
    monkeypatch.setattr(main.config, "INGEST_ON_STARTUP", False)
    main.ensure_policies_ingested()
    assert calls == []


def test_startup_refresh_failure_keeps_the_existing_index(monkeypatch):
    """If the refresh fails (no key, no network) the app starts and the failure is logged."""
    events = []
    monkeypatch.setattr(main, "vector_store_status", lambda: "ok")
    monkeypatch.setattr(main, "log_event", lambda name, **kw: events.append((name, kw)))

    def boom():
        """Fake ingest that fails."""
        raise RuntimeError("no network")

    monkeypatch.setattr("app.rag.ingest.ingest", boom)
    main.ensure_policies_ingested()
    assert events == [
        ("startup_ingest_failed", {"error": "RuntimeError", "status": "kept existing index"})
    ]


def test_health_shows_tracing_state(client):
    """The health endpoint says tracing is "off" when it is not configured."""
    assert client.get("/health").json()["tracing"] == "off"
