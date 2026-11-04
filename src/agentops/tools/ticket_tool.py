"""create_support_ticket: PROPOSES a ticket; never creates one.

Creation happens only via ``POST /confirm`` (see support_ticket.confirm_and_create),
outside the model loop, by the same authenticated user. So even a fully
jailbroken model cannot create a ticket without an explicit human action.

If the user attached an image to this turn, the backend binds it to the
proposal. The model cannot choose, swap or invent an attachment: it only
describes what it saw, and the human sees the real file hash before confirming.
"""

from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext
from pydantic import BaseModel, Field

from agentops import attachments
from agentops.authz import can_create_ticket
from agentops.observability.tracing import get_trace
from agentops.tools.deps import get_deps
from agentops.tools.registry import ToolError, ok, resolve_principal, run_guarded


class TicketArgs(BaseModel):
    issue: str = Field(min_length=10, max_length=1000)


async def create_support_ticket(issue: str, tool_context: ToolContext) -> dict[str, Any]:
    """Propose a support ticket for the user. The ticket is NOT created by this call.

    This only prepares a ticket and asks the user to confirm. Tell the user the ticket
    has NOT been created yet and that they must confirm it in the app. Only call this
    when the user has asked for a ticket to be raised. If the user attached a photo,
    describe what it shows in the issue text; the photo itself is attached automatically.

    Args:
        issue: A clear description of the problem, at least 10 characters.

    Returns:
        ok=True with status "pending_confirmation" and a confirmation_id, or ok=False.
    """
    deps = get_deps()

    async def body(args: TicketArgs) -> dict[str, Any]:
        principal = resolve_principal(tool_context)
        decision = can_create_ticket(principal)
        if not decision.allowed:
            raise ToolError("forbidden", decision.reason)
        att = attachments.get_current()
        pc = deps.confirmations.create(principal, args.issue.strip(), attachment=att)
        trace = get_trace()
        if trace:
            trace.propose(pc.public())
        return ok(
            {
                "status": "pending_confirmation",
                "confirmation_id": pc.confirmation_id,
                "ticket_created": False,
                "attachment": att.meta() if att else None,
                "message": "Ticket NOT created yet. The user must confirm it before it is filed.",
            }
        )

    return await run_guarded("create_support_ticket", TicketArgs, {"issue": issue}, body, deps.tool_timeout)
