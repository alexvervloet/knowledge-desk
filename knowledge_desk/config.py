"""Runtime configuration, loaded from the environment (and .env for local dev).

Provider selection is derived, not configured: with no keys the app runs in the
loud mock fallback so it works and tests green with zero setup. PROVIDER_STRICT=1
turns the fallback into a hard startup error instead.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://kd:kd@localhost:5436/knowledge_desk"

    anthropic_api_key: str | None = None
    voyage_api_key: str | None = None

    # When true, refuse to start in mock mode instead of falling back loudly.
    provider_strict: bool = False

    @property
    def provider(self) -> str:
        """"real" only when both keys are present; otherwise the mock fallback."""
        if self.anthropic_api_key and self.voyage_api_key:
            return "real"
        return "mock"


settings = Settings()
