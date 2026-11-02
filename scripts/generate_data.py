"""Generate the synthetic Brightcart customers and orders.

"Synthetic" data means we made it up by code. It gives the tests a small shop (2 customers and
30 orders), where each order is built to trigger one situation, such as a late parcel or an
opened item. scripts/seed_checks.py verifies those situations afterwards.

Run: uv run python scripts/generate_data.py
It writes data/customers.json and data/orders.json (replacing them).
All dates are computed relative to AS_OF_DATE, so results are reproducible.
The data is fully synthetic (no real people, companies or payment data).
"""

import json
from datetime import timedelta
from pathlib import Path

from app import config
from app.config import AS_OF_DATE, FREE_SHIPPING_MIN, STANDARD_SHIPPING_FEE

# Fixed random seed, so the shuffled customer names are the same every run.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Only two customers exist. Nora is the demo user (the chat page has no customer picker) and owns
# every order that a scripted scenario needs. Milo exists only in the data: his orders let Nora
# ask about an order that is not hers, which tests the ownership guardrail (D55).
CUSTOMERS = [("C-001", "Nora"), ("C-002", "Milo")]

# Orders that belong to Nora. Every other order belongs to Milo.
NORA_ORDERS = {
    "ORD-1001", "ORD-1003", "ORD-1004", "ORD-1005", "ORD-1007", "ORD-1009", "ORD-1010",
    "ORD-1012", "ORD-1013", "ORD-1019", "ORD-1021", "ORD-1023", "ORD-1027", "ORD-1028",
}  # fmt: skip

# Refunds in the last 90 days. Both customers have 0: the "3 refunds" rule is a per-customer
# count, so giving it to Milo would escalate every refund of his. It is unit-tested instead (D57).
REFUNDS_90: dict[str, int] = {}


def day(n: int) -> str:
    """Build a date as text, n days before AS_OF_DATE.

    Args:
        n: Number of days back. A negative n gives a date in the future.

    Returns:
        The date in ISO format, such as "2026-03-01".
    """
    return (AS_OF_DATE - timedelta(days=n)).isoformat()


def item(sku, name, category, price, final_sale=False, personalised=False, opened=False, **extra):
    """Build one order line (one product).

    Args:
        sku: Product code.
        name: Product name.
        category: Product category, such as "apparel" or "electronics".
        price: Price paid, in pesos.
        final_sale: True if the item cannot be returned.
        personalised: True if the item was custom-made.
        opened: True if the item was opened (matters for consumables).
        **extra: Any other field to add, such as current_price.

    Returns:
        The item as a dict.
    """
    base = {
        "sku": sku,
        "name": name,
        "category": category,
        "price": price,
        "final_sale": final_sale,
        "personalised": personalised,
        "opened": opened,
    }
    base.update(extra)
    return base


def order(order_id, customer_id, items, status, delivered_ago=None, **kw):
    """Build one order. Dates are generated relative to the delivery or ship date.

    Args:
        order_id: Order id, such as "ORD-1001".
        customer_id: Id of the customer who owns the order.
        items: The order lines (from item()).
        status: Order status, such as "delivered" or "in_transit".
        delivered_ago: Days since delivery. If None, no delivery dates are filled in.
        **kw: Fields that override the defaults (dates, notes, refunds...).

    Returns:
        The order as a dict.
    """
    # The owner comes from NORA_ORDERS, whatever customer id the caller wrote.
    customer_id = "C-001" if order_id in NORA_ORDERS else "C-002"
    total = sum(i["price"] for i in items)
    # Shipping is free above FREE_SHIPPING_MIN, otherwise a flat fee, unless the caller sets
    # shipping_paid.
    shipping = kw.pop("shipping_paid", 0.0 if total > FREE_SHIPPING_MIN else STANDARD_SHIPPING_FEE)
    o = {
        "order_id": order_id,
        "customer_id": customer_id,
        "items": items,
        "order_date": None,
        "status": status,
        "shipped_date": None,
        "estimated_delivery_date": None,
        "delivered_date": None,
        "shipping_paid": shipping,
        "shipping_region": config.METRO_MANILA,
        "refunded_amount": 0.0,
        "refund_date": None,
        "notes": "",
        "chargeback_open": False,
    }
    # Delivered orders get a realistic timeline: shipped 5 days and ordered 8 days before delivery,
    # with the estimated delivery 1 day before the real one.
    if delivered_ago is not None:
        o["delivered_date"] = day(delivered_ago)
        o["shipped_date"] = day(delivered_ago + 5)
        o["order_date"] = day(delivered_ago + 8)
        o["estimated_delivery_date"] = day(delivered_ago + 1)
    # Explicit keyword arguments win over the defaults above.
    o.update(kw)
    return o


