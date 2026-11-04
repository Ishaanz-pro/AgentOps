"""Shared fixtures. Everything runs offline: temp SQLite + local embeddings."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "evals"))  # fixtures.py (synthetic image)

from agentops.auth import authenticate  # noqa: E402
from seed import seed  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_logging():
    """configure_logging() replaces root handlers; undo that after every test."""
    import logging

    root = logging.getLogger()
    saved, level = list(root.handlers), root.level
    yield
    root.handlers[:] = saved
    root.setLevel(level)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    seed(path)
    return path


@pytest.fixture
def alice():
    return authenticate("tok-alice")


@pytest.fixture
def bob():
    return authenticate("tok-bob")


@pytest.fixture
def carol():
    return authenticate("tok-carol")


@pytest.fixture
def dan():
    return authenticate("tok-dan")


@pytest.fixture
def policy_dir() -> Path:
    return ROOT / "data" / "policies"
