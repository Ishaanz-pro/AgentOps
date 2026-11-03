"""CLI wrapper: seed the SQLite database with synthetic orders.

Run:  python data/seed.py [db_path]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentops.seed_data import ORDERS, seed  # noqa: E402,F401

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "data/agentops.db"
    seed(target)
    print(f"seeded {len(ORDERS)} orders into {target}")
