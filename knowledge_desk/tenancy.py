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
from pgvector.psycopg import Vector

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
        with connect(self.org_id) as conn:
            return conn.execute(
                "select id, name, created_at from groups"
                " where org_id = %s order by name",
                (self.org_id,),
            ).fetchall()

    def create_group(self, name: str) -> dict[str, Any]:
        self.require_role("admin")
        try:
            with connect(self.org_id) as conn:
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
        with connect(self.org_id) as conn:
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
        with connect(self.org_id) as conn:
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
        with connect(self.org_id) as conn:
            return conn.execute(
                "select d.id, d.path, d.source, d.status, d.content_hash,"
                " d.pii_types, d.updated_at,"
                " (select count(*) from chunks c where c.document_id = d.id)"
                " as chunk_count"
                " from documents d where d.org_id = %s order by d.path",
                (self.org_id,),
            ).fetchall()

    def delete_document(self, document_id: str) -> None:
        """Delete a document and everything derived from it. Chunks cascade via
        the foreign key; the ACL lives on the document row, so it goes too."""
        with connect(self.org_id) as conn:
            row = conn.execute(
                "delete from documents where id = %s and org_id = %s returning id",
                (document_id, self.org_id),
            ).fetchone()
        if row is None:
            raise NotFound(f"document not found: {document_id}")

    def export(self) -> dict[str, Any]:
        """A portable snapshot of the org's members and documents (metadata, not
        raw content). Backs the tenant data-export requirement."""
        return {"members": self.list_members(), "documents": self.list_documents()}

    # --- retrieval --------------------------------------------------------

    def principals(self) -> list[str]:
        """The caller's access set: org-wide, their own user principal, and one
        principal per group they belong to. Computed fresh on every call, so a
        group change takes effect on the next query with no cache to invalidate.
        """
        with connect(self.org_id) as conn:
            groups = conn.execute(
                "select gm.group_id from group_members gm"
                " join groups g on g.id = gm.group_id"
                " where g.org_id = %s and gm.user_id = %s",
                (self.org_id, self.ctx.user_id),
            ).fetchall()
        principals = ["public-to-org", f"user:{self.ctx.user_id}"]
        principals += [f"group:{r['group_id']}" for r in groups]
        return principals

    def search(self, query_embedding: list[float], k: int = 5) -> list[dict[str, Any]]:
        """Nearest chunks the caller is allowed to see. The ACL filter is part of
        the candidate fetch (`d.acl ?| principals`), so a forbidden chunk is never
        ranked, never scored, and cannot leak through a missed post-filter. The
        org_id filter sits on top as the tenant boundary.
        """
        principals = self.principals()
        vec = Vector(query_embedding)  # binds as `vector`, not double precision[]
        with connect(self.org_id) as conn:
            return conn.execute(
                "select c.document_id, c.ordinal, c.text, d.path,"
                " (c.embedding <=> %s) as distance"
                " from chunks c join documents d on d.id = c.document_id"
                " where c.org_id = %s and d.status = 'ingested'"
                " and d.acl ?| %s"
                " order by c.embedding <=> %s limit %s",
                (vec, self.org_id, principals, vec, k),
            ).fetchall()

    # --- answers and feedback --------------------------------------------

    def record_answer(self, question: str, provider: str, refused: bool) -> str:
        with connect(self.org_id) as conn:
            row = conn.execute(
                "insert into answers(org_id, user_id, question, provider, refused)"
                " values (%s, %s, %s, %s, %s) returning id",
                (self.org_id, self.ctx.user_id, question, provider, refused),
            ).fetchone()
        return str(row["id"])

    def finalize_answer(
        self, answer_id: str, input_tokens: int, output_tokens: int, cost_usd: float
    ) -> None:
        with connect(self.org_id) as conn:
            conn.execute(
                "update answers set input_tokens = %s, output_tokens = %s,"
                " cost_usd = %s where id = %s and org_id = %s",
                (input_tokens, output_tokens, cost_usd, answer_id, self.org_id),
            )

    def mark_blocked(self, answer_id: str) -> None:
        with connect(self.org_id) as conn:
            conn.execute(
                "update answers set blocked = true where id = %s and org_id = %s",
                (answer_id, self.org_id),
            )

    def spend_last_24h(self) -> float:
        with connect(self.org_id) as conn:
            row = conn.execute(
                "select coalesce(sum(cost_usd), 0) as spend from answers"
                " where org_id = %s and created_at > now() - interval '24 hours'",
                (self.org_id,),
            ).fetchone()
        return float(row["spend"])

    def questions_this_month(self) -> int:
        with connect(self.org_id) as conn:
            row = conn.execute(
                "select count(*) as n from answers where org_id = %s"
                " and created_at >= date_trunc('month', now())",
                (self.org_id,),
            ).fetchone()
        return int(row["n"])

    def storage_usage(self) -> dict[str, int]:
        """Live document count and total content bytes for this org (excluding
        deleted documents). Used to enforce ingest caps."""
        with connect(self.org_id) as conn:
            row = conn.execute(
                "select count(*) as docs,"
                " coalesce(sum(octet_length(content)), 0) as bytes"
                " from documents where org_id = %s and status <> 'deleted'",
                (self.org_id,),
            ).fetchone()
        return {"docs": int(row["docs"]), "bytes": int(row["bytes"])}

    def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        with connect(self.org_id) as conn:
            return conn.execute(
                "select a.action, a.detail, a.created_at, u.email as actor"
                " from audit_log a left join users u on u.id = a.actor_user_id"
                " where a.org_id = %s order by a.created_at desc limit %s",
                (self.org_id, limit),
            ).fetchall()

    def add_feedback(self, answer_id: str, rating: str, note: str | None) -> None:
        with connect(self.org_id) as conn:
            answer = conn.execute(
                "select 1 from answers where id = %s and org_id = %s",
                (answer_id, self.org_id),
            ).fetchone()
            if answer is None:
                raise NotFound(f"answer not found: {answer_id}")
            try:
                conn.execute(
                    "insert into feedback(org_id, user_id, answer_id, rating, note)"
                    " values (%s, %s, %s, %s, %s)",
                    (self.org_id, self.ctx.user_id, answer_id, rating, note),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise Conflict("feedback already recorded for this answer") from exc

    # --- members ----------------------------------------------------------

    def list_members(self) -> list[dict[str, Any]]:
        with connect(self.org_id) as conn:
            return conn.execute(
                "select u.id, u.email, m.role, m.created_at"
                " from memberships m join users u on u.id = m.user_id"
                " where m.org_id = %s order by u.email",
                (self.org_id,),
            ).fetchall()
