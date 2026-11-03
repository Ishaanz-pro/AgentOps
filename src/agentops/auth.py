"""Authentication: bearer token -> Principal.

This is a deliberately simple static-token scheme for a demo. The important
property is that the *backend* resolves the Principal; the model never sees a
token and can never choose which user it is acting for.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    EMPLOYEE = "employee"          # can see only their own orders
    SUPPORT_AGENT = "support_agent"  # can see orders in their region
    ADMIN = "admin"                # can see all orders


@dataclass(frozen=True)
class Principal:
    user_id: str
    display_name: str
    role: Role
    region: str  # e.g. "IN-WEST"; used for support_agent scoping


class AuthError(Exception):
    """Raised when a token is missing or invalid."""


# Synthetic users. In a real system this would be an IdP / JWT validation.
_USERS: dict[str, Principal] = {
    "u-alice": Principal("u-alice", "Alice Rao", Role.EMPLOYEE, "IN-WEST"),
    "u-bob": Principal("u-bob", "Bob Mehta", Role.EMPLOYEE, "IN-SOUTH"),
    "u-carol": Principal("u-carol", "Carol Singh", Role.SUPPORT_AGENT, "IN-WEST"),
    "u-dan": Principal("u-dan", "Dan Iyer", Role.ADMIN, "IN-NORTH"),
}

# token -> user_id. Tokens are synthetic and intentionally obvious.
_TOKENS: dict[str, str] = {
    "tok-alice": "u-alice",
    "tok-bob": "u-bob",
    "tok-carol": "u-carol",
    "tok-dan": "u-dan",
}


def authenticate(token: str | None) -> Principal:
    """Resolve a bearer token to a Principal or raise AuthError."""
    if not token:
        raise AuthError("missing bearer token")
    user_id = _TOKENS.get(token.strip())
    if user_id is None:
        raise AuthError("invalid bearer token")
    return _USERS[user_id]


def get_user(user_id: str) -> Principal | None:
    return _USERS.get(user_id)


def list_users() -> list[Principal]:
    return list(_USERS.values())
