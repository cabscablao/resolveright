"""Open-case check and message routing (processing flow, Scenarios 3 and 4, D58).

Where it sits: `cases.handle_message` calls `route_message` before anything else. A customer may
already have a case waiting for staff (status `escalated` or `in_review`). Then a new message is
one of:
  - a follow-up on that case (Scenario 3): a status question, new information, or a repeat or
    pressure. It is answered from a fixed template or appended to the case. The agent is NOT run,
    so the chatbot can never decide, promise or change a case that is waiting for staff;
  - something unrelated (Scenario 4): a policy question, or a request about another order. It goes
    through the normal pipeline and the open case stays untouched;
  - unclear: the customer is asked one short question and nothing changes.

The router is plain rules (word lists below), with no LLM: it is free, instant and testable, and
a wrong guess can only ever send a message to the normal pipeline or ask a question. Wording the
lists miss is treated as unrelated or unclear, never as a decision on the held case. Withdraw and
urgent follow-ups (processing flow 5.2, rows 4 and 5) are not built yet; they fall into the
normal pipeline, where the existing guardrails handle a request for a person.

This file has no I/O: it takes the customer's open cases and returns a Route.
"""

import re
from dataclasses import dataclass

# Customer is waiting for staff in these two statuses only. `need_info` ends as `completed`.
OPEN_STATUSES = ("escalated", "in_review")

# Kinds of route. normal and unrelated run the agent; the rest never do.
NORMAL, UNRELATED = "normal", "unrelated"
STATUS_CHECK, ADDED_INFO, REPEAT, AMBIGUOUS = "status_check", "added_info", "repeat", "ambiguous"

_ORDER_ID = re.compile(r"\bORD-\d{4}\b", re.IGNORECASE)
_CASE_ID = re.compile(r"\bCASE-[0-9A-F]{8}\b", re.IGNORECASE)

# Wording lists. Matching is on the lower-cased message.
STATUS_PHRASES = (
    "any update", "an update", "any news", "any word", "progress", "heard back", "follow up",
    "following up", "follow-up", "still waiting", "what happened", "status of", "case status",
    "update on", "updates on", "checking in",
)  # fmt: skip
REPEAT_PHRASES = (
    "just refund", "refund me now", "right now", "asap", "hurry", "immediately", "taking so long",
    "taking too long", "why is it taking", "again", "just do it", "i already asked",
)  # fmt: skip
ADDED_INFO_PHRASES = (
    "arrived", "cracked", "broken", "damaged", "defective", "scratched", "torn", "missing",
    "stopped working", "doesn't work", "does not work", "won't turn on", "also", "by the way",
    "forgot to mention", "i have a photo", "attached", "wrong colour", "wrong color",
)  # fmt: skip
# Words that tie a message without an order id to the held matter.
TOPIC_WORDS = ("return", "refund", "replacement", "replace", "exchange", "claim", "complaint",
               "my case", "my request", "my order")  # fmt: skip
# Words of a general question. A short pronoun question without these is unclear.
GENERAL_TOPICS = (
    "shipping", "delivery", "policy", "warranty", "store", "price", "payment", "gift card",
    "window", "hours", "fee", "cost", "express", "standard",
)  # fmt: skip
_PRONOUNS = re.compile(r"\b(it|that|this|they)\b")
_QUESTION_START = re.compile(r"^(how|when|what|where|why|is|does|will|can|do|did)\b")


@dataclass(frozen=True)
class Route:
    """The routing decision for one message.

    Attributes:
        kind: normal, unrelated, status_check, added_info, repeat or ambiguous.
        case: The held case the message relates to (None for normal).
        open_case_found: True when the customer had an open case at all.
    """

    kind: str
    case: dict | None = None
    open_case_found: bool = False

    @property
    def runs_agent(self) -> bool:
        """True when the normal pipeline (the agent) should answer this message."""
        return self.kind in (NORMAL, UNRELATED)


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    """True when any of the phrases appears in the text."""
    return any(w in text for w in words)


