"""Check how requests for a person are handled (D62). No network, no cost.

Two parts, both rule-based and repeatable:
1. Phrasings: which messages the keyword rule treats as "asks for a human" (and which it must not).
2. Hand-off: after an earlier chat about an order, a bare "talk to human staff" (with a scripted
   model that wrongly copies the old order) must be handed off with no order, item or guessed
   reason, and with no question back to the customer.

Writes eval/results/human_request.json.
Run: uv run python -m eval.human_request_eval   (exit code 1 if any check fails)
"""

import json
import sys
import tempfile
from pathlib import Path

from app import config
from app.agent.graph import Agent
from app.guardrails.input_rules import HUMAN_WORDS
from tests.test_agent import FakeLLM, draft, und

# (id, message, should the rule fire?)
PHRASINGS = [
    ("H-P01", "I want to talk to human staff", True),
    ("H-P02", "Can I speak to a real person please", True),
    ("H-P03", "connect me to an agent", True),
    ("H-P04", "I need a human", True),
    ("H-P05", "Let me talk to a manager about my refund", True),
    ("H-P06", "the human-sized mannequin is too big", False),
    ("H-P07", "I want to return the shirt from ORD-1001", False),
    ("H-P08", "Where is my order?", False),
]  # fmt: skip


def _handoff_check() -> dict:
    """Run the two-turn conversation with the scripted model and check the hand-off."""
    config.ESCALATION_LOG = Path(tempfile.mkdtemp()) / "escalations.jsonl"
    llm = FakeLLM(
        [
            und("damaged", "ORD-1001", item_sku="SKU-X", summary="damaged mug"),
            und("policy_question", "ORD-1001", item_sku="SKU-X", summary="asks about their order"),
        ],
        lambda c, n: draft("need_info", question="Which item?"),
    )
    agent = Agent(llm, semantic=False)  # no cache: this check needs no vector store
    agent.resolve("My mug arrived damaged, ORD-1001", "C-001", session_id="eval-h")
    r = agent.resolve("I want to talk to human staff", "C-001", session_id="eval-h")
    req = r.handoff.request if r.handoff else None
    checks = {
        "escalated": r.recommendation == "escalate",
        "reason_human_requested": bool(r.escalation) and r.escalation.reason_code == "human_requested",
        "no_question_back": r.clarifying_question is None,
        "no_order": bool(req) and req.order_id is None,
        "no_item": bool(req) and req.item_sku is None,
        "no_guessed_reason": bool(req) and req.summary == "Customer asked to talk to a human; no reason given.",
    }  # fmt: skip
    return {"id": "H-T01", "ok": all(checks.values()), "checks": checks}


def main() -> int:
    """Run both parts, print one line each and save the result file."""
    rows = []
    for pid, message, expected in PHRASINGS:
        got = bool(HUMAN_WORDS.search(message))
        rows.append({"id": pid, "message": message, "expected": expected, "got": got,
                     "ok": got == expected})  # fmt: skip
        print(f"{pid} {'OK  ' if got == expected else 'FAIL'} fires={got!s:<5} {message}")
    hand = _handoff_check()
    print(
        f"{hand['id']} {'OK  ' if hand['ok'] else 'FAIL'} hand-off after an order chat {hand['checks']}"
    )
    ok = sum(r["ok"] for r in rows) + int(hand["ok"])
    total = len(rows) + 1
    out = config.ROOT_DIR / "eval" / "results" / "human_request.json"
    out.write_text(
        json.dumps({"passed": ok, "total": total, "phrasings": rows, "handoff": hand}, indent=2),
        "utf-8",
    )
    print(f"human request: {ok}/{total}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
