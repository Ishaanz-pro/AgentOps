"""schedule_followup: PROPOSES a Google Calendar event; never writes one.

The write happens only via ``POST /confirm`` (support_ticket.confirm_and_schedule),
outside the model loop, by the same authenticated user, through the configured
CalendarClient. The model supplies only title/time/notes; the requester identity
and the target calendar are fixed by the backend.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from google.adk.tools import ToolContext
from pydantic import BaseModel, Field, field_validator

from agentops.authz import can_schedule_followup
from agentops.observability.tracing import get_trace
from agentops.tools.deps import get_deps
from agentops.tools.registry import ToolError, ok, resolve_principal, run_guarded
from agentops.tools.support_ticket import KIND_CALENDAR
from agentops.workspace import parse_rfc3339


class FollowupArgs(BaseModel):
    title: str = Field(min_length=5, max_length=120)
    start_at: str = Field(min_length=16, max_length=40)
    notes: str = Field(default="", max_length=1000)
    duration_minutes: int = Field(default=30, ge=5, le=480)

    @field_validator("start_at")
    @classmethod
    def _rfc3339(cls, v: str) -> str:
        parse_rfc3339(v)
        return v


async def schedule_followup(
    title: str, start_at: str, tool_context: ToolContext, notes: str = "", duration_minutes: int = 30
) -> dict[str, Any]:
    """Propose a follow-up event on the support team's Google Calendar. NOT created by this call.

    Only call this when the user explicitly asks for a follow-up, reminder or
    call-back to be scheduled. Tell the user the event is NOT on the calendar yet
    and that they must confirm it in the app.

    Args:
        title: Short event title, e.g. "Follow up on ORD-1001 refund".
        start_at: Start time as RFC 3339 with UTC offset, e.g. "2026-11-05T10:00:00+05:30".
        notes: Optional context for the support team (no personal data of other users).
        duration_minutes: Event length, 5 to 480 minutes. Default 30.

    Returns:
        ok=True with status "pending_confirmation" and a confirmation_id, or ok=False.
    """
    deps = get_deps()
    raw = {"title": title, "start_at": start_at, "notes": notes, "duration_minutes": duration_minutes}

    async def body(args: FollowupArgs) -> dict[str, Any]:
        principal = resolve_principal(tool_context)
        decision = can_schedule_followup(principal)
        if not decision.allowed:
            raise ToolError("forbidden", decision.reason)
        start = parse_rfc3339(args.start_at)
        if start.date() < deps.today:
            raise ToolError("invalid_arguments", f"start_at must not be before today ({deps.today.isoformat()}).")
        end = start + timedelta(minutes=args.duration_minutes)
        payload = {"title": args.title.strip(), "start_at": start.isoformat(), "end_at": end.isoformat(),
                   "notes": args.notes.strip()}
        pc = deps.confirmations.create(principal, kind=KIND_CALENDAR, payload=payload)
        trace = get_trace()
        if trace:
            trace.propose(pc.public())
        return ok({
            "status": "pending_confirmation",
            "confirmation_id": pc.confirmation_id,
            "event_created": False,
            "calendar_provider": deps.calendar.provider if deps.calendar else "dryrun",
            "message": "Event NOT scheduled yet. The user must confirm it before it is written to the calendar.",
        })

    return await run_guarded("schedule_followup", FollowupArgs, raw, body, deps.tool_timeout)
