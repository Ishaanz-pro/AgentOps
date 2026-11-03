from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")


class ChatResponse(BaseModel):
    request_id: str
    trace_id: str
    session_id: str
    status: str
    answer: str
    sources: list[str]
    pending_confirmation: dict[str, Any] | None = None
    latency_ms: float | None = None
    unverified_citations: list[str] = Field(default_factory=list)


class ConfirmRequest(BaseModel):
    confirmation_id: str = Field(min_length=4, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")


class ConfirmResponse(BaseModel):
    ticket_id: str
    status: str
    created_at: str


class HealthResponse(BaseModel):
    status: str
    model_mode: str            # "gemini" | "mock" (so a demo can never silently pass mock off as Gemini)
    model: str
    embeddings: str
    policy_chunks: int
    database: str              # "ok" | "unavailable"
