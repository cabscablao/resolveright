"""Session memory: last 6 turns plus a CaseState, in memory only, 30-minute TTL.

Where it sits: the agent graph reads and updates a `Session` at the start and end of each
request so a follow-up message ("yes, that one") makes sense.

TTL (time to live) means a session is thrown away after it has been idle that long.
There is no long-term storage of customer data. Turns hold the (already masked) customer
message and the assistant's short question or recommendation, never names or addresses.
"""

import time
from dataclasses import dataclass, field

from app import config

# How many recent messages to keep (3 customer + 3 assistant); older ones are dropped.
MAX_TURNS = 6


@dataclass
class CaseState:
    """The facts we have learned so far about the customer's case."""

    customer_id: str
    order_id: str | None = None
    intent: str | None = None
    item_sku: str | None = None
    error_origin: str | None = None
    open_question: str | None = None  # a question we asked and still wait on


@dataclass
class Session:
    """One conversation: the case facts, recent turns and the last time it was used."""

    case: CaseState
    turns: list[tuple[str, str]] = field(default_factory=list)  # (role, text)
    # monotonic clock never jumps backwards, so it is safe for measuring idle time.
    touched: float = field(default_factory=time.monotonic)


class SessionStore:
    """Holds all live sessions in a dict and expires idle ones."""

    def __init__(self, ttl_seconds: int = config.SESSION_TTL_SECONDS, clock=time.monotonic):
        """Create an empty store.

        Args:
            ttl_seconds: How long a session may sit idle before it is dropped.
            clock: Function returning seconds; tests pass a fake to control time.
        """
        self.ttl, self._clock = ttl_seconds, clock
        self._sessions: dict[str, Session] = {}

    def get(self, session_id: str, customer_id: str) -> Session:
        """Return a live session; an expired one, or one for another customer, starts fresh.

        Args:
            session_id: Id the browser sends for this conversation.
            customer_id: Who is logged in; a session is never shared between customers.
        """
        now = self._clock()
        # Drop expired sessions on every call (simple; no background thread needed).
        self._sessions = {k: s for k, s in self._sessions.items() if now - s.touched < self.ttl}
        session = self._sessions.get(session_id)
        # Customer check stops one customer reusing another's session id.
        if session is None or session.case.customer_id != customer_id:
            session = Session(case=CaseState(customer_id=customer_id), touched=now)
            self._sessions[session_id] = session
        session.touched = now
        return session

    def add_turn(self, session: Session, role: str, text: str) -> None:
        """Append a turn, cut it to the max message length and keep only the last MAX_TURNS."""
        session.turns = (session.turns + [(role, text[: config.MAX_MESSAGE_CHARS])])[-MAX_TURNS:]

    def clear(self) -> None:
        """Forget every session (used by tests)."""
        self._sessions.clear()
