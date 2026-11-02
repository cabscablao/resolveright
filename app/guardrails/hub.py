"""Guardrails AI validators (course Session 9) as a second layer on top of the rules.

Where it sits in the flow: the input layer (input_rules.py) and the output layer
(output_rules.py) call this only after their own regex rules found nothing. It is slower than the
rules because it can load a model or call an LLM.

On by default since 2026-11-02 (GUARDRAILS_AI_ENABLED=false turns it off; render.yaml does that
for the 512 MB free plan). Its telemetry is silenced without touching Langfuse (see
`silence_telemetry`). Each validator is built on first use in its own
try/except, so one that cannot load (model download, library conflict) never stops the others or a
request: it is reported in `status()` and the rule layers carry on alone.

Used:   DetectJailbreak (local Hugging Face model), ProfanityFree, RestrictToTopic (LLM mode).
Tried and not usable here: GuardrailsPII and CompetitorCheck. Both load a spaCy model, and spaCy
3.8.16 fails on it with "OverflowError: Python int too large to convert to C ssize_t". The regex
PII masker (`pii.py`) and the competitor list (`output_rules.py`) do those jobs. See
docs/guardrails.md.
"""

import os
import re

from app import config
from app.guardrails.types import GuardEvent
from app.observability.logging import log_event

# Words that show a message is about the store. The topic check is an LLM call (slow and paid),
# so we only run it on messages that contain none of these.
_STORE_WORDS = re.compile(
    r"ORD-\d{4}|\b(order|refund|return|deliver\w*|ship\w*|package|parcel|policy|warranty|exchange|"
    r"cancel\w*|item|product|receipt|price|charge\w*|broken|damaged|defective|late)\b",
    re.IGNORECASE,
)
# Topic lists for the RestrictToTopic validator.
VALID_TOPICS = ["online shopping", "orders", "returns and refunds", "delivery", "store policies"]
INVALID_TOPICS = ["politics", "gambling", "cryptocurrency", "medical advice", "legal advice"]


