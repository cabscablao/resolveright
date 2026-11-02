"""Minimal command-line client for the ResolveRight API.

It sends one customer message to a running server, prints the key fields of the answer
and can optionally review (approve, edit or escalate) the resulting case.
It writes nothing to disk. It needs the server to be running.

Run: uv run python -m scripts.client C-001 "I want to return something"
     uv run python -m scripts.client C-001 "It's ORD-1001" --session demo --review approve
Start the server first: uv run uvicorn app.server:app --port 8000
"""

import argparse
import json
import os

import httpx


def main() -> int:
    """Send a message to the API, print the answer and optionally submit a review.

    Returns:
        The process exit code (0 means success). HTTP errors raise an exception.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("customer_id")
    ap.add_argument("message")
    # A session id lets a follow-up message continue the same conversation.
    ap.add_argument("--session", default=None)
    ap.add_argument("--review", choices=["approve", "edit", "escalate"], default=None)
    ap.add_argument("--edited-reply", default=None)
    ap.add_argument("--url", default=os.getenv("RESOLVERIGHT_URL", "http://127.0.0.1:8000"))
    args = ap.parse_args()

    # 60 seconds, because a real model call can be slow.
    with httpx.Client(base_url=args.url, timeout=60) as http:
        # --- Step 1: ask the agent to resolve the message ---
        body = {"customer_id": args.customer_id, "message": args.message,
                "session_id": args.session}  # fmt: skip
        r = http.post("/api/v1/resolve", json=body)
        r.raise_for_status()
        result = r.json()
        # Print only the fields a person needs to read, not the whole response.
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "recommendation",
                        "amount",
                        "approval_level",
                        "review_status",
                        "case_id",
                        "clarifying_question",
                        "customer_reply_preview",
                        "notices",
                    )
                },
                indent=2,
            )
        )

        # --- Step 2: optional human review. Only a case that is still "pending" can be reviewed ---
        if args.review and result["review_status"] == "pending":
            r = http.post("/api/v1/review", json={
                "case_id": result["case_id"], "action": args.review,
                "edited_reply": args.edited_reply, "customer_id": args.customer_id,
            })  # fmt: skip
            r.raise_for_status()
            print("review ->", r.json()["review_status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
