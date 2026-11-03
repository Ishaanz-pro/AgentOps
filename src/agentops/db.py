"""SQLite access for synthetic orders and support tickets.

Design notes
- Every public function takes an explicit db path so tests can use a temp DB.
- Connections use a short timeout; failures surface as DatabaseUnavailable so
  the tool layer can report them honestly instead of fabricating data.
- Orders carry owner_user_id and region, which authz.py uses.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


class DatabaseUnavailable(Exception):
    """The database could not be opened or queried."""


@dataclass(frozen=True)
class Order:
    order_id: str
    owner_user_id: str
    region: str
    item: str
    amount_inr: int
    status: str            # processing | shipped | delivered | cancelled
    ordered_at: str        # ISO date
    delivered_at: str | None
    carrier: str | None
    tracking_id: str | None


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    created_by: str
    issue: str
    created_at: str
    status: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    region TEXT NOT NULL,
    item TEXT NOT NULL,
    amount_inr INTEGER NOT NULL,
    status TEXT NOT NULL,
    ordered_at TEXT NOT NULL,
    delivered_at TEXT,
    carrier TEXT,
    tracking_id TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    created_by TEXT NOT NULL,
    issue TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
"""


@contextmanager
def connect(db_path: Path | str, timeout: float = 2.0) -> Iterator[sqlite3.Connection]:
    path = Path(db_path)
    if not path.exists() and str(path) != ":memory:":
        raise DatabaseUnavailable(f"database file not found: {path}")
    try:
        conn = sqlite3.connect(str(path), timeout=timeout)
    except sqlite3.Error as exc:  # pragma: no cover - hard to trigger reliably
        raise DatabaseUnavailable(str(exc)) from exc
    try:
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise DatabaseUnavailable(str(exc)) from exc
    finally:
        conn.close()


def init_db(db_path: Path | str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def insert_orders(db_path: Path | str, orders: list[Order]) -> None:
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    o.order_id, o.owner_user_id, o.region, o.item, o.amount_inr,
                    o.status, o.ordered_at, o.delivered_at, o.carrier, o.tracking_id,
                )
                for o in orders
            ],
        )


def get_order(db_path: Path | str, order_id: str) -> Order | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    if row is None:
        return None
    return Order(**dict(row))


def create_ticket(db_path: Path | str, created_by: str, issue: str) -> Ticket:
    ticket = Ticket(
        ticket_id=f"TCK-{uuid.uuid4().hex[:8].upper()}",
        created_by=created_by,
        issue=issue,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        status="open",
    )
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO tickets VALUES (?,?,?,?,?)",
            (ticket.ticket_id, ticket.created_by, ticket.issue, ticket.created_at, ticket.status),
        )
    return ticket


def list_tickets(db_path: Path | str, created_by: str | None = None) -> list[Ticket]:
    with connect(db_path) as conn:
        if created_by:
            rows = conn.execute("SELECT * FROM tickets WHERE created_by = ?", (created_by,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tickets").fetchall()
    return [Ticket(**dict(r)) for r in rows]
