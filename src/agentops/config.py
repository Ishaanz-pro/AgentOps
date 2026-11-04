"""Application settings loaded from environment variables.

Secrets (the API key) are held in a SecretStr so they never appear in
reprs, logs, or error messages by accident.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    google_api_key: SecretStr | None = Field(default=None, alias="GOOGLE_API_KEY")
    model: str = Field(default="gemini-3.5-flash", alias="AGENTOPS_MODEL")
    # "auto": Gemini when GOOGLE_API_KEY is set, otherwise the offline local embedder.
    embeddings: Literal["auto", "gemini", "local"] = Field(default="auto", alias="AGENTOPS_EMBEDDINGS")
    # "auto": real Gemini when a key is set, otherwise the offline MockLlm. "mock" forces the mock.
    mode: Literal["auto", "gemini", "mock"] = Field(default="auto", alias="AGENTOPS_MODE")
    db_path: Path = Field(default=Path("data/agentops.db"), alias="AGENTOPS_DB_PATH")
    policy_dir: Path = Field(default=Path("data/policies"), alias="AGENTOPS_POLICY_DIR")
    index_dir: Path = Field(default=Path("data/index"), alias="AGENTOPS_INDEX_DIR")
    tool_timeout: float = Field(default=5.0, alias="AGENTOPS_TOOL_TIMEOUT")
    log_level: str = Field(default="INFO", alias="AGENTOPS_LOG_LEVEL")

    # Vertex AI backend. On Cloud Run / Agent Engine this is keyless: google-genai uses the
    # runtime service account through Application Default Credentials. Same env names as google-genai.
    use_vertexai: bool = Field(default=False, alias="GOOGLE_GENAI_USE_VERTEXAI")
    gcp_project: str | None = Field(default=None, alias="GOOGLE_CLOUD_PROJECT")
    gcp_location: str = Field(default="global", alias="GOOGLE_CLOUD_LOCATION")

    # Google Workspace (Calendar) for schedule_followup. "dryrun" writes only the local audit
    # outbox; "google" also inserts the event into AGENTOPS_CALENDAR_ID via the Calendar API.
    calendar_mode: Literal["dryrun", "google"] = Field(default="dryrun", alias="AGENTOPS_CALENDAR")
    calendar_id: str | None = Field(default=None, alias="AGENTOPS_CALENDAR_ID")
    attachments_dir: Path = Field(default=Path("data/attachments"), alias="AGENTOPS_ATTACHMENTS_DIR")
    samples_dir: Path = Field(default=Path("data/samples"), alias="AGENTOPS_SAMPLES_DIR")
    # Cost guardrail for a public demo URL: chat turns per UTC day across all users, 0 = unlimited.
    daily_chat_budget: int = Field(default=0, alias="AGENTOPS_DAILY_CHAT_BUDGET")

    @property
    def has_api_key(self) -> bool:
        """True only for a real-looking key; the .env.example placeholder counts as 'no key'."""
        if self.google_api_key is None:
            return False
        value = self.google_api_key.get_secret_value().strip()
        return bool(value) and not value.lower().startswith("your-")

    @property
    def has_model_credentials(self) -> bool:
        """An AI Studio key, or Vertex AI with a project (credentials come from ADC)."""
        return (self.use_vertexai and bool(self.gcp_project)) or self.has_api_key

    @property
    def gemini_backend(self) -> str:
        return "vertex-ai" if self.use_vertexai else "ai-studio"


def apply_model_env(settings: Settings) -> None:
    """Export the model credentials google-genai / ADK read from the process environment.

    Vertex AI wins when enabled so a stray API key can never silently route traffic
    to a different backend than /health reports. The key itself is never logged.
    """
    import os

    if settings.use_vertexai:
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
        os.environ["GOOGLE_CLOUD_PROJECT"] = settings.gcp_project or ""
        os.environ["GOOGLE_CLOUD_LOCATION"] = settings.gcp_location
        os.environ.pop("GOOGLE_API_KEY", None)
    elif settings.has_api_key:
        os.environ["GOOGLE_API_KEY"] = settings.google_api_key.get_secret_value()  # type: ignore[union-attr]


@lru_cache
def get_settings() -> Settings:
    return Settings()
