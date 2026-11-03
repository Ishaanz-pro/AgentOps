from pathlib import Path

import pytest

from agentops.db import DatabaseUnavailable, create_ticket, get_order, list_tickets


def test_seeded_order_lookup(db_path):
    o = get_order(db_path, "ORD-1001")
    assert o is not None
    assert o.owner_user_id == "u-alice"
    assert o.status == "delivered"
    assert o.delivered_at == "2026-10-24"


def test_unknown_order_returns_none(db_path):
    assert get_order(db_path, "ORD-9999") is None


def test_missing_database_raises_unavailable(tmp_path: Path):
    with pytest.raises(DatabaseUnavailable):
        get_order(tmp_path / "does-not-exist.db", "ORD-1001")


def test_create_and_list_ticket(db_path):
    t = create_ticket(db_path, "u-alice", "Headphones arrived with a cracked band")
    assert t.ticket_id.startswith("TCK-")
    assert t.status == "open"
    mine = list_tickets(db_path, created_by="u-alice")
    assert [x.ticket_id for x in mine] == [t.ticket_id]
    assert list_tickets(db_path, created_by="u-bob") == []
