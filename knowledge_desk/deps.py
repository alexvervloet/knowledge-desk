"""FastAPI wiring: pull the bearer token, resolve it to an acting context, and
hand routes a ready TenantScope. Domain errors are mapped to HTTP codes here so
route handlers can just raise them.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from knowledge_desk import accounts
from knowledge_desk.config import settings
from knowledge_desk.errors import AuthError, Conflict, DomainError, Forbidden, NotFound
from knowledge_desk.ratelimit import auth_limiter
from knowledge_desk.tenancy import AuthContext, TenantScope


def client_key(request: Request) -> str:
    """Identify an unauthenticated caller for rate limiting.

    There is no user id yet on the auth routes, so the caller's address is the
    only handle available. Behind a proxy the socket peer is the proxy, which
    would put every user in one bucket, so a configured header wins when present.
    """
    header = settings.client_ip_header
    if header:
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def auth_rate_limit(request: Request) -> None:
    """Throttle the unauthenticated auth routes per caller address.

    Without this, `/auth/login` takes password guesses as fast as they arrive,
    and each one costs a bcrypt verification, so the same requests are both a
    brute-force channel and a way to spend someone else's CPU.
    """
    allowed, retry_after = auth_limiter.check(
        client_key(request), settings.auth_rate_burst, settings.auth_rate_per_min
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="too many attempts",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


def bearer_token(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return authorization.split(" ", 1)[1].strip()


def current_ctx(token: Annotated[str, Depends(bearer_token)]) -> AuthContext:
    ctx = accounts.resolve_session(token)
    if ctx is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    return ctx


def current_scope(ctx: Annotated[AuthContext, Depends(current_ctx)]) -> TenantScope:
    return TenantScope(ctx)


_STATUS = {
    AuthError: 401,
    Forbidden: 403,
    NotFound: 404,
    Conflict: 409,
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _handle(_: Request, exc: DomainError) -> JSONResponse:
        status = next(
            (code for typ, code in _STATUS.items() if isinstance(exc, typ)), 400
        )
        return JSONResponse(status_code=status, content={"detail": str(exc)})
