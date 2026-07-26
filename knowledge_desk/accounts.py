"""Account-level operations: identities, org grants, and sessions.

This module owns things that inherently cross a single org boundary: creating a
global user, granting a user access to an org (a membership), and resolving a
session into an acting context. Once a caller is acting inside one org, all
org-scoped data goes through TenantScope instead.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg

from knowledge_desk.auth import (
    hash_password,
    hash_token,
    new_session_token,
    verify_password,
)
from knowledge_desk.config import settings
from knowledge_desk.db import connect
from knowledge_desk.errors import AuthError, Conflict, NotFound
from knowledge_desk.tenancy import AuthContext


def create_org_with_owner(
    org_slug: str, org_name: str, email: str, password: str
) -> AuthContext:
    """Sign-up: create an org and its first user as owner, in one transaction."""
    email = email.strip().lower()
    try:
        with connect() as conn:
            org = conn.execute(
                "insert into orgs(slug, name) values (%s, %s) returning id",
                (org_slug, org_name),
            ).fetchone()
            user = conn.execute(
                "insert into users(email, password_hash) values (%s, %s)"
                " returning id",
                (email, hash_password(password)),
            ).fetchone()
            conn.execute(
                "insert into memberships(user_id, org_id, role)"
                " values (%s, %s, 'owner')",
                (user["id"], org["id"]),
            )
    except psycopg.errors.UniqueViolation as exc:
        raise Conflict("org slug or email already taken") from exc
    return AuthContext(
        user_id=str(user["id"]), org_id=str(org["id"]), role="owner", email=email
    )


def add_member(org_id: str, email: str, password: str, role: str) -> str:
    """Grant a user access to an org, creating the global user if needed.
    Returns the user id. The caller must already be authorized (admin+).
    """
    email = email.strip().lower()
    with connect() as conn:
        user = conn.execute(
            "select id from users where email = %s", (email,)
        ).fetchone()
        if user is None:
            user = conn.execute(
                "insert into users(email, password_hash) values (%s, %s)"
                " returning id",
                (email, hash_password(password)),
            ).fetchone()
        try:
            conn.execute(
                "insert into memberships(user_id, org_id, role) values (%s, %s, %s)",
                (user["id"], org_id, role),
            )
        except psycopg.errors.UniqueViolation as exc:
            raise Conflict("user is already a member of this org") from exc
    return str(user["id"])


def authenticate(email: str, password: str, org_slug: str | None = None) -> AuthContext:
    """Verify credentials and resolve which org the session acts in."""
    email = email.strip().lower()
    with connect() as conn:
        user = conn.execute(
            "select id, password_hash from users where email = %s", (email,)
        ).fetchone()
        if user is None or not verify_password(password, user["password_hash"]):
            raise AuthError("invalid email or password")
        memberships = conn.execute(
            "select m.org_id, m.role, o.slug from memberships m"
            " join orgs o on o.id = m.org_id where m.user_id = %s",
            (user["id"],),
        ).fetchall()

    if not memberships:
        raise AuthError("user has no org memberships")
    if org_slug is not None:
        chosen = next((m for m in memberships if m["slug"] == org_slug), None)
        if chosen is None:
            raise AuthError(f"not a member of org: {org_slug}")
    elif len(memberships) == 1:
        chosen = memberships[0]
    else:
        raise AuthError("multiple orgs; specify org_slug")

    return AuthContext(
        user_id=str(user["id"]),
        org_id=str(chosen["org_id"]),
        role=chosen["role"],
        email=email,
    )


def create_session(ctx: AuthContext) -> str:
    """Persist a session for the acting context; return the raw bearer token."""
    raw, token_hash = new_session_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    with connect() as conn:
        conn.execute(
            "insert into sessions(token_hash, user_id, org_id, expires_at)"
            " values (%s, %s, %s, %s)",
            (token_hash, ctx.user_id, ctx.org_id, expires_at),
        )
    return raw


def resolve_session(raw_token: str) -> AuthContext | None:
    """Return the acting context for a live token, or None if missing/expired."""
    with connect() as conn:
        row = conn.execute(
            "select s.user_id, s.org_id, s.expires_at, m.role, u.email"
            " from sessions s"
            " join memberships m on m.user_id = s.user_id and m.org_id = s.org_id"
            " join users u on u.id = s.user_id"
            " where s.token_hash = %s",
            (hash_token(raw_token),),
        ).fetchone()
    if row is None:
        return None
    if row["expires_at"] <= datetime.now(timezone.utc):
        return None
    return AuthContext(
        user_id=str(row["user_id"]),
        org_id=str(row["org_id"]),
        role=row["role"],
        email=row["email"],
    )


def delete_session(raw_token: str) -> None:
    with connect() as conn:
        conn.execute(
            "delete from sessions where token_hash = %s", (hash_token(raw_token),)
        )


def delete_org(org_id: str) -> None:
    """Delete an entire tenant. Every org-scoped table references orgs with
    `on delete cascade`, so this removes memberships, documents, chunks, answers,
    audit records, and sessions in one statement."""
    with connect() as conn:
        conn.execute("delete from orgs where id = %s", (org_id,))


def find_user_id(email: str) -> str:
    email = email.strip().lower()
    with connect() as conn:
        row = conn.execute(
            "select id from users where email = %s", (email,)
        ).fetchone()
    if row is None:
        raise NotFound(f"no user with email: {email}")
    return str(row["id"])
