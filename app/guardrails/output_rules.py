"""Output guardrail layer: checks on the drafted answer before it reaches the human reviewer.

Where it sits in the flow: the agent drafts a reply, then runs these checks on it. A problem
here makes the agent reject or fix the draft instead of showing it.

Rule-consistency with the eligibility engine and the structure check live in the graph
(`_check`, `_finalise`). This module adds: citation grounding, moderation, profanity, competitor
mentions, PII leaks and a soft check on peso amounts.
"""

import re

from app import config
from app.guardrails.input_rules import PROFANITY_THRESHOLD, _profanity_score
from app.guardrails.pii import mask_pii
from app.guardrails.types import GuardEvent
from app.observability.logging import log_event

# Other stores the assistant must never mention (a business rule for this shop).
COMPETITORS = ["Amazon", "Walmart", "Target", "Best Buy", "eBay", "Costco", "AliExpress"]
# Whole-word, case-insensitive match; re.escape handles names with spaces or dots.
_COMPETITOR_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in COMPETITORS) + r")\b", re.IGNORECASE
)
# A peso sign (or "PHP"), then an amount such as 1,200 or 49.99. The capture group is just the
# number. A stray dollar sign counts too, so an amount in the wrong currency is still checked.
_AMOUNT_RE = re.compile(r"(?:₱|\$|PHP|Php)\s?(\d+(?:,\d{3})*(?:\.\d{1,2})?)")


# A money word, then (within 25 characters, no peso sign or digit in between) a number with no
# currency sign in front, such as "fee of 1200.00" or "refund 1,500". `(?<![\w.-])` keeps order ids
# like ORD-1001 out. Only numbers with 3+ digits or a decimal part count, so "5 to 7 days" is safe.
_BARE_AMOUNT_RE = re.compile(
    r"\b(?:fee|fees|refund(?:ed)?|deduct\w*|charge[sd]?|price|cost|credit|pay(?:ment)?|amount|"
    r"total|shipping)\b[^.\n\d₱$]{0,25}?(?<![\w.-])(\d+(?:,\d{3})*(?:\.\d{1,2})?)(?![\d-])",
    re.IGNORECASE,
)


def openai_moderation(text: str) -> bool | None:
    """Ask the OpenAI moderation endpoint whether a text is harmful.

    Args:
        text: The text to check.

    Returns:
        True if flagged, False if not, None if moderation is off or could not be reached.
    """
    if not config.MODERATION_ENABLED:
        return None
    try:
        from openai import OpenAI

        out = OpenAI().moderations.create(model=config.MODERATION_MODEL, input=text)
        return bool(out.results[0].flagged)
    except Exception as exc:  # noqa: BLE001 - fail open, but say so: the other checks still run
        log_event("moderation_unavailable", error=type(exc).__name__)
        return None


