"""Fault injection for tests and demos.

Fault injection means breaking a tool on purpose to check the agent copes with failures.
Where it sits: each tool calls `injected_fault` first and returns the fake error if one is set.

Set env var FAULT_INJECTION="lookup_order:timeout,check_eligibility:disabled".
It is read on every call so tests can switch faults on and off.
"""

import os

from app.tools.results import ToolError


def injected_fault(tool_name: str) -> ToolError | None:
    """Return a ToolError if a fault is configured for this tool, else None.

    Args:
        tool_name: Name of the calling tool, for example "lookup_order".
    """
    # Each entry looks like "tool:mode"; empty or unknown entries are skipped.
    for entry in os.getenv("FAULT_INJECTION", "").split(","):
        name, _, mode = entry.strip().partition(":")
        if name != tool_name:
            continue
        if mode == "timeout":
            return ToolError(code="timeout", message=f"{tool_name} timed out (injected fault)")
        if mode == "disabled":
            return ToolError(code="disabled", message=f"{tool_name} is disabled (injected fault)")
    return None
