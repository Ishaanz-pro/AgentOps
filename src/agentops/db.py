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
    attachment_sha256: str | None = None   # photo evidence, stored by content hash


@dataclass(frozen=True)
class CalendarEvent:
    """Audit record of a confirmed calendar write (written for dry-run AND real Google Calendar)."""
    event_id: str
    created_by: str
    title: str
    start_at: str
    end_at: str
    notes: str
    provider: str              # dryrun | google-calendar
    external_link: str | None
    created_at: str


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
    status TEXT NOT NULL,
    attachment_sha256 TEXT
);
CREATE TABLE IF NOT EXISTS calendar_events (
    event_id TEXT PRIMARY KEY,
    created_by TEXT NOT NULL,
    title TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    notes TEXT NOT NULL,
    provider TEXT NOT NULL,
    external_link TEXT,
    created_at TEXT NOT NULL
);
"""


def migrate(db_path: Path | str) -> None:
    """Bring an existing database up to the current schema (idempotent, additive only)."""
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tickets)")}
        if "attachment_sha256" not in cols:
            conn.execute("ALTER TABLE tickets ADD COLUMN attachment_sha256 TEXT")


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_ticket(db_path: Path | str, created_by: str, issue: str, attachment_sha256: str | None = None) -> Ticket:
    ticket = Ticket(
        ticket_id=f"TCK-{uuid.uuid4().hex[:8].upper()}",
        created_by=created_by,
        issue=issue,
        created_at=_now(),
        status="open",
        attachment_sha256=attachment_sha256,
    )
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO tickets (ticket_id, created_by, issue, created_at, status, attachment_sha256)"
            " VALUES (?,?,?,?,?,?)",
            (ticket.ticket_id, ticket.created_by, ticket.issue, ticket.created_at, ticket.status,
             ticket.attachment_sha256),
        )
    return ticket


def record_calendar_event(db_path: Path | str, ev: CalendarEvent) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO calendar_events VALUES (?,?,?,?,?,?,?,?,?)",
            (ev.event_id, ev.created_by, ev.title, ev.start_at, ev.end_at, ev.notes, ev.provider,
             ev.external_link, ev.created_at),
        )


def list_calendar_events(db_path: Path | str, created_by: str | None = None) -> list[CalendarEvent]:
    with connect(db_path) as conn:
        q, args = ("SELECT * FROM calendar_events WHERE created_by = ?", (created_by,)) if created_by else (
            "SELECT * FROM calendar_events", ())
        rows = conn.execute(q, args).fetchall()
    return [CalendarEvent(**dict(r)) for r in rows]


def list_tickets(db_path: Path | str, created_by: str | None = None) -> list[Ticket]:
    with connect(db_path) as conn:
        if created_by:
            rows = conn.execute("SELECT * FROM tickets WHERE created_by = ?", (created_by,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tickets").fetchall()
    return [Ticket(**dict(r)) for r in rows]
