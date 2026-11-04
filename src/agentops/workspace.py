"""Google Workspace integration: a Calendar client behind the confirmation gate.

Two providers with one interface:
- DryRunCalendar: no network. The event is recorded in the local audit table
  only. Default, so the whole pipeline (tool -> confirmation -> execution) is
  testable offline and the demo never needs Workspace credentials.
- GoogleCalendar: inserts the event into a shared calendar through the Calendar
  REST API using Application Default Credentials (the Cloud Run service
  account, or `gcloud auth application-default login` locally). The service
  account must be granted "Make changes to events" on that calendar.

Trust properties that hold for both
- Only `confirm_and_schedule` calls `insert_event`, after a human confirmed.
- The requester is stamped into the event description by the backend from the
  verified Principal; the model never supplies attendee or owner identity.
- The agent process never holds a user's OAuth token: it writes to one
  designated support calendar as itself, so a hijacked model cannot read or
  modify a user's personal calendar.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import quote

from agentops.auth import Principal
from agentops.observability.logging import get_logger

log = get_logger("agentops.workspace")
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
_API = "https://www.googleapis.com/calendar/v3"


class CalendarUnavailable(Exception):
    """The calendar backend could not be reached or refused the write."""


class CalendarClient(Protocol):
    provider: str

    def insert_event(self, *, title: str, start_at: str, end_at: str, notes: str, requester: Principal) -> dict[str, Any]: ...


def _description(notes: str, requester: Principal) -> str:
    return f"{notes}\n\nRequested by {requester.display_name} ({requester.user_id}) via Employee Assistant."


class DryRunCalendar:
    provider = "dryrun"

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def insert_event(self, *, title: str, start_at: str, end_at: str, notes: str, requester: Principal) -> dict[str, Any]:
        ev = {"event_id": f"evt-dryrun-{uuid.uuid4().hex[:10]}", "provider": self.provider, "link": None,
              "title": title, "start_at": start_at, "end_at": end_at, "description": _description(notes, requester)}
        self.events.append(ev)
        return ev


class GoogleCalendar:
    provider = "google-calendar"

    def __init__(self, calendar_id: str, timeout: float = 10.0):
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        creds, _ = google.auth.default(scopes=[CALENDAR_SCOPE])
        self._session = AuthorizedSession(creds)
        self._calendar_id = calendar_id
        self._timeout = timeout

    def insert_event(self, *, title: str, start_at: str, end_at: str, notes: str, requester: Principal) -> dict[str, Any]:
        body = {
            "summary": title,
            "description": _description(notes, requester),
            "start": {"dateTime": start_at},
            "end": {"dateTime": end_at},
            "extendedProperties": {"private": {"agentops_requester": requester.user_id}},
        }
        try:
            r = self._session.post(f"{_API}/calendars/{quote(self._calendar_id, safe='')}/events",
                                   json=body, timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 - network/auth failures become one honest error
            log.error("calendar_request_failed", extra={"exc_type": type(exc).__name__})
            raise CalendarUnavailable("calendar request failed") from exc
        if r.status_code >= 400:
            log.error("calendar_insert_rejected", extra={"status": r.status_code})
            raise CalendarUnavailable(f"calendar API returned HTTP {r.status_code}")
        data = r.json()
        return {"event_id": data.get("id", ""), "provider": self.provider, "link": data.get("htmlLink")}


def make_calendar(mode: str, calendar_id: str | None) -> CalendarClient:
    if mode == "google":
        if not calendar_id:
            raise ValueError("AGENTOPS_CALENDAR=google requires AGENTOPS_CALENDAR_ID")
        return GoogleCalendar(calendar_id)
    return DryRunCalendar()


def parse_rfc3339(value: str) -> datetime:
    """Strict: must carry a UTC offset so the calendar never guesses a time zone."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset, e.g. 2026-11-05T10:00:00+05:30")
    return dt