def _numbers(text: str) -> set[float]:
    """Collect every number in a text as a float (thousand separators removed).

    Args:
        text: Any text.

    Returns:
        A set of the numbers found.
    """
    return {float(m.replace(",", "")) for m in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", text)}


class OutputGuard:
    """Checks one drafted answer. `moderate` is injectable so tests stay offline."""

    def __init__(self, moderate=openai_moderation, extra_checks=None):
        """Set up the guard.

        Args:
            moderate: Function text -> bool|None. Tests pass a fake so no network is needed.
            extra_checks: Optional extra functions text -> (problem, event), such as the
                Guardrails AI profanity check from hub.py.
        """
        self.moderate = moderate
        self.extra_checks = extra_checks or []  # Guardrails AI validators (see hub.py)

    def grounding(self, citations, chunk_ids: set[str]) -> list[str]:
        """Check that every cited policy chunk was really retrieved (no invented sources).

        Args:
            citations: The citations the LLM wrote (each has a `chunk_id`).
            chunk_ids: The chunk ids the retriever actually returned.

        Returns:
            A list with one problem message, or an empty list when all citations are real.
        """
        bad = [c.chunk_id for c in citations if c.chunk_id not in chunk_ids]
        return [f"cites chunk ids that were not retrieved: {bad}"] if bad else []

    def content(self, texts: list[str]) -> tuple[list[str], list[GuardEvent]]:
        """Moderation, profanity and competitor checks on the text a customer would read.

        Args:
            texts: The customer-facing pieces of the answer (empty ones are ignored).

        Returns:
            A tuple (problems, events). Both are empty when the text is fine.
        """
        problems: list[str] = []
        events: list[GuardEvent] = []
        # Check everything as one block so a word split across fields is still seen in context.
        joined = "\n".join(t for t in texts if t)
        # --- Check 1: competitor names ---
        if _COMPETITOR_RE.search(joined):
            problems.append("mentions a competitor; do not refer to other stores")
            events.append(GuardEvent("output", "competitor_mention", "reject"))
        # --- Check 2: profanity (same 0.92 threshold as the input layer) ---
        score = _profanity_score(joined)
        if score >= PROFANITY_THRESHOLD:
            problems.append("contains offensive language")
            events.append(GuardEvent("output", "profanity", "reject", score))
        # --- Check 3: OpenAI moderation (None means "could not ask", so it does not reject) ---
        if self.moderate(joined):
            problems.append("flagged by the moderation check")
            events.append(GuardEvent("output", "moderation", "reject", source="moderation"))
        # --- Check 4: optional extra validators ---
        for check in self.extra_checks:
            problem, event = check(joined)
            if problem:
                problems.append(problem)
                events.append(event)
        return problems, events

    @staticmethod
    def mask_leaks(text: str) -> tuple[str, list[GuardEvent]]:
        """Mask any PII that appears in the answer (it is masked, not rejected).

        Args:
            text: A piece of the drafted answer.

        Returns:
            A tuple (masked text, events), with one "mask" event per PII type found.
        """
        masked, found = mask_pii(text)
        return masked, [
            GuardEvent("output", f"pii_{k.lower()}", "mask", n) for k, n in found.items()
        ]

    @staticmethod
    def bare_amounts(texts: list[str], case_text: str, amount) -> list[str]:
        """Check money figures written without a peso sign (for example "a fee of 1200.00").

        The sign-based check above never sees these. A figure counts only when it follows a money
        word ("fee", "refund", "deduct"...), has 3+ digits or a decimal part, and appears nowhere
        in the case data or as the engine's amount. This is a hard check: the draft is repaired.

        Args:
            texts: The customer-facing pieces of the answer.
            case_text: The order, eligibility and policy text the answer was based on.
            amount: The refund amount the engine computed, or None.

        Returns:
            One problem message naming the figures, or an empty list.
        """
        known = _numbers(case_text) | ({float(amount)} if amount is not None else set())
        found: set[float] = set()
        for text in texts:
            for raw in _BARE_AMOUNT_RE.findall(text or ""):
                digits = raw.split(".")[0].replace(",", "")
                if (len(digits) >= 3 or "." in raw) and float(raw.replace(",", "")) not in known:
                    found.add(float(raw.replace(",", "")))
        if not found:
            return []
        shown = ", ".join(f"{v:g}" for v in sorted(found))
        return [
            f"states amounts that are not in the case information ({shown}); use only the "
            f"amounts given there, written with the {config.CURRENCY_SYMBOL} sign"
        ]

    @staticmethod
    def unsupported_amounts(texts: list[str], case_text: str, amount) -> list[str]:
        """Soft check: peso figures in the answer that appear nowhere in the case data.

        Args:
            texts: The customer-facing pieces of the answer.
            case_text: The order and policy text the answer was based on.
            amount: The refund amount the engine computed, or None.

        Returns:
            Peso figures like "₱12.5" that the answer shows but the case data never has.
            This is only a warning: it may be a harmless calculation.
        """
        known = _numbers(case_text) | ({float(amount)} if amount is not None else set())
        shown = {float(m.replace(",", "")) for t in texts if t for m in _AMOUNT_RE.findall(t)}
        return [f"{config.CURRENCY_SYMBOL}{v:g}" for v in sorted(shown - known)]
