"""Runtime dependencies shared by tools.

Set once at application startup by the API layer. The model can never
influence these: tools read them from this module, not from tool arguments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from agentops.rag.index import PolicyIndex
from agentops.tools.support_ticket import ConfirmationStore

# The synthetic dataset is anchored to this date so "days since delivery"
# is stable for evals regardless of when the demo runs.
SYNTHETIC_TODAY = date(2026, 11, 3)


@dataclass
class ToolDeps:
    db_path: Path
    index: PolicyIndex
    tool_timeout: float = 5.0
    today: date = SYNTHETIC_TODAY
    confirmations: ConfirmationStore = field(default_factory=ConfirmationStore)


_deps: ToolDeps | None = None


def set_deps(deps: ToolDeps) -> None:
    global _deps
    _deps = deps


def get_deps() -> ToolDeps:
    if _deps is None:
        raise RuntimeError("ToolDeps not initialised; call set_deps() at startup")
    return _deps
