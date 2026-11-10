"""Routing check for messages that arrive while a case waits for staff (D58). No LLM, no cost.

It runs the examples of the processing-flow tables (sections 5.6 and 6.5) through the rule router
and compares the route with the expected one. The router is rules, so the result is the same on
every run. Writes docs/evidence/evals/followup_routing.json.

Run: uv run python -m eval.followup_eval   (exit code 1 if any example is routed differently)
"""

import json
import sys

from app.followup import route_message
from eval.common import RESULTS_DIR

# What the held case looks like: an escalated return of the chair, ORD-1021.
HELD = {
    "case_id": "CASE-0000AAAA", "order_id": "ORD-1021", "status": "escalated",
    "transcript": [{"role": "user", "content": "I want to return the ergonomic chair on ORD-1021"}],
}  # fmt: skip

# (id, message, expected route, where it comes from in the flow document)
EXAMPLES = [
    ("F01", "Any update on my return?", "status_check", "5.6 row 1"),
    ("F02", "The chair arrived with a cracked leg", "added_info", "5.6 row 2"),
    ("F03", "Just refund me now!", "repeat", "5.6 row 3"),
    ("F04", "What's your shipping policy?", "unrelated", "6.5 row 1"),
    ("F05", "I want to return the mouse from ORD-1002", "unrelated", "6.5 row 2"),
    ("F06", "How long does it take?", "ambiguous", "6.5 row 3"),
    ("F07", "Do you refund expensive items?", "unrelated", "6.5 row 4"),
]  # fmt: skip


def main() -> int:
    """Route every example, print one line each and save the result file."""
    rows = []
    for fid, message, expected, source in EXAMPLES:
        got = route_message(message, [HELD], "Ergonomic chair").kind
        rows.append({"id": fid, "message": message, "expected": expected, "got": got,
                     "ok": got == expected, "source": source})  # fmt: skip
        print(f"{fid} {'OK  ' if got == expected else 'FAIL'} {got:<13} {message}")
    ok = sum(r["ok"] for r in rows)
    out = RESULTS_DIR / "followup_routing.json"
    out.write_text(json.dumps({"passed": ok, "total": len(rows), "rows": rows}, indent=2), "utf-8")
    print(f"followup routing: {ok}/{len(rows)}")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
