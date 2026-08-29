"""Request and response bodies for the HTTP API.

Separated from the routes because this is where the trust boundary is drawn.
Every one of these models is the first thing an untrusted request meets, and the
bounds on them — how long a question may be, how many documents one upload may
carry, which roles are even nameable — are a policy worth reading in one place
rather than finding scattered between handlers.

The bounds here are validation, not the whole story: they cap a single request,
while the per-tenant quotas in TenantScope cap the account, and the body limit
in bodylimit.py refuses an oversized request before it is ever parsed into one
of these.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

# Shared constraints, named once so the same rule cannot drift between two
# endpoints that mean the same thing by it.
Slug = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
Password = Field(min_length=8, max_length=200)
Role = Field(pattern=r"^(owner|admin|member)$")
Email = Field(min_length=3, max_length=200)


def _no_control_characters(value: str) -> str:
    """Reject control characters in a document path.

    A path is uploaded text, and it is rendered into the answer prompt on the
    citation line above the passage. The renderer neutralizes our delimiters
    wherever they appear, but a newline needs no delimiter to do damage: it ends
    the citation line and opens a fresh one, which is most of what an injection
    is after. A real file path has no use for a control character, so the
    cheapest place to settle it is here, before the text is ever stored.
    """
    if any(ch < " " or ch == "\x7f" for ch in value):
        raise ValueError("must not contain control characters")
    return value


DocumentPath = Annotated[
    str, Field(min_length=1, max_length=1024), AfterValidator(_no_control_characters)
]


# --- auth -----------------------------------------------------------------


class SignupRequest(BaseModel):
    org_slug: str = Slug
    org_name: str = Field(min_length=1, max_length=100)
    email: str = Email
    password: str = Password


class LoginRequest(BaseModel):
    email: str = Email
    password: str = Password
    org_slug: str | None = None


class TokenResponse(BaseModel):
    token: str
    org_id: str
    role: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Password
    new_password: str = Password


# --- members and groups ---------------------------------------------------


class AddMemberRequest(BaseModel):
    email: str = Email
    password: str = Password
    role: str = Role


class SetRoleRequest(BaseModel):
    role: str = Role


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class AddGroupMemberRequest(BaseModel):
    email: str = Email


# --- sources and documents ------------------------------------------------


class UploadedDocument(BaseModel):
    path: DocumentPath
    content: str = Field(max_length=1_000_000)
    acl: list[str] | None = None


class FolderUploadRequest(BaseModel):
    # 1000 documents of 1MB is far more than any single request should carry;
    # what actually stops one that large is the body limit, which refuses it
    # before it reaches this model. These bounds are the backstop.
    documents: list[UploadedDocument] = Field(max_length=1000)


class UpdateAclRequest(BaseModel):
    acl: list[str] = Field(max_length=200)


# --- retrieval and the assistant ------------------------------------------


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=5, ge=1, le=50)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    k: int | None = Field(default=None, ge=1, le=50)


class FeedbackRequest(BaseModel):
    answer_id: str
    rating: str = Field(pattern=r"^(up|down)$")
    note: str | None = Field(default=None, max_length=2000)
