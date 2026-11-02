"""Shared guardrail types.

Small data classes used by the input rules, the output rules and the agent graph, so they all
describe a guardrail decision in the same way.
"""

from dataclasses import dataclass, field

from app.observability.logging import log_event

# Placeholder for the customer's first name. The LLM and our canned replies write it, and the
# agent's render step swaps in the real name just before the reply is shown.
FIRST_NAME = "{{first_name}}"


@dataclass
class GuardEvent:
    """One guardrail firing. Never holds customer text: only the layer, the rule and the action."""

    layer: str  # input | tool_data | output
    rule: str
    action: str  # block | escalate | mask | reject | drop | warn
    score: float = 1.0
    source: str = "rules"  # rules | guardrails_ai | moderation

    def as_dict(self) -> dict:
        """Convert the event to a plain dict for the API response.

        Returns:
            A dict with layer, rule, action, score and source.
        """
        return {
            "layer": self.layer,
            "rule": self.rule,
            "action": self.action,
            # Round so scores like 0.9234567 stay short and readable.
            "score": round(self.score, 3),
            "source": self.source,
        }

    def log(self) -> None:
        """Write this event to the structured log (it holds no customer text)."""
        log_event(
            "guardrail_triggered",
            layer=self.layer,
            rule=self.rule,
            action=self.action,
            score=round(self.score, 3),
            source=self.source,
        )


@dataclass
class InputVerdict:
    """What the input layer decided. `message` is always the masked text."""

    action: str  # allow | block | escalate
    message: str
    rule: str | None = None
    reply: str | None = None
    escalation_reason: str | None = None
    events: list[GuardEvent] = field(default_factory=list)
