"""FastAPI service: POST /api/v1/resolve, POST /api/v1/review, GET /api/v1/cases/{id}, GET /health.

Where it sits in the flow: this is the front door. The UI and any other client send HTTP
requests here; each handler calls the Agent (app/agent/graph.py), which runs the guardrails,
caches, retrieval and LLM, then returns the result.

The agent carries out what Brightcart policy lets a support agent approve (a mock: a ledger line
and a runtime order change, no real payment system). Only a policy escalation or a policy gap goes
to a person, with a structured hand-off.
"""

import threading
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import cases, config
from app.agent.graph import Agent, ReviewError
from app.agent.limits import LimitExceeded
from app.agent.render import render_reply
from app.agent.schemas import ExecutionInfo, ResolutionResult
from app.observability import tracing
from app.observability.logging import log_event
from app.tools import orders, policy_search
from app.tools.orders import _customers, get_customer

# Shown in the API docs: what the agent does by itself and what goes to a person.
NOTICE = (
    "The agent carries out refunds, store credit, replacements and cancellations that policy lets "
    "a support agent approve (a mock: no real payment system). Cases the policy keeps for a "
    "person, and questions no policy covers, go to staff with a structured hand-off."
)


class ResolveRequest(BaseModel):
    """Body of POST /api/v1/resolve: one customer message."""

    customer_id: str = Field(examples=["C-001"])
    message: str = Field(examples=["I'd like to return the headphones from ORD-1001."])
    session_id: str | None = None


class ResolveResponse(ResolutionResult):
    """The agent's result plus a ready-to-read reply preview and the case status."""

    customer_reply_preview: str = Field(description="Reply draft with the first name filled in")
    case_status: str | None = Field(
        default=None, description="processing, escalated, in_review or completed (D51)"
    )


class ReviewRequest(BaseModel):
    """Body of POST /api/v1/review: a staff member's decision on an escalated case.

    approve carries out the chatbot's suggested action, modify carries out the action and amount
    given here (or only replies when resolution_action is "none"), reject executes nothing.
    edit (reply only) and escalate (hand on again) are kept from the earlier API.
    """

    case_id: str
    action: Literal["approve", "modify", "reject", "edit", "escalate"]
    edited_reply: str | None = None
    reviewer: str = "agent"
    customer_id: str = Field(default="", description="Only used to fill in the first name")
    note: str = ""
    resolution_action: str | None = Field(default=None, description="For modify")
    amount: float | None = Field(default=None, description="For modify")


@lru_cache(maxsize=1)
def get_agent() -> Agent:
    """Return the one shared Agent, creating it on first use.

    lru_cache(maxsize=1) makes this a simple singleton, so the agent's session memory and
    caches are shared by all requests.
    """
    return Agent()


def _response(result: ResolutionResult, customer_id: str) -> ResolveResponse:
    """Wrap an agent result in the API response, adding the personalised reply preview.

    Args:
        result: The agent's resolution.
        customer_id: Used to look up the first name for the greeting.

    Returns:
        The response model.
    """
    case = cases.get_case(result.case_id)
    return ResolveResponse(
        **result.model_dump(),
        customer_reply_preview=render_reply(result.customer_reply_draft, customer_id),
        case_status=case["status"] if case else None,
    )


def ensure_policies_ingested() -> None:
    """Embed new or changed policy files at start-up. Failure keeps the old index and is logged.

    On a fresh host (empty disk) this embeds everything. On a host with an index it re-embeds
    only the files whose text changed (unchanged files cost no OpenAI call), so an edited policy
    never leaves a stale index behind. If it fails, the app starts on the existing index.
    """
    if not config.INGEST_ON_STARTUP:
        return
    try:
        # Imported here so the API starts even if the RAG packages have a problem.
        from app.rag.ingest import ingest

        stats = ingest()
        if stats.get("docs_embedded"):
            log_event("startup_ingest_refreshed", status=str(stats))
    except Exception as exc:  # noqa: BLE001 - the app must still start; /health shows the state
        log_event(
            "startup_ingest_failed",
            error=type(exc).__name__,
            status="kept existing index" if vector_store_status() == "ok" else "index empty",
        )