class _NullSpanExporter:
    """An OpenTelemetry span exporter that drops every span (nothing leaves the machine)."""

    def export(self, spans):
        """Pretend to send the spans.

        Args:
            spans: The finished spans (ignored).

        Returns:
            The SUCCESS result, so the span processor does not retry.
        """
        from opentelemetry.sdk.trace.export import SpanExportResult

        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        """Nothing to close."""

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Nothing is buffered.

        Args:
            timeout_millis: Ignored.

        Returns:
            True, always.
        """
        return True


def silence_telemetry() -> None:
    """Stop Guardrails AI from sending usage spans to its own third-party endpoint.

    Setting `enable_metrics` to False is not enough: the library still exports spans to
    hty0gc1ok3.execute-api.us-east-1.amazonaws.com (checked on 2026-11-02 by counting export
    calls). The old fix, OTEL_SDK_DISABLED=true, also switched Langfuse tracing off (D45), so
    instead only the library's own exporter is replaced with one that drops everything. Langfuse
    and the rest of the app keep their normal OpenTelemetry setup.
    """
    try:
        from guardrails.settings import settings
        from guardrails.utils import hub_telemetry_utils

        settings.rc.enable_metrics = False
        # HubTelemetry builds its exporter by this name the first time a Guard is created.
        hub_telemetry_utils.OTLPSpanExporter = lambda *args, **kwargs: _NullSpanExporter()
    except Exception as exc:  # noqa: BLE001 - optional layer, must never break the app
        log_event("guardrails_ai_unavailable", stage="telemetry", error=type(exc).__name__)


class HubGuards:
    """Builds and runs the optional Guardrails AI validators, tolerating any that fail to load."""

    def __init__(self):
        """Silence the library's telemetry and prepare empty validator tables."""
        os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
        silence_telemetry()
        self._guards: dict[str, object] = {}
        self._errors: dict[str, str] = {}

    def warm_up(self) -> dict[str, str]:
        """Build every validator now, so the first customer message does not wait for a model.

        Returns:
            The same report as `status()`.
        """
        for name in ("jailbreak", "profanity", "topic"):
            self._guard(name)
        return self.status()

    def _guard(self, name: str):
        """Build one guard on first use. Returns None if it cannot be built.

        Args:
            name: "jailbreak", "profanity" or "topic".

        Returns:
            The ready Guard, or None if it failed to build (the failure is remembered).
        """
        if name in self._guards:
            return self._guards[name]
        # A guard that already failed is not retried: a retry would repeat a slow failure.
        if name in self._errors:
            return None
        try:
            from guardrails import Guard

            # on_fail="noop" means the validator only reports; our code decides what to do.
            if name == "jailbreak":
                from guardrails_ai.detect_jailbreak import DetectJailbreak

                guard = Guard().use(DetectJailbreak(use_local=True, on_fail="noop"))
            elif name == "profanity":
                from guardrails_ai.profanity_free import ProfanityFree

                guard = Guard().use(ProfanityFree(on_fail="noop"))
            elif name == "topic":
                from guardrails_ai.restricttotopic import RestrictToTopic

                guard = Guard().use(
                    RestrictToTopic(
                        valid_topics=VALID_TOPICS,
                        invalid_topics=INVALID_TOPICS,
                        disable_classifier=True,
                        disable_llm=False,
                        on_fail="noop",
                    )
                )
            else:
                raise ValueError(name)
        except Exception as exc:  # noqa: BLE001 - optional layer, must never break a request
            self._errors[name] = f"{type(exc).__name__}: {str(exc)[:120]}"
            log_event("guardrails_ai_unavailable", stage=name, error=type(exc).__name__)
            return None
        self._guards[name] = guard
        return guard

    def status(self) -> dict[str, str]:
        """Report which validators are ready and which failed to load.

        Returns:
            A dict like {"jailbreak": "ready", "topic": "unavailable (ImportError: ...)"}.
        """
        out = {name: "ready" for name in self._guards}
        out.update({name: f"unavailable ({err})" for name, err in self._errors.items()})
        return out

    def _passed(self, name: str, text: str) -> bool | None:
        """Run one validator on a text.

        Args:
            name: Validator name (see `_guard`).
            text: The text to check.

        Returns:
            True if the text passed, False if it failed, None if the validator is unavailable
            or raised an error (so the caller can tell "failed" from "could not check").
        """
        guard = self._guard(name)
        if guard is None:
            return None
        try:
            return bool(guard.validate(text).validation_passed)
        except Exception as exc:  # noqa: BLE001
            log_event("guardrails_ai_error", stage=name, error=type(exc).__name__)
            return None

    def screen_input(self, text: str) -> tuple[str, str, GuardEvent] | None:
        """Run the input validators on the masked customer message.

        Args:
            text: The message after PII masking.

        Returns:
            (action, rule, event) if a validator rejects the message, otherwise None.
        """
        # `is False` (not `not`) so that None ("could not check") never counts as a rejection.
        if self._passed("jailbreak", text) is False:
            ev = GuardEvent("input", "jailbreak_model", "block", source="guardrails_ai")
            return "block", "prompt_injection", ev
        if self._passed("profanity", text) is False:
            ev = GuardEvent("input", "profanity_free", "escalate", source="guardrails_ai")
            return "escalate", "harassment", ev
        # The topic check is an LLM call, so only ask it about messages with no store vocabulary.
        if not _STORE_WORDS.search(text) and self._passed("topic", text) is False:
            ev = GuardEvent("input", "restrict_to_topic", "block", source="guardrails_ai")
            return "block", "off_topic", ev
        return None

    def check_output(self, text: str):
        """Extra check for OutputGuard: reject offensive language in the drafted answer.

        Args:
            text: The drafted answer text.

        Returns:
            (problem, event), or (None, None) when the text is fine.
        """
        if self._passed("profanity", text) is False:
            return (
                "contains offensive language",
                GuardEvent("output", "profanity_free", "reject", source="guardrails_ai"),
            )
        return None, None


# The single shared instance, created on first use so loading models happens at most once.
_hub: HubGuards | None = None


def get_hub() -> HubGuards | None:
    """Return the shared Guardrails AI validators.

    Returns:
        A HubGuards object, or None when GUARDRAILS_AI_ENABLED is off.
    """
    global _hub
    if not config.GUARDRAILS_AI_ENABLED:
        return None
    if _hub is None:
        _hub = HubGuards()
    return _hub
