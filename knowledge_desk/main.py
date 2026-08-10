"""FastAPI application.

Phase 0 gave a keyless skeleton; Phase 1 adds the multi-tenant spine: signup,
login, sessions, members, and org-scoped groups. No LLM yet. Every org-scoped
route acts through a TenantScope so tenant isolation is enforced in one place.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from knowledge_desk import __version__, accounts, assistant, audit, ingest, retrieval, tracing
from knowledge_desk.config import settings
from knowledge_desk.deps import (
    auth_rate_limit,
    bearer_token,
    current_ctx,
    current_scope,
    register_error_handlers,
)
from knowledge_desk.ratelimit import limiter
from knowledge_desk.tenancy import AuthContext, TenantScope

app = FastAPI(title="Knowledge Desk", version=__version__)
register_error_handlers(app)
tracing.init()  # enables Langfuse only if LANGFUSE_* keys are set; no-op otherwise
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    # The SPA reads the paging total from a response header, which a browser
    # hides on cross-origin replies unless it is exposed. Same-origin in prod,
    # cross-origin against the dev server.
    expose_headers=["X-Total-Count"],
)

_KNOWN_COLLECTIONS = {"demo"}

Slug = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
Password = Field(min_length=8, max_length=200)
Role = Field(pattern=r"^(owner|admin|member)$")


# --- health and Phase 0 surface ------------------------------------------


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "version": __version__, "provider": settings.provider}


@app.get("/collections/{name}")
def get_collection(name: str):
    if name not in _KNOWN_COLLECTIONS:
        raise HTTPException(status_code=404, detail=f"unknown collection: {name}")
    return {"name": name}


# --- auth -----------------------------------------------------------------


class SignupRequest(BaseModel):
    org_slug: str = Slug
    org_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=200)
    password: str = Password


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Password
    org_slug: str | None = None


class TokenResponse(BaseModel):
    token: str
    org_id: str
    role: str


@app.post("/auth/signup", status_code=status.HTTP_201_CREATED, response_model=TokenResponse)
def signup(
    req: SignupRequest, _: Annotated[None, Depends(auth_rate_limit)] = None
) -> TokenResponse:
    ctx = accounts.create_org_with_owner(
        req.org_slug, req.org_name, req.email, req.password
    )
    token = accounts.create_session(ctx)
    audit.log(ctx.org_id, ctx.user_id, "org.created", {"slug": req.org_slug})
    return TokenResponse(token=token, org_id=ctx.org_id, role=ctx.role)


@app.post("/auth/login", response_model=TokenResponse)
def login(
    req: LoginRequest, _: Annotated[None, Depends(auth_rate_limit)] = None
) -> TokenResponse:
    ctx = accounts.authenticate(req.email, req.password, req.org_slug)
    token = accounts.create_session(ctx)
    audit.log(ctx.org_id, ctx.user_id, "user.login", {})
    return TokenResponse(token=token, org_id=ctx.org_id, role=ctx.role)


@app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(token: Annotated[str, Depends(bearer_token)]) -> Response:
    accounts.delete_session(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/me")
def me(ctx: Annotated[AuthContext, Depends(current_ctx)]) -> dict:
    return {
        "user_id": ctx.user_id,
        "email": ctx.email,
        "org_id": ctx.org_id,
        "role": ctx.role,
    }


# --- members --------------------------------------------------------------


class AddMemberRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Password
    role: str = Role


@app.post("/members", status_code=status.HTTP_201_CREATED)
def add_member(
    req: AddMemberRequest, scope: Annotated[TenantScope, Depends(current_scope)]
) -> dict:
    scope.require_can_grant(req.role)
    user_id = accounts.add_member(scope.org_id, req.email, req.password, req.role)
    audit.log(scope.org_id, scope.ctx.user_id, "member.added",
              {"user_id": user_id, "role": req.role})
    return {"user_id": user_id}


@app.get("/members")
def list_members(
    response: Response,
    scope: Annotated[TenantScope, Depends(current_scope)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    response.headers["X-Total-Count"] = str(scope.count_members())
    return scope.list_members(limit, offset)


class SetRoleRequest(BaseModel):
    role: str = Role


@app.patch("/members/{user_id}")
def set_member_role(
    user_id: str, req: SetRoleRequest, scope: Annotated[TenantScope, Depends(current_scope)]
) -> dict:
    scope.set_member_role(user_id, req.role)
    audit.log(scope.org_id, scope.ctx.user_id, "member.role_changed",
              {"user_id": user_id, "role": req.role})
    return {"user_id": user_id, "role": req.role}


@app.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    user_id: str, scope: Annotated[TenantScope, Depends(current_scope)]
) -> Response:
    scope.remove_member(user_id)
    audit.log(scope.org_id, scope.ctx.user_id, "member.removed", {"user_id": user_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- groups ---------------------------------------------------------------


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class AddGroupMemberRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)


@app.post("/groups", status_code=status.HTTP_201_CREATED)
def create_group(
    req: CreateGroupRequest, scope: Annotated[TenantScope, Depends(current_scope)]
) -> dict:
    return scope.create_group(req.name)


@app.get("/groups")
def list_groups(scope: Annotated[TenantScope, Depends(current_scope)]) -> list[dict]:
    return scope.list_groups()


@app.get("/groups/{group_id}")
def get_group(
    group_id: str, scope: Annotated[TenantScope, Depends(current_scope)]
) -> dict:
    return scope.get_group(group_id)


@app.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(
    group_id: str, scope: Annotated[TenantScope, Depends(current_scope)]
) -> Response:
    scope.delete_group(group_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/groups/{group_id}/members")
def list_group_members(
    group_id: str, scope: Annotated[TenantScope, Depends(current_scope)]
) -> list[dict]:
    return scope.list_group_members(group_id)


@app.post("/groups/{group_id}/members", status_code=status.HTTP_201_CREATED)
def add_group_member(
    group_id: str,
    req: AddGroupMemberRequest,
    scope: Annotated[TenantScope, Depends(current_scope)],
) -> dict:
    user_id = accounts.find_user_id(req.email)
    scope.add_group_member(group_id, user_id)
    return {"group_id": group_id, "user_id": user_id}


@app.delete("/groups/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_group_member(
    group_id: str, user_id: str, scope: Annotated[TenantScope, Depends(current_scope)]
) -> Response:
    scope.remove_group_member(group_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- sources and documents ------------------------------------------------

LOCAL_FOLDER_SOURCE = "local-folder"


class UploadedDocument(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=1_000_000)
    acl: list[str] | None = None


class FolderUploadRequest(BaseModel):
    documents: list[UploadedDocument] = Field(max_length=1000)


@app.post("/sources/folder", status_code=status.HTTP_202_ACCEPTED)
def upload_folder(
    req: FolderUploadRequest, scope: Annotated[TenantScope, Depends(current_scope)]
) -> dict:
    """Reconcile an org's local-folder documents and enqueue ingest jobs for the
    ones that changed. Returns immediately (202); the worker does the embedding.
    """
    scope.require_role("admin")
    items = [d.model_dump() for d in req.documents]

    # Enforce per-org caps before enqueuing any embedding work. Conservative:
    # an update counts toward the incoming total, which can only over-protect.
    usage = scope.storage_usage()
    incoming_bytes = sum(len(d.content.encode("utf-8")) for d in req.documents)
    if usage["bytes"] + incoming_bytes > settings.org_storage_bytes_cap:
        raise HTTPException(status_code=413, detail="org storage cap exceeded")
    if usage["docs"] + len(req.documents) > settings.org_doc_cap:
        raise HTTPException(status_code=413, detail="org document cap exceeded")

    result = ingest.sync_documents(scope.org_id, LOCAL_FOLDER_SOURCE, items)
    audit.log(scope.org_id, scope.ctx.user_id, "source.synced", result)
    return result


@app.get("/documents")
def list_documents(
    response: Response,
    scope: Annotated[TenantScope, Depends(current_scope)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    response.headers["X-Total-Count"] = str(scope.count_documents())
    return scope.list_documents(limit, offset)


@app.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str, scope: Annotated[TenantScope, Depends(current_scope)]
) -> Response:
    scope.require_role("admin")
    scope.delete_document(document_id)
    audit.log(scope.org_id, scope.ctx.user_id, "document.deleted", {"document_id": document_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class UpdateAclRequest(BaseModel):
    acl: list[str] = Field(max_length=200)


@app.patch("/documents/{document_id}/acl")
def update_document_acl(
    document_id: str,
    req: UpdateAclRequest,
    scope: Annotated[TenantScope, Depends(current_scope)],
) -> dict:
    scope.update_document_acl(document_id, req.acl)
    audit.log(scope.org_id, scope.ctx.user_id, "document.acl_changed",
              {"document_id": document_id})
    return {"document_id": document_id, "acl": req.acl}


# --- tenant retention -----------------------------------------------------


@app.get("/org/export")
def export_org(scope: Annotated[TenantScope, Depends(current_scope)]) -> dict:
    scope.require_role("admin")
    return scope.export()


@app.delete("/org", status_code=status.HTTP_204_NO_CONTENT)
def delete_org(scope: Annotated[TenantScope, Depends(current_scope)]) -> Response:
    """Delete the whole tenant. Owner only, and irreversible: cascades to every
    org-scoped table."""
    scope.require_role("owner")
    accounts.delete_org(scope.org_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- retrieval ------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=5, ge=1, le=50)


@app.post("/search")
def search(
    req: SearchRequest, scope: Annotated[TenantScope, Depends(current_scope)]
) -> list[dict]:
    """Nearest chunks the caller is permitted to see. Access filtering happens in
    the candidate fetch, so results can only ever contain allowed content.
    """
    return retrieval.search(scope, req.query, req.k)


# --- assistant ------------------------------------------------------------


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    k: int | None = Field(default=None, ge=1, le=50)


@app.post("/ask")
def ask(req: AskRequest, scope: Annotated[TenantScope, Depends(current_scope)]):
    """Stream a grounded, access-scoped answer as SSE. Each event is one
    `data: {json}` frame: meta, sources, token(s), then done (or error).

    A per-user rate limit is enforced up front as a 429; the per-org budget and
    question caps are enforced inside the stream as a loud limit frame (see the
    assistant), so the client always gets a clear signal rather than silence.
    """
    allowed, retry_after = limiter.check(scope.ctx.user_id)
    if not allowed:
        raise HTTPException(
            status_code=429, detail="rate limit exceeded",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    k = req.k or settings.retrieval_k

    def frames():
        for event in assistant.answer_stream(scope, req.question, k):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(frames(), media_type="text/event-stream")


# --- audit ----------------------------------------------------------------


@app.get("/audit")
def audit_log(
    response: Response,
    scope: Annotated[TenantScope, Depends(current_scope)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    """Recent audit events for the caller's org. Admin only."""
    scope.require_role("admin")
    response.headers["X-Total-Count"] = str(scope.count_audit())
    return scope.list_audit(limit, offset)


@app.get("/usage")
def usage(scope: Annotated[TenantScope, Depends(current_scope)]) -> dict:
    """Org usage against its caps, for the admin dashboard. Admin only."""
    scope.require_role("admin")
    return scope.usage_summary()


class FeedbackRequest(BaseModel):
    answer_id: str
    rating: str = Field(pattern=r"^(up|down)$")
    note: str | None = Field(default=None, max_length=2000)


@app.post("/feedback", status_code=status.HTTP_201_CREATED)
def feedback(
    req: FeedbackRequest, scope: Annotated[TenantScope, Depends(current_scope)]
) -> dict:
    scope.add_feedback(req.answer_id, req.rating, req.note)
    return {"answer_id": req.answer_id, "rating": req.rating}


# --- static SPA (production) ----------------------------------------------
#
# Mounted last so it never shadows an API route: unmatched paths fall through to
# the built frontend. Off unless SERVE_STATIC=1 and the build exists, so dev and
# tests do not depend on a compiled UI.
if settings.serve_static:
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    _static = Path(settings.static_dir)
    if _static.is_dir():
        app.mount("/", StaticFiles(directory=_static, html=True), name="spa")
