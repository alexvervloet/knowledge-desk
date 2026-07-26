"""FastAPI application.

Phase 0 gave a keyless skeleton; Phase 1 adds the multi-tenant spine: signup,
login, sessions, members, and org-scoped groups. No LLM yet. Every org-scoped
route acts through a TenantScope so tenant isolation is enforced in one place.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from knowledge_desk import __version__, accounts, assistant, ingest, retrieval
from knowledge_desk.config import settings
from knowledge_desk.deps import (
    bearer_token,
    current_ctx,
    current_scope,
    register_error_handlers,
)
from knowledge_desk.tenancy import AuthContext, TenantScope

app = FastAPI(title="Knowledge Desk", version=__version__)
register_error_handlers(app)

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
def signup(req: SignupRequest) -> TokenResponse:
    ctx = accounts.create_org_with_owner(
        req.org_slug, req.org_name, req.email, req.password
    )
    token = accounts.create_session(ctx)
    return TokenResponse(token=token, org_id=ctx.org_id, role=ctx.role)


@app.post("/auth/login", response_model=TokenResponse)
def login(req: LoginRequest) -> TokenResponse:
    ctx = accounts.authenticate(req.email, req.password, req.org_slug)
    token = accounts.create_session(ctx)
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
    scope.require_role("admin")
    user_id = accounts.add_member(scope.org_id, req.email, req.password, req.role)
    return {"user_id": user_id}


@app.get("/members")
def list_members(scope: Annotated[TenantScope, Depends(current_scope)]) -> list[dict]:
    return scope.list_members()


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


@app.post("/groups/{group_id}/members", status_code=status.HTTP_201_CREATED)
def add_group_member(
    group_id: str,
    req: AddGroupMemberRequest,
    scope: Annotated[TenantScope, Depends(current_scope)],
) -> dict:
    user_id = accounts.find_user_id(req.email)
    scope.add_group_member(group_id, user_id)
    return {"group_id": group_id, "user_id": user_id}


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
    return ingest.sync_documents(scope.org_id, LOCAL_FOLDER_SOURCE, items)


@app.get("/documents")
def list_documents(scope: Annotated[TenantScope, Depends(current_scope)]) -> list[dict]:
    return scope.list_documents()


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
    """
    k = req.k or settings.retrieval_k

    def frames():
        for event in assistant.answer_stream(scope, req.question, k):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(frames(), media_type="text/event-stream")


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
