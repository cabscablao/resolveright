"""search_policy tool: RAG over the policy documents.

RAG (retrieval-augmented generation) means we first search our own documents for relevant
passages, then give them to the LLM so its answer is grounded in company policy.
Where it sits: the agent calls `search_policy` to fetch policy text before drafting a reply.
"""

from app import config
from app.observability.logging import log_event
from app.tools.fault_injection import injected_fault
from app.tools.results import ToolError, ToolOk

_retriever = None  # created on first use; tests can replace it with set_retriever()


def set_retriever(retriever) -> None:
    """Swap in a different retriever (used by tests to avoid real vector search)."""
    global _retriever
    _retriever = retriever


def _get_retriever():
    """Return the shared retriever, creating it on first use."""
    global _retriever
    if _retriever is None:
        # Imported here because the RAG module is heavy and slows start-up.
        from app.rag.retriever import Retriever

        _retriever = Retriever()
    return _retriever


def warm_up() -> None:
    """Load the retriever and embed one query now, so the first real request is not slow.

    A cold start takes many seconds (imports, vector store, first API call), which would eat
    the per-request deadline. Never raises: search_policy reports problems itself.
    """
    try:
        _get_retriever().retrieve("warm up", 1)
    except Exception as exc:  # noqa: BLE001
        log_event("warm_up_failed", error=type(exc).__name__)


def search_policy(query: str, k: int = config.TOP_K) -> ToolOk | ToolError:
    """Return the k most relevant policy chunks, with doc id, chunk id, section and score.

    Args:
        query: What to look for, in plain words.
        k: How many chunks (pieces of a policy document) to return; limited to 1..8.
    """
    if fault := injected_fault("search_policy"):
        return fault
    if not query or not query.strip():
        return ToolError(code="invalid_input", message="query must not be empty.")
    # Clamp k: at least 1, and at most 8 so the prompt stays small.
    k = max(1, min(int(k), 8))
    try:
        chunks = _get_retriever().retrieve(query.strip(), k)
    except Exception as exc:  # noqa: BLE001 - vector store down, empty, or embedding API failure
        log_event("search_policy_failed", error=type(exc).__name__)
        return ToolError(code="unavailable", message="Policy search is unavailable right now.")
    return ToolOk(data={"chunks": chunks})
