"""Runtime configuration, loaded from the environment (and .env for local dev).

Provider selection is derived, not configured: with no keys the app runs in the
loud mock fallback so it works and tests green with zero setup. PROVIDER_STRICT=1
turns the fallback into a hard startup error instead.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Owner role: migrations, DDL, and preflight. Superuser in dev.
    database_url: str = "postgresql://kd:kd@localhost:5436/knowledge_desk"
    # Least-privilege app role: every runtime query. Non-owner so RLS applies.
    app_database_url: str = "postgresql://kd_app:kd_app@localhost:5436/knowledge_desk"

    anthropic_api_key: str | None = None
    voyage_api_key: str | None = None

    # When true, refuse to start in mock mode instead of falling back loudly.
    provider_strict: bool = False

    # How long a login session stays valid.
    session_ttl_hours: int = 720

    # Ingestion.
    embed_model: str = "voyage-3"
    chunk_size: int = 1000
    chunk_overlap: int = 150
    job_max_attempts: int = 3

    # Assistant.
    answer_model: str = "claude-opus-5"
    answer_max_tokens: int = 2048
    retrieval_k: int = 6

    # Operational controls (all env-overridable).
    daily_budget_usd: float = 5.0          # per org, rolling 24h
    monthly_question_cap: int = 1000       # per org, calendar month
    rate_burst: int = 5                    # per user, token-bucket burst
    rate_per_min: int = 30                 # per user, sustained
    org_doc_cap: int = 1000                # per org, total documents
    org_storage_bytes_cap: int = 50_000_000  # per org, total content bytes

    @property
    def provider(self) -> str:
        """"real" only when both keys are present; otherwise the mock fallback."""
        if self.anthropic_api_key and self.voyage_api_key:
            return "real"
        return "mock"


settings = Settings()