def build_customers():
    """Build the two customers: Nora (C-001, the demo user) and Milo (C-002, data only).

    Returns:
        A list of customer dicts.
    """
    customers = []
    for cid, first in CUSTOMERS:
        customers.append(
            {
                "customer_id": cid,
                "first_name": first,
                "email": f"{first.lower()}.{cid.lower()}@example.test",
                "country": "PH",
                "refunds_last_90_days": REFUNDS_90.get(cid, 0),
            }
        )
    return customers


def build_orders():
    """Build the 30 orders. Each one is made to cover one test situation.

    Returns:
        A list of order dicts, ORD-1001 to ORD-1030.
    """
    # Short name for the most common status, to keep the order lines compact.
    cd = "delivered"
    return [
        order(
            "ORD-1001",
            "C-001",
            [item("SKU-HP1", "Wireless headphones", "electronics", 2950.0)],
            cd,
            10,
            notes="Customer changed their mind.",
        ),
        order("ORD-1002", "C-002", [item("SKU-JK1", "Rain jacket", "apparel", 4000.0)], cd, 45),
        order("ORD-1003", "C-003", [item("SKU-SH1", "Running shoes", "apparel", 2500.0)], cd, 29),
        order("ORD-1004", "C-004", [item("SKU-BP1", "Backpack", "apparel", 2000.0)], cd, 31),
        order(
            "ORD-1005",
            "C-005",
            [item("SKU-SW1", "Clearance sweater", "apparel", 1350.0, final_sale=True)],
            cd,
            5,
        ),
        order(
            "ORD-1006",
            "C-006",
            [item("SKU-LP1", "Clearance desk lamp", "home", 1500.0, final_sale=True)],
            cd,
            5,
        ),
        order(
            "ORD-1007", "C-007", [item("SKU-DW1", "Ceramic dinnerware set", "home", 3150.0)], cd, 3
        ),
        order(
            "ORD-1008",
            "C-008",
            [item("SKU-VS1", "Glass vase", "home", 2350.0)],
            cd,
            12,
            notes="Customer reports the vase arrived broken, 12 days after delivery.",
        ),
        order(
            "ORD-1009", "C-009", [item("SKU-LT1", "Laptop 14 inch", "electronics", 30000.0)], cd, 8
        ),
        order(
            "ORD-1010",
            "C-010",
            [item("SKU-TB1", "Tablet stand", "electronics", 2150.0)],
            "in_transit",
            order_date=day(17),
            shipped_date=day(15),
            estimated_delivery_date=day(9),
        ),
        order(
            "ORD-1011",
            "C-011",
            [item("SKU-KT1", "Electric kettle", "home", 1950.0)],
            "in_transit",
            order_date=day(8),
            shipped_date=day(6),
            estimated_delivery_date=day(1),
        ),
        order(
            "ORD-1012",
            "C-012",
            [item("SKU-CS1", "Phone case", "electronics", 1150.0)],
            "in_transit",
            shipping_paid=250.0,
            order_date=day(11),
            shipped_date=day(9),
            estimated_delivery_date=day(4),
        ),
        order(
            "ORD-1013",
            "C-013",
            [item("SKU-BK1", "Yoga mat", "home", 1400.0)],
            "processing",
            order_date=day(1),
            estimated_delivery_date=day(-6),
        ),
        order(
            "ORD-1014",
            "C-014",
            [item("SKU-BL1", "Bluetooth speaker mini", "electronics", 1850.0)],
            "shipped",
            order_date=day(3),
            shipped_date=day(1),
            estimated_delivery_date=day(-5),
        ),
        order(
            "ORD-1015",
            "C-015",
            [item("SKU-TW1", "Bath towel set", "home", 1600.0)],
            "processing",
            order_date=day(1),
            estimated_delivery_date=day(-6),
            notes="Customer wants to change the delivery address.",
        ),
        order(
            "ORD-1016",
            "C-016",
            [item("SKU-SC1", "Scarf (blue ordered)", "apparel", 1850.0)],
            cd,
            20,
            notes="Customer received a red scarf instead of blue.",
        ),
        order(
            "ORD-1017",
            "C-017",
            [item("SKU-SP1", "Portable speaker", "electronics", 4350.0)],
            cd,
            200,
        ),
        order(
            "ORD-1018", "C-018", [item("SKU-AD1", "USB-C adapter", "electronics", 2000.0)], cd, 400
        ),
        order(
            "ORD-1019",
            "C-001",
            [item("SKU-GC1", "Brightcart gift card", "gift_card", 1650.0)],
            cd,
            15,
        ),
        order(
            "ORD-1020",
            "C-002",
            [item("SKU-CT1", "Wool coat", "apparel", 6650.0, current_price=5350.0)],
            cd,
            5,
            order_date=day(12),
            notes="Our price dropped to ₱5,350 after purchase.",
        ),
        order("ORD-1021", "C-003", [item("SKU-CH1", "Ergonomic chair", "home", 10650.0)], cd, 5),
        order("ORD-1022", "C-009", [item("SKU-HB1", "Hair brush set", "home", 1350.0)], cd, 10),
        order(
            "ORD-1023",
            "C-004",
            [item("SKU-PB1", "Power bank", "electronics", 1150.0)],
            cd,
            6,
            notes="Customer reports the power bank overheats while charging.",
        ),
        order(
            "ORD-1024",
            "C-005",
            [item("SKU-JK2", "Fleece jacket", "apparel", 3000.0)],
            cd,
            10,
            refunded_amount=1000.0,
            refund_date=day(8),
            notes="₱1,000 already refunded as a goodwill gesture.",
        ),
        # Delivered to Bulacan, outside Metro Manila, so the higher return fee applies (POL-01).
        order(
            "ORD-1025",
            "C-006",
            [item("SKU-BT1", "Leather boots", "apparel", 2850.0)],
            cd,
            12,
            shipping_region="Bulacan",
        ),
        order(
            "ORD-1026",
            "C-007",
            [item("SKU-PL1", "Pillow set", "home", 1650.0)],
            "cancelled",
            order_date=day(5),
            refunded_amount=1650.0,
            refund_date=day(3),
            notes="Cancelled 3 days ago. Refund issued 3 days ago.",
        ),
        order(
            "ORD-1027",
            "C-008",
            [item("SKU-SU1", "Protein supplement", "consumable", 950.0, opened=True)],
            cd,
            25,
        ),
        order(
            "ORD-1028",
            "C-010",
            [item("SKU-MG1", "Personalised mug", "home", 750.0, personalised=True)],
            cd,
            4,
            notes="Name on mug is misspelled. Unclear whether the customer or we made the error.",
        ),
        order(
            "ORD-1029",
            "C-011",
            [
                item("SKU-LM1", "Table lamp", "home", 1350.0),
                item("SKU-FR1", "Photo frame", "home", 1850.0),
            ],
            cd,
            7,
        ),
        order(
            "ORD-1030",
            "C-012",
            [item("SKU-HD1", "Hoodie", "apparel", 3650.0)],
            cd,
            14,
            chargeback_open=True,
        ),
    ]


def main():
    """Build the customers and orders and write them as JSON files into data/."""
    DATA_DIR.mkdir(exist_ok=True)
    customers, orders = build_customers(), build_orders()
    (DATA_DIR / "customers.json").write_text(
        json.dumps(customers, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (DATA_DIR / "orders.json").write_text(
        json.dumps(orders, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Wrote {len(customers)} customers and {len(orders)} orders to {DATA_DIR}")


if __name__ == "__main__":
    main()
