"""Abuse and cost protection: a per-customer rate limit and a global daily request budget.

Where it sits: the API calls `RequestLimiter.check` first, before any LLM call, so a flood of
requests cannot run up the OpenAI bill. A rate limit means "at most N requests per minute".

State is kept in memory (single process), so it resets when the server restarts.
"""

import time
from collections import defaultdict, deque
from datetime import UTC, datetime

from app import config


class LimitExceeded(Exception):
    """Raised when a request is over the rate limit or the daily budget."""

    def __init__(self, kind: str, message: str, retry_after: int):
        """Store the reason so the API can build a clear HTTP error.

        Args:
            kind: Which limit was hit, "rate_limit" or "daily_budget".
            message: Friendly text to show the customer.
            retry_after: Seconds the caller should wait before trying again.
        """
        super().__init__(message)
        self.kind, self.retry_after = kind, retry_after


class RequestLimiter:
    """Counts requests per key (per minute) and in total (per day)."""

    def __init__(
        self,
        per_minute: int = config.RATE_LIMIT_PER_MINUTE,
        daily_budget: int = config.DAILY_REQUEST_BUDGET,
        clock=time.monotonic,
        today=lambda: datetime.now(UTC).date(),
    ):
        """Set up empty counters.

        Args:
            per_minute: Max requests one key may make in any 60 second window.
            daily_budget: Max requests across all keys in one UTC day.
            clock: Function returning seconds; tests pass a fake to control time.
            today: Function returning today's date; tests pass a fake here too.
        """
        self.per_minute, self.daily_budget = per_minute, daily_budget
        self._clock, self._today = clock, today
        # One queue of request timestamps per key (a "sliding window").
        self._hits: dict[str, deque] = defaultdict(deque)
        self._day, self._used = today(), 0

    def check(self, key: str) -> None:
        """Count one request or raise LimitExceeded. Rejected requests are not counted.

        Args:
            key: Who is asking, for example the customer id.

        Raises:
            LimitExceeded: If the daily budget or the per-minute limit is used up.
        """
        # --- Step 1: reset the daily counter when the date changes ---
        if self._today() != self._day:
            self._day, self._used = self._today(), 0
        # --- Step 2: global daily budget ---
        if self._used >= self.daily_budget:
            raise LimitExceeded(
                "daily_budget",
                "The daily request budget has been reached. Try again tomorrow.",
                3600,  # a rough "try again in an hour" hint; the real reset is at midnight UTC
            )
        # --- Step 3: per-key sliding window ---
        now = self._clock()
        hits = self._hits[key]
        # Forget requests older than 60 s; they no longer count toward the limit.
        while hits and now - hits[0] >= 60:
            hits.popleft()
        if len(hits) >= self.per_minute:
            # The oldest hit leaves the window at hits[0] + 60; always ask for 1 s or more.
            wait = max(1, int(60 - (now - hits[0])))
            raise LimitExceeded(
                "rate_limit", f"Too many requests. Please wait {wait} seconds.", wait
            )
        # --- Step 4: accept and count the request ---
        hits.append(now)
        self._used += 1
