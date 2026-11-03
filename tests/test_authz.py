"""Authorization rules are pure functions: tested with no LLM, no DB."""

import pytest

from agentops.auth import AuthError, Role, authenticate
from agentops.authz import can_access_order, can_create_ticket


def test_authenticate_valid_tokens():
    assert authenticate("tok-alice").role == Role.EMPLOYEE
    assert authenticate("tok-carol").role == Role.SUPPORT_AGENT
    assert authenticate("tok-dan").role == Role.ADMIN


def test_authenticate_rejects_missing_or_bad_token():
    with pytest.raises(AuthError):
        authenticate(None)
    with pytest.raises(AuthError):
        authenticate("")
    with pytest.raises(AuthError):
        authenticate("tok-nobody")


def test_employee_can_access_own_order(alice):
    assert can_access_order(alice, "u-alice", "IN-WEST").allowed


def test_employee_cannot_access_other_users_order(alice):
    d = can_access_order(alice, "u-bob", "IN-SOUTH")
    assert not d.allowed
    assert "not the order owner" in d.reason


def test_employee_cannot_access_same_region_other_order(alice):
    # Same region, different owner: still denied for plain employees.
    assert not can_access_order(alice, "u-carol", "IN-WEST").allowed


def test_support_agent_scoped_to_region(carol):
    assert can_access_order(carol, "u-alice", "IN-WEST").allowed
    assert not can_access_order(carol, "u-bob", "IN-SOUTH").allowed


def test_admin_can_access_any_order(dan):
    assert can_access_order(dan, "u-alice", "IN-WEST").allowed
    assert can_access_order(dan, "u-bob", "IN-SOUTH").allowed


def test_everyone_can_create_ticket(alice, bob, carol, dan):
    for p in (alice, bob, carol, dan):
        assert can_create_ticket(p).allowed
