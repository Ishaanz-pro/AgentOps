"""Two-phase support-ticket creation.

Phase 1 (agent tool): the model may *propose* a ticket. The tool validates
the issue and stores a pending confirmation bound to the authenticated user.
Nothing is written to the tickets table.

Phase 2 (API, outside the model loop): ``POST /confirm`` with the
confirmation id, authenticated as the *same* user, creates the ticket.
A pending confirmation is single-use and expires after a short TTL.

Design choice: ADK ships a native ``FunctionTool(require_confirmation=True)``
flow. We implement the gate explicitly in backend code so the confirmation
state is owned and validated server-side (bound to principal, single-use,
expiring) and is testable without a model in the loop.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from agentops.auth import Principal
from agentops.authz import can_create_ticket
from agentops.db import Ticket, create_ticket


class ConfirmationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PendingConfirmation:
    confirmation_id: str
    user_id: str
    issue: str
    created_at: float
    expires_at: float


class ConfirmationStore:
    def __init__(self, ttl_seconds: float = 600.0):
        self._ttl = ttl_seconds
        self._items: dict[str, PendingConfirmation] = {}

    def create(self, principal: Principal, issue: str) -> PendingConfirmation:
        now = time.time()
        pc = PendingConfirmation(
            confirmation_id=f"cfm-{uuid.uuid4().hex[:10]}",
            user_id=principal.user_id,
            issue=issue,
            created_at=now,
            expires_at=now + self._ttl,
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
    store: ConfirmationStore, db_path: Path, principal: Principal, confirmation_id: str
) -> Ticket:
    """Phase 2: consume the confirmation for this principal and create the ticket."""
    decision = can_create_ticket(principal)
    if not decision.allowed:
        raise ConfirmationError("forbidden", decision.reason)
    pc = store.consume(confirmation_id, principal)
    return create_ticket(db_path, created_by=principal.user_id, issue=pc.issue)
