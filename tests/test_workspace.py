"""Google Workspace (Calendar) follow-ups: propose -> confirm -> execute, with the same gate as tickets."""

from types import SimpleNamespace

import pytest

from agentops.auth import Principal
from agentops.db import list_calendar_events, list_tickets
from agentops.rag.embeddings import LocalHashEmbedder
from agentops.rag.index import PolicyIndex
from agentops.tools import deps as deps_mod
from agentops.tools.deps import ToolDeps, set_deps
from agentops.tools.followup_tool import schedule_followup
from agentops.tools.support_ticket import ConfirmationError, confirm_and_create, confirm_and_schedule
from agentops.tools.ticket_tool import create_support_ticket
from agentops.workspace import CalendarUnavailable, DryRunCalendar, GoogleCalendar, make_calendar, parse_rfc3339


@pytest.fixture
def deps(db_path, policy_dir):
    d = ToolDeps(db_path=db_path, index=PolicyIndex.build_from_dir(LocalHashEmbedder(), policy_dir))
    set_deps(d)
    yield d
    deps_mod._deps = None


def ctx(user_id):
    return SimpleNamespace(state={"principal_user_id": user_id} if user_id else {})


START = "2026-11-05T10:00:00+05:30"


async def test_followup_is_only_proposed(deps, db_path):
    r = await schedule_followup("Follow up on ORD-1001 refund", START, ctx("u-alice"), notes="customer waiting")
    assert r["ok"] is True
    assert r["data"]["status"] == "pending_confirmation" and r["data"]["event_created"] is False
    assert r["data"]["calendar_provider"] == "dryrun"
    assert list_calendar_events(db_path) == [] and deps.calendar.events == []


async def test_followup_created_only_after_confirmation_and_attributed_by_backend(deps, db_path, alice):
    r = await schedule_followup("Follow up on ORD-1001 refund", START, ctx("u-alice"), duration_minutes=45)
    cid = r["data"]["confirmation_id"]
    ev = confirm_and_schedule(deps.confirmations, db_path, alice, cid, deps.calendar)
    assert ev.created_by == "u-alice" and ev.provider == "dryrun" and ev.event_id.startswith("evt-dryrun-")
    assert ev.start_at == START and ev.end_at == "2026-11-05T10:45:00+05:30"
    assert len(list_calendar_events(db_path)) == 1
    # requester identity comes from the verified principal, not from the model's arguments
    assert "Alice Rao (u-alice)" in deps.calendar.events[0]["description"]
    with pytest.raises(ConfirmationError):  # single use
        confirm_and_schedule(deps.confirmations, db_path, alice, cid, deps.calendar)


async def test_followup_confirmation_bound_to_user(deps, db_path, bob):
    r = await schedule_followup("Follow up on ORD-1001 refund", START, ctx("u-alice"))
    with pytest.raises(ConfirmationError) as e:
        confirm_and_schedule(deps.confirmations, db_path, bob, r["data"]["confirmation_id"], deps.calendar)
    assert e.value.code == "not_found" and list_calendar_events(db_path) == []


async def test_confirmation_kinds_cannot_be_crossed(deps, db_path, alice):
    """A ticket confirmation id cannot be redeemed as a calendar write, and vice versa."""
    t = await create_support_ticket("My headphones arrived with a cracked band", ctx("u-alice"))
    with pytest.raises(ConfirmationError):
        confirm_and_schedule(deps.confirmations, db_path, alice, t["data"]["confirmation_id"], deps.calendar)
    f = await schedule_followup("Follow up on ORD-1001 refund", START, ctx("u-alice"))
    with pytest.raises(ConfirmationError):
        confirm_and_create(deps.confirmations, db_path, alice, f["data"]["confirmation_id"])
    assert list_tickets(db_path) == [] and list_calendar_events(db_path) == []


