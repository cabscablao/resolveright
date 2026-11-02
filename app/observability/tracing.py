"""Langfuse tracing (course tool for tracing). Off unless the Langfuse keys are set.

A trace is a recorded timeline of one request: every step, LLM call, token count and cost.
Langfuse is a service that stores and shows traces.

Where it sits in the flow: the agent adds the callbacks from here to each graph run, and the
LLM client class comes from here so each call is recorded too.

Graph runs are traced through Langfuse's LangChain/LangGraph callback handler. LLM calls use the
Langfuse drop-in OpenAI client, so each trace also shows tokens and cost.
Traces hold the same data the graph sees; input masking arrives with the week 2 guardrails.
"""

import hashlib
import os

from app.observability.logging import log_event


def enabled() -> bool:
    """Say whether Langfuse tracing is on.

    Returns:
        True only if LANGFUSE_ENABLED is not switched off AND both Langfuse keys are set.
    """
    if os.getenv("LANGFUSE_ENABLED", "true").lower() in ("0", "false", "no"):
        return False
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def hash_id(value: str) -> str:
    """Make a pseudonymous id for sessions and customers (never the raw id).

    Args:
        value: The raw id.

    Returns:
        The first 10 hex characters of its SHA-256 hash: short, stable, hard to reverse.
    """
    return hashlib.sha256(value.encode()).hexdigest()[:10]


def callbacks_for(session_id: str, customer_id: str, case_id: str) -> tuple[list, dict]:
    """Build the tracing callbacks and metadata for one graph run.

    Args:
        session_id: The conversation session id (hashed before it is sent).
        customer_id: The customer id (hashed before it is sent).
        case_id: The case id, used as a tag.

    Returns:
        A tuple (callbacks, metadata). Both are empty when tracing is off or broken.
    """
    if not enabled():
        return [], {}
    try:
        # Imported here so the app runs without Langfuse installed or configured.
        from langfuse.langchain import CallbackHandler

        # Langfuse reads these special "langfuse_*" keys from the run metadata.
        metadata = {
            "langfuse_session_id": hash_id(session_id),
            "langfuse_user_id": hash_id(customer_id),
            "langfuse_tags": ["resolveright", case_id],
        }
        return [CallbackHandler()], metadata
    except Exception as exc:  # noqa: BLE001 - tracing must never break a request
        log_event("tracing_unavailable", error=type(exc).__name__)
        return [], {}


def openai_client_class():
    """Pick the OpenAI client class to use.

    Returns:
        The Langfuse-instrumented OpenAI class when tracing is on, else the plain one.
    """
    if enabled():
        try:
            from langfuse.openai import OpenAI

            return OpenAI
        except Exception as exc:  # noqa: BLE001
            log_event("tracing_unavailable", error=type(exc).__name__)
    import openai

    return openai.OpenAI


def flush() -> None:
    """Send any buffered traces now (Langfuse batches them), for example at shutdown."""
    if not enabled():
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as exc:  # noqa: BLE001
        log_event("tracing_flush_failed", error=type(exc).__name__)
