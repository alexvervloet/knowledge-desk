"""The tenant-scoped data layer.

Every org-scoped read and write goes through TenantScope, which carries the
acting user's org_id and stamps it onto every query. This is the single choke
point that makes cross-tenant leakage a code-review target instead of something
spread across every handler: if a query touches org data and is not a method
here, that is the bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from knowledge_desk.auth import role_at_least
from knowledge_desk.db import connect
from knowledge_desk.errors import Conflict, Forbidden, NotFound


@dataclass(frozen=True)
class AuthContext:
    """Who is acting, and in which org. Built from a resolved session."""

    user_id: str
    org_id: str
    role: str
    email: str


class TenantScope:
    def __init__(self, ctx: AuthContext) -> None:
        self.ctx = ctx

    @property
    def org_id(self) -> str:
        return self.ctx.org_id

    def require_role(self, minimum: str) -> None:
        if not role_at_least(self.ctx.role, minimum):
            raise Forbidden(f"requires role {minimum}, caller is {self.ctx.role}")

    # --- groups -----------------------------------------------------------

    def list_groups(self) -> list[dict[str, Any]]:
        with connect() as conn:
            return conn.execute(
                "select id, name, created_at from groups"
                " where org_id = %s order by name",
                (self.org_id,),
            ).fetchall()

    def create_group(self, name: str) -> dict[str, Any]:
        self.require_role("admin")
        try:
            with connect() as conn:
                return conn.execute(
                    "insert into groups(org_id, name) values (%s, %s)"
                    " returning id, name, created_at",
                    (self.org_id, name),
                ).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise Conflict(f"group already exists: {name}") from exc

    def get_group(self, group_id: str) -> dict[str, Any]:
        """Fetch a group by id, but only within the caller's org. A group that
        belongs to another org is indistinguishable from one that does not
        exist: the org_id filter is the isolation boundary.
        """
        with connect() as conn:
            row = conn.execute(
                "select id, name, created_at from groups"
                " where id = %s and org_id = %s",
                (group_id, self.org_id),
            ).fetchone()
        if row is None:
            raise NotFound(f"group not found: {group_id}")
        return row

    def add_group_member(self, group_id: str, user_id: str) -> None:
        self.require_role("admin")
        self.get_group(group_id)  # 404s if the group is not in this org
        with connect() as conn:
            is_member = conn.execute(
                "select 1 from memberships where user_id = %s and org_id = %s",
                (user_id, self.org_id),
            ).fetchone()
            if is_member is None:
                # Cannot add a user to a group unless they belong to this org.
                raise NotFound(f"user is not a member of this org: {user_id}")
            conn.execute(
                "insert into group_members(group_id, user_id) values (%s, %s)"
                " on conflict do nothing",
                (group_id, user_id),
            )

    # --- members ----------------------------------------------------------

    # --- documents --------------------------------------------------------

    def list_documents(self) -> list[dict[str, Any]]:
        with connect() as conn:
            return conn.execute(
                "select d.id, d.path, d.source, d.status, d.content_hash,"
                " d.updated_at,"
                " (select count(*) from chunks c where c.document_id = d.id)"
                " as chunk_count"
                " from documents d where d.org_id = %s order by d.path",
                (self.org_id,),
            ).fetchall()

    # --- members ----------------------------------------------------------

    def list_members(self) -> list[dict[str, Any]]:
        with connect() as conn:
            return conn.execute(
                "select u.id, u.email, m.role, m.created_at"
                " from memberships m join users u on u.id = m.user_id"
                " where m.org_id = %s order by u.email",
                (self.org_id,),
            ).fetchall()
