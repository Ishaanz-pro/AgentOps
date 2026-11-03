"""Tool allowlist, principal resolution, and the guarded execution wrapper.

Every tool body runs through ``run_guarded`` which:
- validates arguments with a pydantic model (malformed args -> structured error)
- enforces a timeout (hung backend -> structured error, never a hang)
- converts DatabaseUnavailable / unexpected exceptions into structured errors
- records outcome + latency in the active Trace

Tools return a uniform dict so the model only ever sees
``{"ok": true, "data": {...}}`` or ``{"ok": false, "error": {"code", "message"}}``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from agentops.auth import Principal, get_user
from agentops.db import DatabaseUnavailable
from agentops.observability.logging import get_logger
from agentops.observability.tracing import get_trace

log = get_logger("agentops.tools")

# Explicit allowlist. The agent is built only from these; before_tool_callback
# re-checks at call time as defence in depth.
ALLOWED_TOOLS: frozenset[str] = frozenset(
    {"search_policy_documents", "get_order_status", "create_support_ticket"}
)

PRINCIPAL_STATE_KEY = "principal_user_id"


def ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "data": data}


def fail(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


class ToolError(Exception):
    """Raise inside a tool body to return a structured error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def resolve_principal(tool_context: Any) -> Principal:
    """Read the backend-set principal from session state.

    The model cannot set this: session state is initialised by the API layer
    from the verified bearer token, and no tool writes to this key.
    """
    state = getattr(tool_context, "state", None)
    user_id = state.get(PRINCIPAL_STATE_KEY) if state is not None else None
    principal = get_user(user_id) if user_id else None
    if principal is None:
        raise ToolError("unauthenticated", "No authenticated user is bound to this session.")
    return principal


async def run_guarded(
    name: str,
    args_model: type[BaseModel] | None,
    raw_args: dict[str, Any],
    body: Callable[[Any], Awaitable[dict[str, Any]]],
    timeout: float,
) -> dict[str, Any]:
    """Validate args, run ``body(validated_args)`` with a timeout, map errors."""
    trace = get_trace()
    t0 = time.perf_counter()
    code: str | None = None
    try:
        parsed = args_model.model_validate(raw_args) if args_model else None
        result = await asyncio.wait_for(body(parsed), timeout=timeout)
        if not result.get("ok", False):
            code = result.get("error", {}).get("code")
    except ValidationError as exc:
        code = "invalid_arguments"
        msgs = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        result = fail(code, f"Invalid arguments: {msgs}")
    except ToolError as exc:
        code = exc.code
        result = fail(exc.code, exc.message)
    except asyncio.TimeoutError:
        code = "timeout"
        result = fail(code, f"The {name} backend did not respond within {timeout}s.")
    except DatabaseUnavailable as exc:
        code = "backend_unavailable"
        log.error("tool_backend_unavailable", extra={"tool": name, "reason": str(exc)[:200]})
        result = fail(code, "The order database is currently unavailable. Please try again later.")
    except Exception as exc:  # noqa: BLE001 - last line of defence; never leak internals to the model
        code = "internal_error"
        log.exception("tool_internal_error", extra={"tool": name, "exc_type": type(exc).__name__})
        result = fail(code, "The tool failed unexpectedly.")

    duration_ms = round((time.perf_counter() - t0) * 1000, 2)
    if trace:
        trace.record_tool(name, result.get("ok", False), code, duration_ms)
    log.info(
        "tool_invoked",
        extra={"tool": name, "ok": result.get("ok", False), "code": code, "duration_ms": duration_ms},
    )
    return result
