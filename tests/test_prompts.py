"""Tests for the draft prompt files in prompts/ (no network).

The draft prompt is chosen by the PROMPT_VERSION setting. These tests check that every variant
loads, keeps the name placeholder, and that the newest variant (v4) matches how human approval
works now: only an escalation waits for a person (D47), and v5 matches the agent that executes
approved resolutions (D50).
"""

import pytest

from app import config
from app.agent.graph import Agent, _load_prompt
from tests.test_agent import FakeLLM, _env, draft, und  # noqa: F401  (_env is an autouse fixture)

VERSIONS = ["v1_baseline", "v2_grounded", "v3_checklist", "v4_approval_aware", "v5_executing"]


@pytest.mark.parametrize("version", VERSIONS)
def test_every_prompt_variant_loads_and_keeps_the_name_placeholder(version):
    """Each variant loads from prompts/ and tells the LLM to use {{first_name}}."""
    assert "{{first_name}}" in _load_prompt(version)


def test_v4_does_not_say_every_answer_is_reviewed():
    """v4 no longer says a human reviews or approves every answer (D47)."""
    text = _load_prompt("v4_approval_aware")
    assert "reviews your answer before anything is sent" not in text
    assert "has not yet approved" not in text
    assert "never say that a team member still has to approve it" in text
    assert "escalate" in text and "never execute" in text


def test_v4_can_be_selected_and_runs_an_agent(monkeypatch):
    """With PROMPT_VERSION=v4_approval_aware an agent run completes with the fake LLM."""
    monkeypatch.setattr(config, "PROMPT_VERSION", "v4_approval_aware")
    llm = FakeLLM(und("return", "ORD-1001"), lambda c, n: draft("refund"))
    r = Agent(llm).resolve("Return ORD-1001 please", "C-001")
    assert r.prompt_version == "v4_approval_aware" and r.recommendation == "refund"
    assert r.requires_human_approval is False


def test_v5_says_the_system_executes_and_adds_the_reference():
    """v5 tells the model the system carries out the action and adds the reference itself (D50)."""
    text = _load_prompt("v5_executing")
    assert "carries out the approved action itself" in text
    assert "Do not write a reference number" in text
    assert "never say that a team member still has to approve it" in text
    assert "never execute" not in text
