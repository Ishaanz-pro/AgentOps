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
    model: str = Field(default="gemini-2.5-flash", alias="AGENTOPS_MODEL")
    # "auto": Gemini when GOOGLE_API_KEY is set, otherwise the offline local embedder.
    embeddings: Literal["auto", "gemini", "local"] = Field(default="auto", alias="AGENTOPS_EMBEDDINGS")
    # "auto": real Gemini when a key is set, otherwise the offline MockLlm. "mock" forces the mock.
    mode: Literal["auto", "gemini", "mock"] = Field(default="auto", alias="AGENTOPS_MODE")
    db_path: Path = Field(default=Path("data/agentops.db"), alias="AGENTOPS_DB_PATH")
    policy_dir: Path = Field(default=Path("data/policies"), alias="AGENTOPS_POLICY_DIR")
    index_dir: Path = Field(default=Path("data/index"), alias="AGENTOPS_INDEX_DIR")
    tool_timeout: float = Field(default=5.0, alias="AGENTOPS_TOOL_TIMEOUT")
    log_level: str = Field(default="INFO", alias="AGENTOPS_LOG_LEVEL")

    @property
    def has_api_key(self) -> bool:
        return self.google_api_key is not None and bool(self.google_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
