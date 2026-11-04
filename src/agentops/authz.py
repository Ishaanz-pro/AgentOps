"""Authorization rules as pure functions.

These are enforced inside tool implementations (backend code), not via
model instructions. They take a Principal resolved by the backend and the
record being accessed, and return a Decision. They are unit-tested without
any LLM involved.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentops.auth import Principal, Role


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.allowed


class AuthorizationError(Exception):
    def __init__(self, decision: Decision):
        super().__init__(decision.reason)
        self.decision = decision


def can_access_order(principal: Principal, order_owner_id: str, order_region: str) -> Decision:
    """Who may read an order?

    - admin: any order
    - support_agent: orders in their own region
    - employee: only orders they own
    """
    if principal.role == Role.ADMIN:
        return Decision(True, "admin role")
    if principal.role == Role.SUPPORT_AGENT:
        if principal.region == order_region:
            return Decision(True, "support agent in order region")
        return Decision(False, "support agent outside order region")
    if principal.role == Role.EMPLOYEE:
        if principal.user_id == order_owner_id:
            return Decision(True, "order owner")
        return Decision(False, "employee is not the order owner")
    return Decision(False, "unknown role")


def can_create_ticket(principal: Principal) -> Decision:
    """All authenticated users may open a ticket on their own behalf."""
    return Decision(True, f"role {principal.role} may create tickets")


def can_schedule_followup(principal: Principal) -> Decision:
    """Calendar follow-ups go on the shared support calendar and are always attributed to the requester."""
    return Decision(True, f"role {principal.role} may schedule a follow-up")


def can_search_policies(principal: Principal) -> Decision:
    """Policy documents are internal but visible to every authenticated employee."""
    return Decision(True, "authenticated user")
