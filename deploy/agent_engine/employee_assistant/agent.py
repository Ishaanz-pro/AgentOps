"""Vertex AI Agent Engine entry point (also what Gemini Enterprise registers).

`adk deploy agent_engine` packages this folder plus the `extra_packages` listed in
.agent_engine_config.json (the agentops source tree and the synthetic data). The
ADK runtime imports this module and serves `root_agent`.

Differences from the FastAPI deployment, all deliberate:
- No HTTP layer of our own, so the principal comes from the runtime's authenticated
  user_id (AGENTOPS_TRUST_RUNNER_USER_ID=1 in the config). IAM on the Agent Engine
  resource is the authentication boundary; authorization inside the tools is unchanged.
- The two-phase confirmation gate still holds: the agent can only PROPOSE tickets and
  calendar events. Executing them needs the AgentOps API's /confirm, called by a human.
  Gemini Enterprise surfaces the proposal text; nothing is written from this runtime.
- State lives in /tmp (SQLite, FAISS index); it is synthetic and rebuilt on cold start.
"""

from __future__ import annotations

import os
from pathlib import Path

from agentops.agent.agent import build_agent, make_gemini_model
from agentops.config import Settings, apply_model_env
from agentops.observability.logging import configure_logging, get_logger
from agentops.rag.embeddings import LocalHashEmbedder, make_embedder
from agentops.rag.index import PolicyIndex
from agentops.seed_data import seed
from agentops.tools.deps import ToolDeps, set_deps

log = get_logger("agentops.agent_engine")

_HERE = Path(__file__).resolve().parent
# Staged by `adk deploy` at /app/data; when imported from the repo checkout, use the repo's data dir.
_DATA = Path("/app/data") if Path("/app/data").is_dir() else _HERE.parents[2] / "data"
_STATE = Path(os.environ.get("AGENTOPS_STATE_DIR", "/tmp/agentops" if os.name != "nt" else str(_HERE / ".state")))

settings = Settings(
    AGENTOPS_POLICY_DIR=str(_DATA / "policies"),
    AGENTOPS_DB_PATH=str(_STATE / "agentops.db"),
    AGENTOPS_INDEX_DIR=str(_STATE / "index"),
    AGENTOPS_ATTACHMENTS_DIR=str(_STATE / "attachments"),
)
configure_logging(settings.log_level)
apply_model_env(settings)


def _embedder():
    want = "gemini" if settings.embeddings in ("auto", "gemini") and settings.has_model_credentials else "local"
    vertex = (settings.gcp_project, settings.gcp_location) if settings.use_vertexai and settings.gcp_project else None
    key = settings.google_api_key.get_secret_value() if settings.has_api_key and not vertex else None  # type: ignore[union-attr]
    try:
        return make_embedder(want, key, vertex)
    except Exception as exc:  # noqa: BLE001 - degrade to the offline embedder rather than fail cold start
        log.error("embedder_init_failed", extra={"wanted": want, "exc_type": type(exc).__name__})
        return LocalHashEmbedder()


_STATE.mkdir(parents=True, exist_ok=True)
if not settings.db_path.exists():
    seed(settings.db_path)
_index = PolicyIndex.load_or_build(_embedder(), settings.policy_dir, settings.index_dir)
set_deps(ToolDeps(db_path=settings.db_path, index=_index, tool_timeout=settings.tool_timeout,
                  attachments_dir=settings.attachments_dir))
log.info("agent_engine_ready", extra={"backend": settings.gemini_backend, "embedder": _index.embedder.name,
                                      "chunks": len(_index.chunks)})

root_agent = build_agent(make_gemini_model(settings.model))
