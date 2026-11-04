"""AgentService: runs one chat turn through ADK for an authenticated principal.

Security-relevant points
- The principal is written into ADK session state HERE, from the verified
  bearer token. Tools read it from state; the model has no way to set it.
- Sessions are scoped by (app, user_id, session_id), so one user cannot
  resume another user's session by guessing its id.
- Every turn is bounded by an overall timeout and always produces a Trace.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from google.adk.models import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agentops.agent.agent import build_agent
from agentops.agent.citations import unverified_citations
from agentops.auth import Principal
from agentops.observability.logging import get_logger
from agentops.observability.tracing import Trace, TraceStore, clear_trace, start_trace
from agentops.tools.registry import PRINCIPAL_STATE_KEY

log = get_logger("agentops.agent.runner")
APP_NAME = "agentops"


@dataclass
class ChatResult:
    request_id: str
    trace_id: str
    session_id: str
    status: str                       # ok | error
    answer: str
    sources: list[str] = field(default_factory=list)
    pending_confirmation: dict[str, Any] | None = None
    latency_ms: float | None = None
    error: str | None = None
    unverified_citations: list[str] = field(default_factory=list)


def classify_model_error(exc: BaseException) -> tuple[str, str]:
    """Map a failed turn to (error_code, user-facing message) without leaking internals.

    Rate limits and upstream outages are distinct, expected conditions in a live
    system and must not be reported as a generic failure.
    """
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    name = type(exc).__name__
    text = str(exc)
    if code == 429 or "RESOURCE_EXHAUSTED" in text or "ResourceExhausted" in name:
        return "rate_limited", (
            "The AI model's request quota is exhausted right now (rate limit). "
            "Nothing was looked up or changed. Please try again in a minute."
        )
    if code in (500, 502, 503, 504) or "UNAVAILABLE" in text:
        return "model_unavailable", "The AI model service is temporarily unavailable. Please try again shortly."
    return name, "Sorry, something went wrong while handling your request."


class AgentService:
    def __init__(self, model: str | BaseLlm, traces: TraceStore, turn_timeout: float = 60.0):
        self.traces = traces
        self.turn_timeout = turn_timeout
        self.session_service = InMemorySessionService()
        self.agent = build_agent(model)
        self.runner = Runner(app_name=APP_NAME, agent=self.agent, session_service=self.session_service)

    async def _ensure_session(self, principal: Principal, session_id: str | None) -> str:
        if session_id:
            existing = await self.session_service.get_session(
                app_name=APP_NAME, user_id=principal.user_id, session_id=session_id
            )
            if existing is not None:
                return session_id
        sid = session_id or f"ses-{uuid.uuid4().hex[:12]}"
        await self.session_service.create_session(
            app_name=APP_NAME,
            user_id=principal.user_id,
            session_id=sid,
            state={PRINCIPAL_STATE_KEY: principal.user_id},
        )
        return sid

    async def chat(self, principal: Principal, message: str, session_id: str | None = None) -> ChatResult:
        trace = start_trace(principal.user_id, str(principal.role))
        log.info("request_received", extra={"message_chars": len(message), "role": str(principal.role)})
        sid = ""
        answer = ""
        retrieved: list[str] = []
        retrieved_texts: list[str] = []
        error: str | None = None
        try:
            sid = await self._ensure_session(principal, session_id)
            answer, retrieved, retrieved_texts = await asyncio.wait_for(
                self._run(principal, sid, message, trace), self.turn_timeout
            )
        except asyncio.TimeoutError:
            error = f"turn exceeded {self.turn_timeout}s"
            answer = "Sorry, that took too long. Please try again."
        except Exception as exc:  # noqa: BLE001 - surface a safe message, keep detail in trace/logs
            error, answer = classify_model_error(exc)
            log.exception("agent_turn_failed", extra={"error": error})

        # Provenance check: the answer may only cite policy sections retrieved in this turn.
        unverified = unverified_citations(answer, retrieved, retrieved_texts) if not error else []
        if unverified:
            trace.add_step("error", "unverified_citation", citations=unverified)
            log.warning("unverified_citation", extra={"citations": unverified})

        trace.finish(error)
        self.traces.put(trace)
        clear_trace()
        log.info(
            "request_completed",
            extra={"status": trace.status, "latency_ms": trace.latency_ms,
                   "tools": [t["tool"] for t in trace.tool_calls], "error": error},
        )
        cited = [s for s in dict.fromkeys(retrieved) if s.split(" §")[0] in answer]
        return ChatResult(
            request_id=trace.request_id, trace_id=trace.trace_id, session_id=sid,
            status=trace.status, answer=answer, sources=cited,
            pending_confirmation=trace.pending_confirmation, latency_ms=trace.latency_ms, error=error,
            unverified_citations=unverified,
        )

    async def _run(self, principal: Principal, sid: str, message: str, trace: Trace) -> tuple[str, list[str], list[str]]:
        content = types.Content(role="user", parts=[types.Part(text=message)])
        final_text = ""
        retrieved: list[str] = []
        texts: list[str] = []
        async for event in self.runner.run_async(user_id=principal.user_id, session_id=sid, new_message=content):
            for fc in event.get_function_calls() or []:
                trace.add_step("agent", "model_requested_tool", tool=fc.name, arg_keys=sorted((fc.args or {}).keys()))
            for fr in event.get_function_responses() or []:
                body = fr.response or {}
                if fr.name == "search_policy_documents" and body.get("ok"):
                    for p in body["data"].get("passages", []):
                        retrieved.append(p["source"])
                        texts.append(p["text"])
                        if p.get("instruction_like_content_detected"):
                            trace.add_step("agent", "untrusted_content_flagged", source=p["source"])
                            log.warning("injection_like_content_retrieved", extra={"source": p["source"]})
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "".join(p.text or "" for p in event.content.parts).strip()
        if not final_text:
            raise RuntimeError("agent produced no final response")
        return final_text, retrieved, texts
