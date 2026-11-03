"""FastAPI app factory.

Endpoints
- POST /chat            authenticated chat turn through the ADK agent
- POST /confirm         explicit human confirmation that creates a proposed ticket
- GET  /trace/{id}      execution trace (owner or admin only)
- GET  /health          liveness + which model/embedding mode is ACTUALLY running
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from agentops.agent.mock_llm import MockLlm
from agentops.agent.runner import AgentService
from agentops.api.schemas import (
    ChatRequest, ChatResponse, ConfirmRequest, ConfirmResponse, HealthResponse,
)
from agentops.auth import AuthError, Principal, Role, authenticate
from agentops.config import Settings, get_settings
from agentops.db import DatabaseUnavailable, get_order
from agentops.observability.logging import configure_logging, get_logger
from agentops.observability.tracing import TraceStore
from agentops.rag.embeddings import make_embedder
from agentops.rag.index import PolicyIndex
from agentops.seed_data import seed
from agentops.tools.deps import ToolDeps, set_deps
from agentops.tools.support_ticket import ConfirmationError, confirm_and_create

log = get_logger("agentops.api")


def _resolve_modes(settings: Settings) -> tuple[str, str]:
    """Return (model_mode, embeddings_mode), honouring 'auto' and refusing impossible combos."""
    has_key = settings.has_api_key
    mode = settings.mode if settings.mode != "auto" else ("gemini" if has_key else "mock")
    emb = settings.embeddings if settings.embeddings != "auto" else ("gemini" if has_key else "local")
    if mode == "gemini" and not has_key:
        raise RuntimeError("AGENTOPS_MODE=gemini requires GOOGLE_API_KEY")
    if emb == "gemini" and not has_key:
        raise RuntimeError("AGENTOPS_EMBEDDINGS=gemini requires GOOGLE_API_KEY")
    return mode, emb


def current_principal(authorization: Annotated[str | None, Header()] = None) -> Principal:
    """Resolve the bearer token to a Principal (module level so FastAPI can resolve the annotation)."""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    try:
        return authenticate(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"})


Authed = Annotated[Principal, Depends(current_principal)]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_level)
        mode, emb = _resolve_modes(settings)

        if mode == "gemini":
            # google-genai / ADK read the key from the environment; never log or echo it.
            import os
            os.environ["GOOGLE_API_KEY"] = settings.google_api_key.get_secret_value()  # type: ignore[union-attr]

        if not settings.db_path.exists():
            seed(settings.db_path)
            log.info("database_seeded", extra={"path": str(settings.db_path)})

        key = settings.google_api_key.get_secret_value() if settings.has_api_key else None  # type: ignore[union-attr]
        embedder = make_embedder(emb, key)
        index = PolicyIndex.load_or_build(embedder, settings.policy_dir, settings.index_dir)

        deps = ToolDeps(db_path=settings.db_path, index=index, tool_timeout=settings.tool_timeout)
        set_deps(deps)

        traces = TraceStore()
        model = settings.model if mode == "gemini" else MockLlm()
        app.state.service = AgentService(model, traces)
        app.state.deps = deps
        app.state.traces = traces
        app.state.mode = mode
        app.state.embeddings = emb
        log.info("startup_complete", extra={"model_mode": mode, "embeddings": emb, "chunks": len(index.chunks)})
        yield

    app = FastAPI(title="Enterprise AgentOps", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):  # never leak internals
        log.exception("unhandled_error")
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    # ---------------- routes ----------------
    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        s = request.app.state
        try:
            get_order(s.deps.db_path, "ORD-0000")
            db = "ok"
        except DatabaseUnavailable:
            db = "unavailable"
        return HealthResponse(
            status="ok" if db == "ok" else "degraded",
            model_mode=s.mode,
            model=settings.model if s.mode == "gemini" else "mock-llm (offline, deterministic)",
            embeddings=s.embeddings,
            policy_chunks=len(s.deps.index.chunks),
            database=db,
        )

    @app.post("/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, principal: Authed, request: Request, response: Response) -> ChatResponse:
        result = await request.app.state.service.chat(principal, body.message, body.session_id)
        response.headers["X-Request-ID"] = result.request_id
        return ChatResponse(
            request_id=result.request_id, trace_id=result.trace_id, session_id=result.session_id,
            status=result.status, answer=result.answer, sources=result.sources,
            pending_confirmation=result.pending_confirmation, latency_ms=result.latency_ms,
            unverified_citations=result.unverified_citations,
        )

    @app.post("/confirm", response_model=ConfirmResponse)
    async def confirm(body: ConfirmRequest, principal: Authed, request: Request) -> ConfirmResponse:
        s = request.app.state
        try:
            ticket = confirm_and_create(s.deps.confirmations, s.deps.db_path, principal, body.confirmation_id)
        except ConfirmationError as exc:
            status = {"not_found": 404, "expired": 410, "forbidden": 403}.get(exc.code, 400)
            raise HTTPException(status_code=status, detail=exc.message)
        except DatabaseUnavailable:
            raise HTTPException(status_code=503, detail="ticket database unavailable")
        log.info("ticket_created", extra={"ticket_id": ticket.ticket_id, "user_id": principal.user_id})
        return ConfirmResponse(ticket_id=ticket.ticket_id, status=ticket.status, created_at=ticket.created_at)

    @app.get("/trace/{request_id}")
    async def trace(request_id: str, principal: Authed, request: Request) -> dict:
        t = request.app.state.traces.get(request_id)
        # 404 (not 403) for other users' traces: don't reveal that the id exists.
        if t is None or (t.user_id != principal.user_id and principal.role != Role.ADMIN):
            raise HTTPException(status_code=404, detail="trace not found")
        return t.to_dict()

    return app


def app_factory() -> FastAPI:  # uvicorn --factory agentops.api.main:app_factory
    return create_app()
