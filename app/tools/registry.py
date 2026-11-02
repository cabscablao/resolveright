"""Tool names mapped to functions. The orchestrator (later) dispatches through this.

Where it sits: the agent looks up a tool by name here and calls it, so adding a tool means
adding one line to `TOOLS`.
"""

from app.tools.eligibility import check_eligibility
from app.tools.escalation import escalate_to_human
from app.tools.orders import lookup_order
from app.tools.policy_search import search_policy
from app.tools.resolution import execute_resolution

# Name (as the agent refers to it) -> the Python function that runs the tool.
TOOLS = {
    "search_policy": search_policy,
    "lookup_order": lookup_order,
    "check_eligibility": check_eligibility,
    "execute_resolution": execute_resolution,
    "escalate_to_human": escalate_to_human,
}
