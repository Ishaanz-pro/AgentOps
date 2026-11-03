"""Synthetic order records (all fake). Shared by data/seed.py and API startup."""

from __future__ import annotations

from pathlib import Path

from agentops.db import Order, init_db, insert_orders

# The synthetic world's "today" is 2026-11-03 (see tools/deps.py).
# Refund policy: 30 days from delivery.
ORDERS: list[Order] = [
    # Alice (employee, IN-WEST)
    Order("ORD-1001", "u-alice", "IN-WEST", "Noise-cancelling headphones", 7999, "delivered",
          "2026-10-20", "2026-10-24", "BlueDart", "BD-77120934"),
    Order("ORD-1002", "u-alice", "IN-WEST", "Mechanical keyboard", 4599, "shipped",
          "2026-10-30", None, "Delhivery", "DL-55018273"),
    Order("ORD-1003", "u-alice", "IN-WEST", "Office chair", 15999, "delivered",
          "2026-08-01", "2026-08-06", "BlueDart", "BD-77009811"),  # outside 30-day window
    # Bob (employee, IN-SOUTH)
    Order("ORD-2001", "u-bob", "IN-SOUTH", "USB-C docking station", 8999, "processing",
          "2026-11-02", None, None, None),
    Order("ORD-2002", "u-bob", "IN-SOUTH", "Monitor arm", 3299, "delivered",
          "2026-10-25", "2026-10-29", "Ekart", "EK-30921177"),
    Order("ORD-2003", "u-bob", "IN-SOUTH", "Webcam", 5499, "cancelled",
          "2026-10-15", None, None, None),
    # Carol (support agent, IN-WEST) also has a personal order
    Order("ORD-3001", "u-carol", "IN-WEST", "Laptop stand", 2499, "delivered",
          "2026-10-28", "2026-10-31", "Delhivery", "DL-55023390"),
    # Dan (admin)
    Order("ORD-4001", "u-dan", "IN-NORTH", "Conference speakerphone", 12999, "shipped",
          "2026-10-31", None, "BlueDart", "BD-77130001"),
]


def seed(db_path: Path | str) -> None:
    init_db(db_path)
    insert_orders(db_path, ORDERS)
