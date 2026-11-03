"""get_order_status: authorised order lookup.

The authenticated user is NEVER a model-supplied argument. It is resolved
from session state that the API layer set from the verified bearer token.
Authorization (agentops.authz.can_access_order) runs here, in code, on
every call.

Not-found and not-permitted return the SAME error to the model/user so the
tool cannot be used to enumerate which order IDs exist. The real reason is
recorded in the trace for audit.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from google.adk.tools import ToolContext
from pydantic import BaseModel, field_validator

from agentops.authz import can_access_order
from agentops.db import get_order
from agentops.observability.logging import get_logger
from agentops.observability.tracing import get_trace
from agentops.tools.deps import get_deps
from agentops.tools.registry import ToolError, ok, resolve_principal, run_guarded

log = get_logger("agentops.tools.order")

_ORDER_ID = re.compile(r"^ORD-\d{4,6}$")


class OrderArgs(BaseModel):
    order_id: str

    @field_validator("order_id")
    @classmethod
    def _fmt(cls, v: str) -> str:
        v = v.strip().upper()
        if not _ORDER_ID.match(v):
            raise ValueError("order_id must look like ORD-1234")
        return v


def _days_since(iso: str | None, today: date) -> int | None:
    if not iso:
        return None
    return (today - date.fromisoformat(iso)).days


async def get_order_status(order_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Get the current status and delivery details of an order the user is allowed to see.

    The requesting user is identified by the backend; you cannot and do not need to
    supply a user. If the order does not exist OR the user may not view it, the same
    "not accessible" error is returned. Never guess or infer order details.

    Args:
        order_id: The order identifier, formatted like "ORD-1001".

    Returns:
        ok=True with status, item, carrier, delivery date and days since delivery
        (computed by the backend), or ok=False with an error code.
    """
    deps = get_deps()

    async def body(args: OrderArgs) -> dict[str, Any]:
        principal = resolve_principal(tool_context)
        trace = get_trace()
        order = get_order(deps.db_path, args.order_id)

        if order is None:
            if trace:
                trace.add_step("authz", "order_access", order_id=args.order_id, allowed=False, reason="order not found")
            raise ToolError("order_not_accessible", f"No order {args.order_id} was found that you are permitted to view.")

        decision = can_access_order(principal, order.owner_user_id, order.region)
        if trace:
            trace.add_step(
                "authz", "order_access", order_id=args.order_id,
                allowed=decision.allowed, reason=decision.reason, role=str(principal.role),
            )
        if not decision.allowed:
            log.warning(
                "order_access_denied",
                extra={"order_id": args.order_id, "user_id": principal.user_id, "reason": decision.reason},
            )
            raise ToolError("order_not_accessible", f"No order {args.order_id} was found that you are permitted to view.")

        return ok(
            {
                "order_id": order.order_id,
                "item": order.item,
                "status": order.status,
                "ordered_at": order.ordered_at,
                "delivered_at": order.delivered_at,
                "carrier": order.carrier,
                "tracking_id": order.tracking_id,
                "as_of_date": deps.today.isoformat(),
                "days_since_delivery": _days_since(order.delivered_at, deps.today),
            }
        )

    return await run_guarded("get_order_status", OrderArgs, {"order_id": order_id}, body, deps.tool_timeout)
