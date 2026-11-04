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
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from agentops import attachments
from agentops.agent.agent import make_gemini_model
from agentops.agent.mock_llm import MockLlm
from agentops.agent.runner import AgentService
from agentops.api.schemas import (
    ChatRequest, ChatResponse, ConfirmRequest, ConfirmResponse, HealthResponse,
)
from agentops.auth import AuthError, Principal, Role, authenticate
from agentops.config import Settings, apply_model_env, get_settings
from agentops.db import DatabaseUnavailable, get_order, migrate
from agentops.observability.logging import configure_logging, get_logger
from agentops.observability.tracing import TraceStore
from agentops.rag.embeddings import make_embedder
from agentops.rag.index import PolicyIndex
from agentops.seed_data import seed
from agentops.tools.deps import ToolDeps, set_deps
from agentops.tools.support_ticket import (
    KIND_CALENDAR, ConfirmationError, confirm_and_create, confirm_and_schedule,
)
from agentops.workspace import CalendarUnavailable, make_calendar

log = get_logger("agentops.api")

_INDEX_HTML = Path(__file__).parent / "static" / "index.html"
# The UI is a single self-contained page: no external scripts, styles, fonts or images.
# The only external resources are Roboto / Roboto Mono from Google Fonts (optional: falls back to system fonts).
_UI_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; connect-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _runtime_label() -> str:
    """Where this process runs. Cloud Run sets K_SERVICE and K_REVISION itself; we only report them."""
    import os

    svc, rev = os.environ.get("K_SERVICE"), os.environ.get("K_REVISION")
    return f"cloud-run:{svc}/{rev}" if svc else "local"


def _runtime_label() -> str:
    """Where this process runs. Cloud Run sets K_SERVICE and K_REVISION itself; we only report them."""
    import os

    svc, rev = os.environ.get("K_SERVICE"), os.environ.get("K_REVISION")
    return f"cloud-run:{svc}/{rev}" if svc else "local"


class _DailyBudget:
    """Process-local cap on chat turns per UTC day. A guardrail for a public demo, not a billing system:
    with several Cloud Run instances each instance has its own counter (max-instances=1 in the deploy script)."""

    def __init__(self, limit: int):
        self.limit = limit
        self._day = ""
        self._used = 0

    def take(self) -> bool:
        from datetime import datetime, timezone

        if self.limit <= 0:
            return True
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self._used = today, 0
        if self._used >= self.limit:
            return False
        self._used += 1
        return True


