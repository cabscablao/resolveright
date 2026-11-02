"""Run 5 end-to-end scenarios through the agent (gate for W1 Weekday 5).

Each scenario is a customer message that the agent should handle in a known way.
The script prints what the agent decided next to what we expected, so a person can compare.
It writes nothing to disk. It is a demo, not an automatic test (see eval/eval_tools.py).

Run: uv run python -m scripts.run_scenarios            (real LLM, needs OPENAI_API_KEY)
     uv run python -m scripts.run_scenarios "message" C-001   (one custom message)
The agent carries out approved in-policy resolutions (a mock: a ledger line and a runtime order
change under logs/), so running a scenario twice shows "already processed" the second time.
"""

import json
import sys

from app.agent.graph import Agent
from app.tools import policy_search
from app.tools.orders import get_customer

# (customer id, message, the outcome we expect, written as plain text for the reader)
SCENARIOS = [
    ("C-001", "Hi, I'd like to return the headphones from order ORD-1001, I changed my mind.",
     "refund, max ₱2,750.00 (within window, minus the ₱200 Metro Manila fee)"),
    ("C-001", "I want to return the ergonomic chair on ORD-1021, it doesn't suit me.",
     "escalate (above ₱5,000 agent limit)"),
    ("C-001", "Can I return the protein supplement from ORD-1027? I opened it.",
     "deny (opened consumable)"),
    ("C-001", "My mug from ORD-1028 has my name spelled wrong.",
     "need_info (who made the error?)"),
    ("C-001", "How long do I have to return something after it arrives?",
     "inform, cites POL-01"),
]  # fmt: skip


def show(customer_id: str, message: str, expected: str | None = None) -> None:
    """Run one message through the agent and print the outcome.

    Args:
        customer_id: The customer id, such as "C-001".
        message: The customer's message.
        expected: Optional plain-text description of the expected outcome, shown for comparison.
    """
    result = Agent().resolve(message, customer_id)
    # The draft reply contains a {{first_name}} placeholder; fill it in so it reads naturally.
    first = (get_customer(customer_id) or {}).get("first_name", "there")
    print("=" * 78)
    print(f"[{customer_id}] {message}")
    if expected:
        print(f"expected: {expected}")
    print(
        f"-> {result.recommendation} | amount={result.amount} | approval={result.approval_level}"
        f" | guardrail={result.guardrail_status} | confidence={result.confidence}"
    )
    print("tools:", " -> ".join(f"{t['tool']}({'ok' if t['ok'] else t['error']})"
                                for t in result.tool_trace))  # fmt: skip
    print("citations:", [c.chunk_id for c in result.policy_citations])
    print("rationale:", result.rationale)
    print("reply:", result.customer_reply_draft.replace("{{first_name}}", first))
    if result.escalation:
        print("escalation:", json.dumps(result.escalation.model_dump()))


def main() -> int:
    """Run the built-in scenarios, or one custom message if two arguments are given.

    Returns:
        The process exit code (always 0; this script only shows results).
    """
    sys.stdout.reconfigure(encoding="utf-8")  # the Windows console cannot print "₱" by default
    policy_search.warm_up()  # a cold first search would use up the 30 s request deadline
    # Custom mode: the message is the first argument and the customer id is the second.
    if len(sys.argv) == 3:
        show(sys.argv[2], sys.argv[1])
        return 0
    for customer_id, message, expected in SCENARIOS:
        show(customer_id, message, expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
