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

    # Connection pool bounds. Keep max at or below the database's connection
    # limit divided by the number of running processes (api plus worker).
    db_pool_min: int = 1
    db_pool_max: int = 10

    anthropic_api_key: str | None = None
    voyage_api_key: str | None = None

    # When true, refuse to start in mock mode instead of falling back loudly.
    provider_strict: bool = False

    # How long a login session stays valid.
    session_ttl_hours: int = 720

    # Frontend dev server origins allowed to call the API cross-origin. In prod
    # the UI is served same-origin (Phase 8), so this only matters for `npm run dev`.
    cors_origins: list[str] = ["http://localhost:5173"]

    # Serve the built SPA from the API (same-origin) in the container. Off by
    # default so dev and tests do not depend on a built frontend.
    serve_static: bool = False
    static_dir: str = "frontend/dist"

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
    # Across every org, calendar day. The per-org caps bound one tenant; this is
    # the only number that bounds the bill, since signup creates tenants freely.
    platform_daily_budget_usd: float = 25.0
    monthly_question_cap: int = 1000       # per org, calendar month
    rate_burst: int = 5                    # per user, token-bucket burst
    rate_per_min: int = 30                 # per user, sustained
    auth_rate_burst: int = 10              # per client IP, on login and signup
    auth_rate_per_min: int = 10            # per client IP, sustained

    # Header carrying the real client IP when the app sits behind a proxy that
    # sets it (Fly-Client-IP on Fly). Unset means trust the socket peer, which is
    # right locally and wrong behind a proxy, where every caller would otherwise
    # share the proxy's bucket. Only set this to a header the proxy overwrites on
    # the way in; one a client can supply itself is a limiter that bypasses
    # itself.
    client_ip_header: str | None = None
    org_doc_cap: int = 1000                # per org, total documents
    org_storage_bytes_cap: int = 50_000_000  # per org, total content bytes

    # Hard ceiling on a single request body, enforced before the body is read.
    # The org caps above are the policy; this is what stops a request that will
    # fail them from costing the memory to find out. Uploads past this have to
    # be split into batches.
    max_request_bytes: int = 16_000_000

    @property
    def provider(self) -> str:
        """"real" only when both keys are present; otherwise the mock fallback."""
        if self.anthropic_api_key and self.voyage_api_key:
            return "real"
        return "mock"


settings = Settings()