@pytest.mark.parametrize("start, code", [
    ("2026-11-05T10:00:00", "invalid_arguments"),       # no UTC offset
    ("tomorrow at ten", "invalid_arguments"),
    ("2026-10-01T10:00:00+05:30", "invalid_arguments"),  # before the synthetic 'today'
])
async def test_followup_validates_time(deps, start, code):
    r = await schedule_followup("Follow up on ORD-1001 refund", start, ctx("u-alice"))
    assert r["ok"] is False and r["error"]["code"] == code


async def test_followup_requires_principal(deps):
    r = await schedule_followup("Follow up on ORD-1001 refund", START, ctx(None))
    assert r["ok"] is False and r["error"]["code"] == "unauthenticated"


def test_parse_rfc3339_is_strict():
    assert parse_rfc3339("2026-11-05T10:00:00Z").utcoffset().total_seconds() == 0
    with pytest.raises(ValueError):
        parse_rfc3339("2026-11-05T10:00:00")


def test_calendar_failure_is_reported_not_swallowed(deps, db_path, alice):
    class Broken:
        provider = "google-calendar"

        def insert_event(self, **_):
            raise CalendarUnavailable("HTTP 403")

    pc = deps.confirmations.create(alice, kind="calendar_event",
                                   payload={"title": "x", "start_at": START, "end_at": START, "notes": ""})
    with pytest.raises(CalendarUnavailable):
        confirm_and_schedule(deps.confirmations, db_path, alice, pc.confirmation_id, Broken())
    assert list_calendar_events(db_path) == []  # no audit row for a write that did not happen


def test_make_calendar_modes(monkeypatch):
    assert isinstance(make_calendar("dryrun", None), DryRunCalendar)
    with pytest.raises(ValueError, match="AGENTOPS_CALENDAR_ID"):
        make_calendar("google", None)

    # GoogleCalendar builds an authorized session from ADC; stub the auth so no network/credentials are needed.
    class FakeSession:
        def __init__(self, creds):
            self.creds = creds

    import google.auth
    import google.auth.transport.requests as tr

    monkeypatch.setattr(google.auth, "default", lambda scopes=None: (SimpleNamespace(scopes=scopes), "proj"))
    monkeypatch.setattr(tr, "AuthorizedSession", FakeSession)
    gc = make_calendar("google", "support-calendar@example.com")
    assert isinstance(gc, GoogleCalendar) and gc.provider == "google-calendar"
    assert gc._session.creds.scopes == ["https://www.googleapis.com/auth/calendar.events"]


def test_google_calendar_insert_maps_request_and_errors(monkeypatch):
    calls = []

    class FakeResp:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body

        def json(self):
            return self._body

    class FakeSession:
        def __init__(self, creds):
            pass

        def post(self, url, json, timeout):
            calls.append((url, json))
            return FakeResp(200, {"id": "abc123", "htmlLink": "https://calendar.google.com/event?eid=abc"}) if "ok" in url \
                else FakeResp(403, {})

    import google.auth
    import google.auth.transport.requests as tr

    monkeypatch.setattr(google.auth, "default", lambda scopes=None: (None, "proj"))
    monkeypatch.setattr(tr, "AuthorizedSession", FakeSession)
    alice = Principal("u-alice", "Alice Rao", "employee", "IN-WEST")

    out = GoogleCalendar("ok-cal").insert_event(title="T", start_at=START, end_at=START, notes="n", requester=alice)
    assert out == {"event_id": "abc123", "provider": "google-calendar", "link": "https://calendar.google.com/event?eid=abc"}
    url, body = calls[0]
    assert url.endswith("/calendars/ok-cal/events")
    assert body["start"] == {"dateTime": START} and "u-alice" in body["description"]
    assert body["extendedProperties"]["private"]["agentops_requester"] == "u-alice"

    with pytest.raises(CalendarUnavailable, match="403"):
        GoogleCalendar("denied").insert_event(title="T", start_at=START, end_at=START, notes="n", requester=alice)