def _warm_up_guardrails_in_background() -> None:
    """Load the Guardrails AI validators in a thread, so the server starts at once.

    Without this the first customer message would wait for the jailbreak model to load. A
    validator that fails to load is only logged; the rule layers carry on alone.
    """
    from app.guardrails.hub import get_hub

    hub = get_hub()  # None when GUARDRAILS_AI_ENABLED is off
    if hub is None:
        return

    def _load() -> None:
        log_event("guardrails_ai_status", status=str(hub.warm_up()))

    threading.Thread(target=_load, name="guardrails-warm-up", daemon=True).start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run start-up work before the first request and shutdown work after the last.

    Args:
        _app: The FastAPI app (unused, required by the interface).
    """
    # --- Start-up: clear the cases of the last session, make sure policies are embedded and the
    # search index is warm ---
    if config.RESET_CASES_ON_STARTUP:
        cases.reset_store()
        orders.reset_runtime_state()  # executed refunds start from nothing too (D60)
    ensure_policies_ingested()
    policy_search.warm_up()
    _warm_up_guardrails_in_background()
    yield
    # --- Shutdown ---
    tracing.flush()  # send any traces still buffered


app = FastAPI(
    title=f"{config.APP_NAME} API",
    version=config.APP_VERSION,
    description=NOTICE,
    lifespan=lifespan,
)


@app.post("/api/v1/resolve", response_model=ResolveResponse)
def resolve(req: ResolveRequest, agent: Agent = Depends(get_agent)) -> ResolveResponse:
    """Resolve one customer message: carry out an approved resolution or hand the case to staff.

    Args:
        req: The customer id, message and optional session id.
        agent: The shared Agent (injected by FastAPI).

    Returns:
        The agent's recommendation with a reply preview.

    Raises:
        HTTPException: 404 for an unknown customer, 429 when a rate or budget limit is hit.
    """
    if get_customer(req.customer_id) is None:
        raise HTTPException(status_code=404, detail="Unknown customer_id.")
    try:
        result = cases.process_message(agent, req.message, req.customer_id, req.session_id)
    except LimitExceeded as exc:
        # 429 = "too many requests"; Retry-After tells the client how many seconds to wait.
        raise HTTPException(
            status_code=429, detail=str(exc), headers={"Retry-After": str(exc.retry_after)}
        ) from exc
    return _response(result, req.customer_id)


@app.post("/api/v1/review", response_model=ResolveResponse)
def review(req: ReviewRequest, agent: Agent = Depends(get_agent)) -> ResolveResponse:
    """Decide an escalated case as staff: approve, modify, reject (or edit / escalate again).

    Approve and modify carry out the resolution (a mock) with staff authority; reject executes
    nothing. The case is completed and the customer page shows the staff reply.

    Args:
        req: The case id, the staff decision and, for modify, the action, amount and reply.
        agent: The shared Agent (injected by FastAPI).

    Returns:
        The updated case.

    Raises:
        HTTPException: 409 if the case is unknown, already completed, or the action is refused.
    """
    if req.action == "escalate":
        try:
            return _response(agent.review(req.case_id, "escalate", None, req.reviewer),
                             req.customer_id)  # fmt: skip
        except ReviewError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    decision = {"approve": "approve", "edit": "modify", "modify": "modify", "reject": "reject"}
    try:
        case = cases.staff_decide(
            req.case_id, decision[req.action], reply=req.edited_reply or "", note=req.note,
            action="none" if req.action == "edit" else req.resolution_action,
            amount=req.amount, reviewer=req.reviewer,
        )  # fmt: skip
    except cases.CaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result = ResolutionResult.model_validate(case["debug"]["result"]).model_copy(update={
        "review_status": {"approve": "approved", "modify": "edited", "edit": "edited",
                          "reject": "rejected"}[req.action],
        "customer_reply_draft": case["staff_reply"], "case_id": case["case_id"],
        "execution": ExecutionInfo(**case["execution"]) if case["execution"] else None,
    })  # fmt: skip
    return _response(result, case["customer_id"])


@app.get("/api/v1/cases/{case_id}")
def get_case(case_id: str) -> dict:
    """Status of one case (what the customer page polls): status, who resolved it, replies."""
    case = cases.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Unknown case.")
    keys = (
        "case_id",
        "status",
        "resolved_by",
        "customer_reply",
        "staff_reply",
        "outcome_summary",
        "staff_decision",
        "execution",
        "created_at",
        "updated_at",
        "completed_at",
    )
    return {k: case[k] for k in keys}


@app.get("/api/v1/customers")
def customers() -> list[dict]:
    """Demo customers for the UI dropdown (synthetic data)."""
    return [{"customer_id": c["customer_id"], "first_name": c["first_name"]}
            for c in _customers().values()]  # fmt: skip


def vector_store_status() -> str:
    """Check whether the policy collection exists and has chunks.

    Returns:
        "ok" if it has chunks, "empty" if it exists with none, "unavailable" on any error.
    """
    try:
        from app.rag.embeddings import HashEmbeddings
        from app.rag.ingest import collection_name, get_client

        # The collection name contains the embedding model, so rebuild the same name here.
        model = HashEmbeddings.model_name if config.EMBEDDING_PROVIDER == "hash" else (
            config.EMBEDDING_MODEL
        )  # fmt: skip
        name = collection_name(config.CHUNK_STRATEGY, model)
        client = get_client()
        return "ok" if client.collection_exists(name) and client.count(name).count else "empty"
    except Exception as exc:  # noqa: BLE001 - health must never raise
        log_event("health_vector_store_failed", error=type(exc).__name__)
        return "unavailable"


@app.get("/health")
def health() -> dict:
    """Report whether the service is healthy (used by the host's health check).

    Returns:
        A dict with status ("ok" or "degraded"), version, model, prompt version, vector store
        state, cache and tracing state.
    """
    store = vector_store_status()
    return {
        # "degraded" (not "down"): the app can still answer, but retrieval will not work.
        "status": "ok" if store == "ok" else "degraded",
        "version": config.APP_VERSION,
        "model": config.LLM_MODEL,
        "prompt_version": config.PROMPT_VERSION,
        "vector_store": store,
        # Which of the two caches are switched on (see app/cache).
        "cache": {
            "llm_exact": "on" if config.LLM_CACHE_ENABLED else "off",
            "semantic": "on" if config.SEMANTIC_CACHE_ENABLED else "off",
        },
        "tracing": "langfuse" if tracing.enabled() else "off",
    }
