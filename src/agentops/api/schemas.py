from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    # Optional image for multimodal turns (e.g. a photo of damaged goods). Base64 of at most 4 MB;
    # the server sniffs the real type and rejects anything that is not PNG/JPEG/WebP.
    image_base64: str | None = Field(default=None, min_length=1, max_length=6_000_000)
    image_mime_type: Literal["image/png", "image/jpeg", "image/webp"] | None = None

    @model_validator(mode="after")
    def _image_fields_together(self) -> "ChatRequest":
        if (self.image_base64 is None) != (self.image_mime_type is None):
            raise ValueError("image_base64 and image_mime_type must be provided together")
        return self


class ChatResponse(BaseModel):
    request_id: str
    trace_id: str
    session_id: str
    status: str
    answer: str
    sources: list[str]
    pending_confirmation: dict[str, Any] | None = None          # first proposed side effect (compat)
    pending_confirmations: list[dict[str, Any]] = Field(default_factory=list)  # all of them
    latency_ms: float | None = None
    unverified_citations: list[str] = Field(default_factory=list)


class ConfirmRequest(BaseModel):
    confirmation_id: str = Field(min_length=4, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")


class ConfirmResponse(BaseModel):
    kind: str                       # ticket | calendar_event
    status: str
    created_at: str
    # ticket
    ticket_id: str | None = None
    attachment_sha256: str | None = None
    # calendar_event
    event_id: str | None = None
    title: str | None = None
    start_at: str | None = None
    provider: str | None = None
    link: str | None = None


class HealthResponse(BaseModel):
    status: str
    model_mode: str            # "gemini" | "mock" (so a demo can never silently pass mock off as Gemini)
    model: str
    backend: str               # "ai-studio" | "vertex-ai" | "none"
    embeddings: str
    calendar: str              # "dryrun" | "google-calendar"
    policy_chunks: int
    database: str              # "ok" | "unavailable"
