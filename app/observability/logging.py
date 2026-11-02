"""Structured JSON logging to stdout. Callers must pass already-masked content only.

"Structured" means each log line is one JSON object (time, event name, a few fields) instead of
free text, so tools can search and count them.

Where it sits in the flow: every other module calls `log_event()` to record what happened
(cache hit, guardrail fired, error). The API sets a request id so all lines for one request can
be joined together.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

# Fields allowed in a log line; anything else is dropped so raw text cannot leak by accident.
# This allow-list is the privacy safety net: a caller cannot log a customer message by mistake.
ALLOWED_FIELDS = {
    "request_id",
    "session_id_hash",
    "intent",
    "order_id",
    "tools_called",
    "cache_hit",
    "cache_tier",
    "guardrail_events",
    "latency_ms",
    "tokens_in",
    "tokens_out",
    "est_cost",
    "status",
    "error",
    "tool",
    "stage",
    "attempt",
    "recommendation",
    "review_action",
    "case_id",
    "layer",  # guardrail_triggered events: which layer, rule and action, never the text
    "rule",
    "action",
    "score",
    "source",
    "pii_found",
}


# A ContextVar is a variable with a separate value per request (also safe with async code).
# Set once per request; log_event adds it to every line written while handling that request.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class _StdoutHandler(logging.StreamHandler):
    """Writes to the current sys.stdout (looked up on every emit, so capture/redirect works)."""

    def __init__(self):
        """Start the handler on the current stdout."""
        super().__init__(sys.stdout)

    @property
    def stream(self):
        """Always return the live sys.stdout, so test tools that swap stdout still see logs."""
        return sys.stdout

    @stream.setter
    def stream(self, _value):
        # Ignore attempts to set the stream; the getter above always wins. (A docstring here
        # would make ruff flag the `pass`, and removing `pass` would be a code change.)
        pass


class JsonFormatter(logging.Formatter):
    """Formats a log record as one JSON line, keeping only the allowed fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Turn a log record into a JSON string.

        Args:
            record: The log record. Extra fields are read from `record.fields`.

        Returns:
            One JSON object: ts, level, event, plus the allowed fields.
        """
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        extra = getattr(record, "fields", {})
        entry.update({k: v for k, v in extra.items() if k in ALLOWED_FIELDS})
        # default=str: never crash on a value JSON cannot encode (a date, a Path...).
        return json.dumps(entry, default=str)


def get_logger(name: str = "resolveright") -> logging.Logger:
    """Return the app logger, setting it up the first time.

    Args:
        name: Logger name.

    Returns:
        A logger that writes JSON lines to stdout.
    """
    logger = logging.getLogger(name)
    # Only add a handler once, or every line would be printed several times.
    if not logger.handlers:
        handler = _StdoutHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        # Do not pass records to the root logger, which would print a second, unformatted copy.
        logger.propagate = False
    return logger


def log_event(event: str, **fields) -> None:
    """Write one structured log line.

    Args:
        event: Short event name, such as "llm_cache".
        **fields: Extra values. Only names in ALLOWED_FIELDS are kept.
    """
    # Attach the current request id automatically, unless the caller gave one.
    if request_id_var.get() and "request_id" not in fields:
        fields["request_id"] = request_id_var.get()
    get_logger().info(event, extra={"fields": fields})