def route_message(message: str, open_cases: list[dict], item_names: str = "") -> Route:
    """Decide what a message is, given the customer's open cases.

    Args:
        message: What the customer typed.
        open_cases: The customer's cases that are escalated or in review, newest first.
        item_names: Product names of the newest case's order (for example "Ergonomic chair"), so
            a message that only names the item counts as about that case.

    Returns:
        The Route. With no open case it is always `normal`. With one, only the newest open case is
        considered (one open escalation per order, so a customer rarely has more than one).
    """
    if not open_cases:
        return Route(NORMAL)
    case = open_cases[0]
    text = message.lower()
    held_order = (case.get("order_id") or "").lower()
    order_ids = {m.lower() for m in _ORDER_ID.findall(message)}
    names_case = case["case_id"].lower() in text or any(
        c.lower() == case["case_id"].lower() for c in _CASE_ID.findall(message)
    )
    names_held_order = bool(held_order) and held_order in order_ids
    # A different order id (and not the held one) is a new request: Scenario 4.
    if order_ids and not names_held_order and not names_case:
        return Route(UNRELATED, case, True)

    repeat = _has_any(text, REPEAT_PHRASES)
    status = _has_any(text, STATUS_PHRASES)
    added = _has_any(text, ADDED_INFO_PHRASES)
    related = names_held_order or names_case
    if not related:
        # No id: it must be about the held matter by its words, and say something about it.
        about_topic = _has_any(text, TOPIC_WORDS) or _shares_item_word(text, case, item_names)
        if not (repeat or status or added) or not (about_topic or status):
            if _is_unclear(text):
                return Route(AMBIGUOUS, case, True)
            return Route(UNRELATED, case, True)
    if repeat:
        return Route(REPEAT, case, True)
    if status and not added:
        return Route(STATUS_CHECK, case, True)
    return Route(ADDED_INFO, case, True)


def _shares_item_word(text: str, case: dict, item_names: str = "") -> bool:
    """True when the message uses a word from the held case's first message or its item names."""
    first = next((t["content"] for t in case.get("transcript", []) if t["role"] == "user"), "")
    first = f"{first} {item_names}"
    stop = {"that", "this", "with", "from", "have", "what", "your", "about", "want", "please"}
    words = {w for w in re.findall(r"[a-z]{4,}", first.lower()) if w not in stop}
    return any(w in words for w in re.findall(r"[a-z]{4,}", text))


def _is_unclear(text: str) -> bool:
    """A short question about "it" with no topic of its own, such as "How long does it take?"."""
    words = text.split()
    return (
        len(words) <= 8
        and bool(_QUESTION_START.match(text.strip()))
        and bool(_PRONOUNS.search(text))
        and not _has_any(text, GENERAL_TOPICS)
    )


# ---- fixed replies (no LLM). {{first_name}} is filled in by render_reply. ---------------------


def reply_for(route: Route) -> str:
    """The customer's reply for a route that does not run the agent.

    Args:
        route: A status_check, added_info, repeat or ambiguous route with its case.

    Returns:
        A short reply. It never states or hints at the decision on the held case.
    """
    case = route.case or {}
    case_id = case.get("case_id", "your case")
    reviewing = case.get("status") == "in_review"
    where = (
        f"A team member is reviewing your case {case_id} right now"
        if reviewing
        else f"Your case {case_id} is with our team"
    )
    if route.kind == STATUS_CHECK:
        return f"Hi {{{{first_name}}}}, {where}. We'll reply here as soon as they decide."
    if route.kind == REPEAT:
        return (
            f"Hi {{{{first_name}}}}, I understand you'd like this settled quickly. {where}, and "
            "only they can decide it, so I can't change the outcome. I haven't opened a second "
            "case. We'll reply here as soon as they decide."
        )
    if route.kind == ADDED_INFO:
        return (
            f"Hi {{{{first_name}}}}, I've added this to your case {case_id}, so our team will "
            "see it. We'll reply here as soon as they decide."
        )
    order = case.get("order_id")
    about = f"your case {case_id}" + (f" (order {order})" if order else "")
    return (
        f"Hi {{{{first_name}}}}, is this about {about}, or a general question, for example "
        "about shipping? Tell me which one and I'll help."
    )
