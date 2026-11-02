"""Shared test setup. It runs before any test and switches off every outside service.

Why: tests must be fast, free and repeatable. They must never call the network or
send traces, even if the developer's .env file has real keys.
"""

import os

import pytest

# Never send traces from tests, even if .env has Langfuse keys.
os.environ["LANGFUSE_ENABLED"] = "false"
# Skip the network call to the moderation endpoint.
os.environ["MODERATION_ENABLED"] = "false"
# Avoid model downloads. Tests that need the hub turn this on themselves.
os.environ["GUARDRAILS_AI_ENABLED"] = "false"
# Cache tests turn this on with their own temporary files, so no shared cache leaks between tests.
os.environ["LLM_CACHE_ENABLED"] = "false"
os.environ["SEMANTIC_CACHE_ENABLED"] = "false"


@pytest.fixture(autouse=True)
def _isolated_execution_state(tmp_path, monkeypatch):
    """Send executed resolutions to a temp folder, so tests never change the real runtime state.

    The agent now executes approved resolutions (a ledger line plus a runtime order change).
    Every test gets its own empty ledger and order-state file.
    """
    from app import config

    monkeypatch.setattr(config, "RESOLUTION_LOG", tmp_path / "resolutions.jsonl")
    monkeypatch.setattr(config, "ORDER_STATE_FILE", tmp_path / "order_state.json")
    monkeypatch.setattr(config, "CASES_FILE", tmp_path / "cases.json")