def _resolve_modes(settings: Settings) -> tuple[str, str]:
    """Return (model_mode, embeddings_mode), honouring 'auto' and refusing impossible combos."""
    has_creds = settings.has_model_credentials
    mode = settings.mode if settings.mode != "auto" else ("gemini" if has_creds else "mock")
    emb = settings.embeddings if settings.embeddings != "auto" else ("gemini" if has_creds else "local")
    if mode == "gemini" and not has_creds:
        raise RuntimeError("AGENTOPS_MODE=gemini requires GOOGLE_API_KEY or GOOGLE_GENAI_USE_VERTEXAI + GOOGLE_CLOUD_PROJECT")
    if emb == "gemini" and not has_creds:
        raise RuntimeError("AGENTOPS_EMBEDDINGS=gemini requires GOOGLE_API_KEY or Vertex AI")
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
            # google-genai / ADK read credentials from the environment; never log or echo them.
            apply_model_env(settings)

        if not settings.db_path.exists():
            seed(settings.db_path)
            log.info("database_seeded", extra={"path": str(settings.db_path)})
        else:
            migrate(settings.db_path)

        vertex = (settings.gcp_project, settings.gcp_location) if settings.use_vertexai and settings.gcp_project else None
        key = settings.google_api_key.get_secret_value() if settings.has_api_key and not vertex else None  # type: ignore[union-attr]
        embedder = make_embedder(emb, key, vertex)
        index = PolicyIndex.load_or_build(embedder, settings.policy_dir, settings.index_dir)

        calendar = make_calendar(settings.calendar_mode, settings.calendar_id)
        deps = ToolDeps(db_path=settings.db_path, index=index, tool_timeout=settings.tool_timeout,
                        calendar=calendar, attachments_dir=settings.attachments_dir)
        set_deps(deps)

        traces = TraceStore()
        model = make_gemini_model(settings.model) if mode == "gemini" else MockLlm()
        app.state.service = AgentService(model, traces)
        app.state.deps = deps
        app.state.traces = traces
        app.state.mode = mode
        app.state.backend = settings.gemini_backend if mode == "gemini" else "none"
        app.state.embeddings = emb
        log.info("startup_complete", extra={"model_mode": mode, "backend": app.state.backend, "embeddings": emb,
                                            "calendar": calendar.provider, "chunks": len(index.chunks)})
        yield

    app = FastAPI(title="Enterprise AgentOps", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):  # never leak internals
        log.exception("unhandled_error")
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    # ---------------- routes ----------------
    @app.get("/", include_in_schema=False)
    async def ui() -> FileResponse:
        return FileResponse(
            _INDEX_HTML,
            media_type="text/html; charset=utf-8",
            headers={"Content-Security-Policy": _UI_CSP, "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
        )

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
            backend=s.backend,
            embeddings=s.embeddings,
            calendar=s.deps.calendar.provider,
            runtime=_runtime_label(),
            policy_chunks=len(s.deps.index.chunks),
            database=db,
        )

    _SAMPLES = {"cracked_band.png": "image/png"}  # explicit allowlist, no directory listing or path joins from input

    @app.get("/samples/{name}", include_in_schema=False)
    async def sample(name: str) -> FileResponse:
        if name not in _SAMPLES or not (settings.samples_dir / name).is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(settings.samples_dir / name, media_type=_SAMPLES[name],
                            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=3600"})

    budget = _DailyBudget(settings.daily_chat_budget)

    @app.post("/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, principal: Authed, request: Request, response: Response) -> ChatResponse:
        if not budget.take():
            log.warning("daily_budget_exhausted", extra={"budget": budget.limit})
            raise HTTPException(status_code=429, detail="This demo's daily request budget is used up. Try again tomorrow (UTC).")
        att = None
        if body.image_base64 and body.image_mime_type:
            try:
                att = attachments.parse_image(body.image_base64, body.image_mime_type)
            except attachments.AttachmentError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        result = await request.app.state.service.chat(principal, body.message, body.session_id, attachment=att)
        response.headers["X-Request-ID"] = result.request_id
        return ChatResponse(
            request_id=result.request_id, trace_id=result.trace_id, session_id=result.session_id,
            status=result.status, answer=result.answer, sources=result.sources,
            pending_confirmation=result.pending_confirmation, pending_confirmations=result.pending_confirmations,
            latency_ms=result.latency_ms,
            unverified_citations=result.unverified_citations,
        )

    @app.post("/confirm", response_model=ConfirmResponse)
    async def confirm(body: ConfirmRequest, principal: Authed, request: Request) -> ConfirmResponse:
        """Phase 2 of every side effect. Dispatches on the pending confirmation's kind, which the
        backend recorded at proposal time; the caller cannot turn a ticket into a calendar write."""
        s = request.app.state
        pending = s.deps.confirmations.peek(body.confirmation_id)
        kind = pending.kind if pending and pending.user_id == principal.user_id else "ticket"
        try:
            if kind == KIND_CALENDAR:
                ev = confirm_and_schedule(s.deps.confirmations, s.deps.db_path, principal, body.confirmation_id,
                                          s.deps.calendar)
                log.info("calendar_event_created", extra={"event_id": ev.event_id, "provider": ev.provider,
                                                          "user_id": principal.user_id})
                return ConfirmResponse(kind=KIND_CALENDAR, status="scheduled", created_at=ev.created_at,
                                       event_id=ev.event_id, title=ev.title, start_at=ev.start_at,
                                       provider=ev.provider, link=ev.external_link)
            ticket = confirm_and_create(s.deps.confirmations, s.deps.db_path, principal, body.confirmation_id,
                                        attachments_dir=s.deps.attachments_dir)
        except ConfirmationError as exc:
            status = {"not_found": 404, "expired": 410, "forbidden": 403}.get(exc.code, 400)
            raise HTTPException(status_code=status, detail=exc.message)
        except DatabaseUnavailable:
            raise HTTPException(status_code=503, detail="ticket database unavailable")
        except CalendarUnavailable:
            raise HTTPException(status_code=503, detail="calendar service unavailable; nothing was scheduled")
        log.info("ticket_created", extra={"ticket_id": ticket.ticket_id, "user_id": principal.user_id,
                                          "has_attachment": ticket.attachment_sha256 is not None})
        return ConfirmResponse(kind="ticket", status=ticket.status, created_at=ticket.created_at,
                               ticket_id=ticket.ticket_id, attachment_sha256=ticket.attachment_sha256)

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
