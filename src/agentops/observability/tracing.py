"""Per-request trace records.

A Trace is created by the API layer for every request and made available to
every tool and callback through a ContextVar, so steps can be appended from
anywhere without threading objects through ADK. Traces are retained in a
bounded in-memory store so ``GET /trace/{request_id}`` can show exactly what
the agent did: retrieval results, tool calls and outcomes, latencies, errors.
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator


@dataclass
class Step:
    kind: str                      # retrieval | tool | authz | agent | error
    name: str
    started_at: float
    duration_ms: float | None = None
    ok: bool = True
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trace:
    request_id: str
    trace_id: str
    user_id: str
    role: str
    started_at: str
    status: str = "in_progress"           # in_progress | ok | error
    steps: list[Step] = field(default_factory=list)
    retrieval_sources: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    pending_confirmation: dict[str, Any] | None = None
    latency_ms: float | None = None
    error: str | None = None
    _t0: float = field(default_factory=time.perf_counter, repr=False)

    def add_step(self, kind: str, name: str, **detail: Any) -> Step:
        step = Step(kind=kind, name=name, started_at=time.perf_counter(), duration_ms=0.0, detail=detail)
        self.steps.append(step)
        return step

    @contextmanager
    def span(self, kind: str, name: str, **detail: Any) -> Iterator[Step]:
        step = Step(kind=kind, name=name, started_at=time.perf_counter(), detail=detail)
        self.steps.append(step)
        try:
            yield step
        except Exception as exc:
            step.ok = False
            step.error = f"{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            step.duration_ms = round((time.perf_counter() - step.started_at) * 1000, 2)

    def record_tool(self, name: str, ok: bool, code: str | None, duration_ms: float) -> None:
        self.tool_calls.append({"tool": name, "ok": ok, "code": code, "duration_ms": duration_ms})

    def finish(self, error: str | None = None) -> None:
        self.latency_ms = round((time.perf_counter() - self._t0) * 1000, 2)
        self.error = error
        self.status = "error" if error else "ok"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("_t0", None)
        for s in d["steps"]:
            s.pop("started_at", None)
        return d


_current: ContextVar[Trace | None] = ContextVar("agentops_trace", default=None)


def start_trace(user_id: str, role: str, request_id: str | None = None) -> Trace:
    trace = Trace(
        request_id=request_id or f"req-{uuid.uuid4().hex[:12]}",
        trace_id=f"trc-{uuid.uuid4().hex}",
        user_id=user_id,
        role=role,
        started_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
    )
    _current.set(trace)
    return trace


def get_trace() -> Trace | None:
    return _current.get()


def clear_trace() -> None:
    _current.set(None)


class TraceStore:
    """Bounded in-memory trace retention (demo scope; swap for Cloud Trace / BigQuery in prod)."""

    def __init__(self, capacity: int = 500):
        self._capacity = capacity
        self._items: OrderedDict[str, Trace] = OrderedDict()

    def put(self, trace: Trace) -> None:
        self._items[trace.request_id] = trace
        while len(self._items) > self._capacity:
            self._items.popitem(last=False)

    def get(self, request_id: str) -> Trace | None:
        return self._items.get(request_id)

    def __len__(self) -> int:
        return len(self._items)
