"""Two-phase side effects: the model proposes, a human confirms, the backend executes.

Phase 1 (agent tool): the model may *propose* an action (a support ticket or a
Google Calendar follow-up). The tool validates the arguments and stores a
pending confirmation bound to the authenticated user. Nothing is written.

Phase 2 (API, outside the model loop): ``POST /confirm`` with the
confirmation id, authenticated as the *same* user, executes the action.
A pending confirmation is single-use and expires after a short TTL.

Design choice: ADK ships a native ``FunctionTool(require_confirmation=True)``
flow. We implement the gate explicitly in backend code so the confirmation
state is owned and validated server-side (bound to principal, single-use,
expiring) and is testable without a model in the loop. The same gate covers
every tool with a side effect, so adding a Workspace write did not add a new
trust path.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentops import attachments
from agentops.auth import Principal
from agentops.authz import can_create_ticket, can_schedule_followup
from agentops.db import CalendarEvent, Ticket, create_ticket, record_calendar_event

KIND_TICKET = "ticket"
KIND_CALENDAR = "calendar_event"


class ConfirmationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PendingConfirmation:
    confirmation_id: str
    user_id: str
    kind: str
    payload: dict[str, Any]
    created_at: float
    expires_at: float
    attachment: attachments.Attachment | None = field(default=None, repr=False)

    @property
    def issue(self) -> str:  # ticket shorthand, kept for callers that predate calendar events
        return str(self.payload.get("issue", ""))

    def public(self) -> dict[str, Any]:
        """What the API returns to the user so they can see exactly what they are confirming."""
        out = {"confirmation_id": self.confirmation_id, "kind": self.kind,
               "expires_in_s": int(self.expires_at - self.created_at), **self.payload}
        if self.attachment:
            out["attachment"] = self.attachment.meta()
        return out


class ConfirmationStore:
    def __init__(self, ttl_seconds: float = 600.0):
        self._ttl = ttl_seconds
        self._items: dict[str, PendingConfirmation] = {}

    def create(self, principal: Principal, issue: str | None = None, *, kind: str = KIND_TICKET,
               payload: dict[str, Any] | None = None,
               attachment: attachments.Attachment | None = None) -> PendingConfirmation:
        now = time.time()
        data = dict(payload or {})
        if issue is not None:
            data["issue"] = issue
        pc = PendingConfirmation(
            confirmation_id=f"cfm-{uuid.uuid4().hex[:10]}",
            user_id=principal.user_id,
            kind=kind,
            payload=data,
            created_at=now,
            expires_at=now + self._ttl,
            attachment=attachment,
        )
        self._items[pc.confirmation_id] = pc
        return pc

    def peek(self, confirmation_id: str) -> PendingConfirmation | None:
        return self._items.get(confirmation_id)

    def consume(self, confirmation_id: str, principal: Principal) -> PendingConfirmation:
        pc = self._items.get(confirmation_id)
        # Same error for "missing" and "belongs to someone else": don't leak existence.
        if pc is None or pc.user_id != principal.user_id:
            raise ConfirmationError("not_found", "No pending confirmation with that id.")
        if time.time() > pc.expires_at:
            del self._items[confirmation_id]
            raise ConfirmationError("expired", "This confirmation has expired. Ask the assistant again.")
        del self._items[confirmation_id]
        return pc


def confirm_and_create(
    store: ConfirmationStore, db_path: Path, principal: Principal, confirmation_id: str,
    attachments_dir: Path | None = None,
) -> Ticket:
    """Phase 2 for tickets: consume the confirmation for this principal and create the ticket."""
    decision = can_create_ticket(principal)
    if not decision.allowed:
        raise ConfirmationError("forbidden", decision.reason)
    pc = store.consume(confirmation_id, principal)
    if pc.kind != KIND_TICKET:
        raise ConfirmationError("not_found", "No pending confirmation with that id.")
    sha = None
    if pc.attachment is not None and attachments_dir is not None:
        attachments.save(pc.attachment, attachments_dir)
        sha = pc.attachment.sha256
    return create_ticket(db_path, created_by=principal.user_id, issue=pc.issue, attachment_sha256=sha)


def confirm_and_schedule(
    store: ConfirmationStore, db_path: Path, principal: Principal, confirmation_id: str, calendar: Any,
) -> CalendarEvent:
    """Phase 2 for calendar follow-ups: consume, write through the calendar client, audit locally.

    ``calendar`` is a ``workspace.CalendarClient`` (dry-run or Google). The audit
    row is written for both, so the trace of "what did the agent actually change"
    does not depend on which provider was configured.
    """
    decision = can_schedule_followup(principal)
    if not decision.allowed:
        raise ConfirmationError("forbidden", decision.reason)
    pc = store.consume(confirmation_id, principal)
    if pc.kind != KIND_CALENDAR:
        raise ConfirmationError("not_found", "No pending confirmation with that id.")
    p = pc.payload
    created = calendar.insert_event(title=p["title"], start_at=p["start_at"], end_at=p["end_at"], notes=p["notes"],
                                    requester=principal)
    ev = CalendarEvent(
        event_id=created["event_id"], created_by=principal.user_id, title=p["title"], start_at=p["start_at"],
        end_at=p["end_at"], notes=p["notes"], provider=created["provider"], external_link=created.get("link"),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
    )
    record_calendar_event(db_path, ev)
    return ev
