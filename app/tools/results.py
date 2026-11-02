"""Typed results shared by all tools. A tool returns either ToolOk or ToolError, never raises.

Where it sits: every tool in this package returns one of these, so the agent can check `.ok`
instead of wrapping each call in try/except.
"""

from typing import Any, Literal

from pydantic import BaseModel

# Short machine-readable failure reasons the agent can branch on.
ErrorCode = Literal[
    "not_found",  # also what someone else's order returns, so ids cannot be probed (D55)
    "timeout",
    "invalid_input",
    "disabled",
    "unavailable",
    "not_allowed",  # the rules do not approve this action or amount
    "already_resolved",  # the same action was already executed for this order
]


class ToolOk(BaseModel):
    """A successful tool call; the payload is in `data`."""

    ok: Literal[True] = True
    data: dict[str, Any]


class ToolError(BaseModel):
    """A failed tool call; `code` says why and `message` is safe to show."""

    ok: Literal[False] = False
    code: ErrorCode
    message: str
