"""Check that the generated data matches the archetypes in the master prompt (section 5.4).

The test data is built so that each customer and order covers one situation (an "archetype"),
for example "delivered 10 days ago" or "opened consumable". Later tests depend on those facts.
This script guards them: if someone regenerates or edits the data and breaks one, it fails.
It reads data/customers.json and data/orders.json and writes nothing.

Run: uv run python scripts/seed_checks.py   (exit code 1 on any failure)
"""

import json
import sys
from datetime import date
from pathlib import Path

from app.config import AS_OF_DATE

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def days_ago(iso: str) -> int:
    """Count the days between an ISO date and the fixed "today" (AS_OF_DATE).

    Args:
        iso: A date text such as "2026-03-01".

    Returns:
        Whole days before AS_OF_DATE (negative means a date in the future).
    """
    return (AS_OF_DATE - date.fromisoformat(iso)).days


def run_checks() -> list[str]:
    """Run every data check.

    Returns:
        A list of failure messages (empty = all good).
    """
    customers = json.loads((DATA_DIR / "customers.json").read_text())
    orders = {o["order_id"]: o for o in json.loads((DATA_DIR / "orders.json").read_text())}
    cust_ids = {c["customer_id"] for c in customers}
    errors = []

    def check(cond, msg):
        """Record `msg` as a failure when `cond` is false."""
        if not cond:
            errors.append(msg)

    # --- Step 1: overall counts and customer rules ---
    check(cust_ids == {"C-001", "C-002"}, "expected exactly C-001 (Nora) and C-002 (Milo)")
    check(len(orders) == 30, "expected 30 orders")
    check(all(c["email"].endswith("@example.test") for c in customers), "email domain")
    check(
        all(c["refunds_last_90_days"] < 3 for c in customers),
        "no customer may have 3+ refunds (the rule is unit-tested, see D57)",
    )
    nora = sum(o["customer_id"] == "C-001" for o in orders.values())
    check(nora == 14 and len(orders) - nora == 16, "Nora owns 14 orders and Milo 16")
    for oid, o in orders.items():
        check(o["customer_id"] in cust_ids, f"{oid}: unknown customer")

    # --- Step 2: delivery dates. Each order id maps to how many days ago it was delivered.
    # These ages decide whether an order is inside or outside the return window ---
    delivered_days = {
        "ORD-1001": 10, "ORD-1002": 45, "ORD-1003": 29, "ORD-1004": 31, "ORD-1005": 5,
        "ORD-1006": 5, "ORD-1007": 3, "ORD-1008": 12, "ORD-1009": 8, "ORD-1016": 20,
        "ORD-1017": 200, "ORD-1018": 400, "ORD-1019": 15, "ORD-1021": 5, "ORD-1023": 6,
        "ORD-1024": 10, "ORD-1025": 12, "ORD-1027": 25, "ORD-1029": 7,
    }  # fmt: skip
    for oid, n in delivered_days.items():
        check(days_ago(orders[oid]["delivered_date"]) == n, f"{oid}: delivered {n} days ago")

    # --- Step 3: one specific check per archetype order (late, final sale, gift card...) ---
    check(days_ago(orders["ORD-1010"]["estimated_delivery_date"]) == 9, "ORD-1010 est 9 days ago")
    check(days_ago(orders["ORD-1011"]["estimated_delivery_date"]) == 1, "ORD-1011 est 1 day ago")
    check(days_ago(orders["ORD-1012"]["estimated_delivery_date"]) == 4, "ORD-1012 est 4 days ago")
    check(orders["ORD-1012"]["shipping_paid"] == 250.0, "ORD-1012 shipping ₱250")
    check(orders["ORD-1013"]["status"] == "processing", "ORD-1013 processing")
    check(orders["ORD-1014"]["status"] == "shipped", "ORD-1014 shipped")
    check(days_ago(orders["ORD-1014"]["shipped_date"]) == 1, "ORD-1014 shipped yesterday")
    check(orders["ORD-1015"]["status"] == "processing", "ORD-1015 processing")
    check(orders["ORD-1005"]["items"][0]["final_sale"], "ORD-1005 final sale")
    check(orders["ORD-1006"]["items"][0]["final_sale"], "ORD-1006 final sale")
    check(orders["ORD-1019"]["items"][0]["category"] == "gift_card", "ORD-1019 gift card")
    check(days_ago(orders["ORD-1020"]["order_date"]) == 12, "ORD-1020 ordered 12 days ago")
    check(orders["ORD-1020"]["items"][0]["current_price"] == 5350.0, "ORD-1020 price drop")
    # ₱5,000 is the most an agent may refund without a manager (see the refund policy).
    check(orders["ORD-1021"]["items"][0]["price"] > 5000, "ORD-1021 above agent limit")
    check(orders["ORD-1022"]["customer_id"] == "C-002", "ORD-1022 owned by Milo")
    check(orders["ORD-1024"]["refunded_amount"] == 1000.0, "ORD-1024 ₱1,000 refunded")
    check(orders["ORD-1025"]["shipping_region"] == "Bulacan", "ORD-1025 delivered to Bulacan")
    check(
        sum(o["shipping_region"] != "Metro Manila" for o in orders.values()) == 1,
        "only ORD-1025 is delivered outside Metro Manila",
    )
    check(orders["ORD-1026"]["status"] == "cancelled", "ORD-1026 cancelled")
    check(days_ago(orders["ORD-1026"]["refund_date"]) == 3, "ORD-1026 refund 3 days ago")
    check(orders["ORD-1027"]["items"][0]["opened"], "ORD-1027 opened consumable")
    check(orders["ORD-1028"]["items"][0]["personalised"], "ORD-1028 personalised")
    check(len(orders["ORD-1029"]["items"]) == 2, "ORD-1029 two items")
    check(orders["ORD-1029"]["customer_id"] == "C-002", "ORD-1029 owned by Milo")
    check(orders["ORD-1030"]["chargeback_open"], "ORD-1030 chargeback")
    check("ORD-9999" not in orders, "ORD-9999 must not exist")
    return errors


if __name__ == "__main__":
    # Exit code 1 on any failure, so a CI job or a shell script can detect it.
    problems = run_checks()
    for p in problems:
        print("FAIL:", p)
    print("seed_checks: OK" if not problems else f"seed_checks: {len(problems)} failure(s)")
    sys.exit(1 if problems else 0)
