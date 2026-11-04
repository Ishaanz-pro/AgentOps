"""The Agent Engine entry point and the runtime-user trust fallback it relies on."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentops.tools import deps as deps_mod
from agentops.tools.registry import TRUST_RUNNER_USER_ENV, ToolError, resolve_principal

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "deploy" / "agent_engine" / "employee_assistant" / "agent.py"


def test_runner_user_id_is_trusted_only_when_opted_in(monkeypatch):
    ctx = SimpleNamespace(state={}, user_id="u-alice")
    monkeypatch.delenv(TRUST_RUNNER_USER_ENV, raising=False)
    with pytest.raises(ToolError, match="No authenticated user"):
        resolve_principal(ctx)

    monkeypatch.setenv(TRUST_RUNNER_USER_ENV, "1")
    assert resolve_principal(ctx).user_id == "u-alice"
    with pytest.raises(ToolError):  # unknown runtime user is still unauthenticated
        resolve_principal(SimpleNamespace(state={}, user_id="someone-else"))
    # session state, when present, always wins over the runtime user id
    assert resolve_principal(SimpleNamespace(state={"principal_user_id": "u-bob"}, user_id="u-alice")).user_id == "u-bob"


def test_agent_engine_entry_builds_root_agent(tmp_path, monkeypatch):
    """Imports the deploy module exactly as the Agent Engine runtime would, offline."""
    monkeypatch.setenv("AGENTOPS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTOPS_EMBEDDINGS", "local")
    monkeypatch.setenv("AGENTOPS_MODEL", "gemini-3.5-flash")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env from the repo

    spec = importlib.util.spec_from_file_location("ae_entry", ENTRY)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        assert mod.root_agent.name == "employee_assistant"
        assert {t.__name__ if hasattr(t, "__name__") else t.name for t in mod.root_agent.tools} == {
            "search_policy_documents", "get_order_status", "create_support_ticket", "schedule_followup"}
        assert (tmp_path / "agentops.db").exists() and (tmp_path / "index").is_dir()
        assert mod._index.embedder.name.startswith("local-hash")
    finally:
        deps_mod._deps = None
        sys.modules.pop("ae_entry", None)
