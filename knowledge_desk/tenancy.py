"""The tenant-scoped data layer.

Every org-scoped read and write goes through TenantScope, which carries the
acting user's org_id and stamps it onto every query. This is the single choke
point that makes cross-tenant leakage a code-review target instead of something
spread across every handler: if a query touches org data and is not a method
here, that is the bug.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg
from pgvector import Vector
from psycopg.rows import DictRow
from psycopg.types.json import Json

from knowledge_desk import ingest
from knowledge_desk.auth import role_at_least
from knowledge_desk.config import settings
from knowledge_desk.db import connect, require_row
from knowledge_desk.errors import Conflict, Forbidden, NotFound, QuotaExceeded


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

    def require_can_grant(self, role: str) -> None:
        """Gate a role assignment: admin or better, and never a role above the
        caller's own rank.

        Handing out a role you do not hold is privilege escalation with an extra
        step. An admin who can create an owner also picks that account's
        password, so they log in as it and hold every owner power, including the
        irreversible `DELETE /org`. The rank ceiling is what makes "admins manage
        members, owners manage ownership" true at the API and not just in the UI.
        """
        self.require_role("admin")
        if not role_at_least(self.ctx.role, role):
            raise Forbidden(f"cannot grant role {role}, caller is {self.ctx.role}")

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
                return require_row(conn.execute(
                    "insert into groups(org_id, name) values (%s, %s)"
                    " returning id, name, created_at",
                    (self.org_id, name),
                ).fetchone())
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

    def remove_group_member(self, group_id: str, user_id: str) -> None:
        self.require_role("admin")
        self.get_group(group_id)  # 404s if the group is not in this org
        with connect(self.org_id) as conn:
            conn.execute(
                "delete from group_members where group_id = %s and user_id = %s",
                (group_id, user_id),
            )

    def list_group_members(self, group_id: str) -> list[dict[str, Any]]:
        self.get_group(group_id)
        with connect(self.org_id) as conn:
            return conn.execute(
                "select u.id, u.email from group_members gm"
                " join users u on u.id = gm.user_id"
                " where gm.group_id = %s order by u.email",
                (group_id,),
            ).fetchall()

    def delete_group(self, group_id: str) -> None:
        self.require_role("admin")
        with connect(self.org_id) as conn:
            row = conn.execute(
                "delete from groups where id = %s and org_id = %s returning id",
                (group_id, self.org_id),
            ).fetchone()
        if row is None:
            raise NotFound(f"group not found: {group_id}")

    # --- members ----------------------------------------------------------

    def _owner_count(self, conn: psycopg.Connection[DictRow]) -> int:
        return require_row(conn.execute(
            "select count(*) as n from memberships where org_id = %s and role = 'owner'",
            (self.org_id,),
        ).fetchone())["n"]

    def set_member_role(self, user_id: str, role: str) -> None:
        """Change a member's role. You cannot change your own role (avoids
        self-lockout), you cannot grant a role above your own, and the org must
        always keep at least one owner."""
        self.require_can_grant(role)
        if user_id == self.ctx.user_id:
            raise Forbidden("you cannot change your own role")
        with connect(self.org_id) as conn:
            target = conn.execute(
                "select role from memberships where user_id = %s and org_id = %s",
                (user_id, self.org_id),
            ).fetchone()
            if target is None:
                raise NotFound(f"member not found: {user_id}")
            if target["role"] == "owner" and role != "owner" and self._owner_count(conn) == 1:
                raise Forbidden("the org must keep at least one owner")
            conn.execute(
                "update memberships set role = %s where user_id = %s and org_id = %s",
                (role, user_id, self.org_id),
            )

    def remove_member(self, user_id: str) -> None:
        """Remove a member from the org. You cannot remove yourself, and you
        cannot remove the last owner."""
        self.require_role("admin")
        if user_id == self.ctx.user_id:
            raise Forbidden("you cannot remove yourself")
        with connect(self.org_id) as conn:
            target = conn.execute(
                "select role from memberships where user_id = %s and org_id = %s",
                (user_id, self.org_id),
            ).fetchone()
            if target is None:
                raise NotFound(f"member not found: {user_id}")
            if target["role"] == "owner" and self._owner_count(conn) == 1:
                raise Forbidden("cannot remove the last owner")
            conn.execute(
                "delete from memberships where user_id = %s and org_id = %s",
                (user_id, self.org_id),
            )

    # --- documents --------------------------------------------------------

    def sync_source(self, source: str, items: list[dict[str, Any]]) -> dict[str, int]:
        """Reconcile one source's documents for this org, enforcing the storage
        caps first. Admin only.

        The caps live here rather than in the route because they are a property
        of the tenant, and because this is the layer a new caller reaches for.
        Conservative on updates: an edit counts toward the incoming total, which
        can only over-protect.
        """
        self.require_role("admin")
        incoming_bytes = sum(len(i["content"].encode("utf-8")) for i in items)

        def check_caps(conn: psycopg.Connection[DictRow]) -> None:
            """Runs inside the transaction that does the writing.

            Reading usage and then writing in a separate transaction let two
            concurrent uploads each see room that only one of them could have,
            and both pass a cap neither should have. Locking the org row holds
            the answer still until the documents land. Serializing on the tenant
            is cheap here: uploads are rare and already slow.
            """
            conn.execute("select 1 from orgs where id = %s for update", (self.org_id,))
            usage = require_row(conn.execute(
                "select count(*) as docs,"
                " coalesce(sum(octet_length(content)), 0) as bytes"
                " from documents where org_id = %s and status <> 'deleted'",
                (self.org_id,),
            ).fetchone())
            if int(usage["bytes"]) + incoming_bytes > settings.org_storage_bytes_cap:
                raise QuotaExceeded("org storage cap exceeded")
            if int(usage["docs"]) + len(items) > settings.org_doc_cap:
                raise QuotaExceeded("org document cap exceeded")

        return ingest.sync_documents(self.org_id, source, items, precheck=check_caps)

    def count_documents(self) -> int:
        """Total documents in the org, for the X-Total-Count header. A separate
        query rather than a window function over the page, because the page is
        capped and the client needs the count of everything, not of the page."""
        with connect(self.org_id) as conn:
            return require_row(conn.execute(
                "select count(*) as n from documents where org_id = %s",
                (self.org_id,),
            ).fetchone())["n"]

    def list_documents(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        # Open to every member on purpose: seeing which documents the org holds
        # is not the same as being able to read them, and the Sources tab is for
        # everyone. Retrieval is what enforces the ACL, per document.
        with connect(self.org_id) as conn:
            return conn.execute(
                "select d.id, d.path, d.source, d.status, d.content_hash,"
                " d.pii_types, d.acl, d.updated_at,"
                " (select count(*) from chunks c where c.document_id = d.id)"
                " as chunk_count"
                " from documents d where d.org_id = %s order by d.path"
                " limit %s offset %s",
                (self.org_id, limit, offset),
            ).fetchall()

    def delete_document(self, document_id: str) -> None:
        """Delete a document and everything derived from it. Admin only. Chunks
        cascade via the foreign key; the ACL lives on the document row, so it
        goes too."""
        self.require_role("admin")
        with connect(self.org_id) as conn:
            row = conn.execute(
                "delete from documents where id = %s and org_id = %s returning id",
                (document_id, self.org_id),
            ).fetchone()
        if row is None:
            raise NotFound(f"document not found: {document_id}")

    def update_document_acl(self, document_id: str, acl: list[str]) -> None:
        self.require_role("admin")
        with connect(self.org_id) as conn:
            row = conn.execute(
                "update documents set acl = %s, updated_at = now()"
                " where id = %s and org_id = %s returning id",
                (Json(acl), document_id, self.org_id),
            ).fetchone()
            if row is None:
                raise NotFound(f"document not found: {document_id}")
            # Chunks carry a denormalized copy so vector search can filter and
            # order on one relation; both writes share this transaction so an
            # ACL change can never be half applied.
            conn.execute(
                "update chunks set acl = %s where document_id = %s and org_id = %s",
                (Json(acl), document_id, self.org_id),
            )

    def export(self) -> dict[str, Any]:
        """A portable snapshot of the org's members and documents (metadata, not
        raw content). Admin only. Backs the tenant data-export requirement."""
        self.require_role("admin")
        return {
            "members": self._sweep(self.list_members),
            "documents": self._sweep(self.list_documents),
        }

    # Rows per round trip when sweeping a listing for the export. Not the API's
    # page cap, which bounds what a client may ask for; this is an internal loop,
    # so the number only trades round trips against peak memory.
    _SWEEP_PAGE = 500

    def _sweep(
        self, fetch: Callable[[int, int], list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        """Collect every row of a paginated listing.

        export() used to call the list methods with no arguments and inherit
        whatever their limit defaulted to. That was fine until pagination landed
        and the default became 100, at which point a tenant's export silently
        stopped at 100 members and 100 documents: well-formed JSON, quietly
        incomplete, which is the worst way for a data-export to fail. Paging
        explicitly here means the listing defaults can move again without taking
        the export with them.
        """
        rows: list[dict[str, Any]] = []
        while True:
            page = fetch(self._SWEEP_PAGE, len(rows))
            rows += page
            if len(page) < self._SWEEP_PAGE:
                return rows

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

    def retrieval_stats(self) -> dict[str, int]:
        """How many ingested chunks the org has, and how many of them this caller
        is allowed to see. The gap is the ACL filter made visible (used in the
        retriever trace span). Two cheap counts, only computed when tracing."""
        principals = self.principals()
        with connect(self.org_id) as conn:
            org_chunks = require_row(conn.execute(
                "select count(*) as n from chunks c join documents d on d.id = c.document_id"
                " where c.org_id = %s and d.status = 'ingested'",
                (self.org_id,),
            ).fetchone())["n"]
            allowed = require_row(conn.execute(
                "select count(*) as n from chunks c join documents d on d.id = c.document_id"
                " where c.org_id = %s and d.status = 'ingested' and c.acl ?| %s",
                (self.org_id, principals),
            ).fetchone())["n"]
        return {"org_chunks": int(org_chunks), "allowed_chunks": int(allowed)}

    def search(self, query_embedding: list[float], k: int = 5) -> list[dict[str, Any]]:
        """Nearest chunks the caller is allowed to see. The ACL filter is part of
        the candidate fetch (`d.acl ?| principals`), so a forbidden chunk is never
        ranked, never scored, and cannot leak through a missed post-filter. The
        org_id filter sits on top as the tenant boundary.
        """
        principals = self.principals()
        vec = Vector(query_embedding)  # binds as `vector`, not double precision[]
        with connect(self.org_id) as conn:
            # The ACL predicate reads c.acl, not d.acl. Filtering on the joined
            # documents table forces the planner to abandon the HNSW index and
            # sort the whole corpus; keeping the filter on the same relation as
            # the vector keeps the index in play. See migration 0010.
            return conn.execute(
                "select c.document_id, c.ordinal, c.text, d.path,"
                " (c.embedding <=> %s) as distance"
                " from chunks c join documents d on d.id = c.document_id"
                " where c.org_id = %s and d.status = 'ingested'"
                " and c.acl ?| %s"
                " order by c.embedding <=> %s limit %s",
                (vec, self.org_id, principals, vec, k),
            ).fetchall()

    # --- answers and feedback --------------------------------------------

    def record_answer(self, question: str, provider: str, refused: bool) -> str:
        """Store a question and what answered it.

        The question text is stored as asked, unlike audit-log detail, which is
        PII-redacted before it is written. The asymmetry is deliberate: an audit
        entry is metadata about an action, where a stray email address is
        incidental and redacting it costs nothing, while a question *is* the
        content — redacting it would leave `top_queries` showing
        "[REDACTED-EMAIL]" and make an answer impossible to trace back to what
        was asked. Anyone who can read these is already an admin of the org the
        asker belongs to. It is worth knowing that this is where user-typed text
        accumulates in the clear.
        """
        with connect(self.org_id) as conn:
            row = require_row(conn.execute(
                "insert into answers(org_id, user_id, question, provider, refused)"
                " values (%s, %s, %s, %s, %s) returning id",
                (self.org_id, self.ctx.user_id, question, provider, refused),
            ).fetchone())
        return str(row["id"])

    def finalize_answer(
        self, answer_id: str, input_tokens: int, output_tokens: int, cost_usd: float,
        estimated: bool = False,
    ) -> None:
        """Record what an answer consumed. `estimated` marks usage inferred from
        what was streamed rather than reported by the provider, which is what a
        client disconnect leaves behind (see assistant.answer_stream)."""
        with connect(self.org_id) as conn:
            conn.execute(
                "update answers set input_tokens = %s, output_tokens = %s,"
                " cost_usd = %s, usage_estimated = %s where id = %s and org_id = %s",
                (input_tokens, output_tokens, cost_usd, estimated, answer_id,
                 self.org_id),
            )
            # Same transaction as the per-org ledger, so the two can never
            # disagree about whether an answer was paid for.
            conn.execute(
                "insert into platform_spend(day, cost_usd) values (current_date, %s)"
                " on conflict (day) do update"
                " set cost_usd = platform_spend.cost_usd + excluded.cost_usd",
                (cost_usd,),
            )

    def mark_blocked(self, answer_id: str) -> None:
        with connect(self.org_id) as conn:
            conn.execute(
                "update answers set blocked = true where id = %s and org_id = %s",
                (answer_id, self.org_id),
            )

    def spend_last_24h(self) -> float:
        with connect(self.org_id) as conn:
            row = require_row(conn.execute(
                "select coalesce(sum(cost_usd), 0) as spend from answers"
                " where org_id = %s and created_at > now() - interval '24 hours'",
                (self.org_id,),
            ).fetchone())
        return float(row["spend"])

    def platform_spend_today(self) -> float:
        """Today's spend across every tenant. Not org-scoped on purpose: it is
        the ceiling on the whole deployment's bill, which per-org caps cannot
        provide while signup is open (see migration 0013)."""
        with connect(self.org_id) as conn:
            row = conn.execute(
                "select cost_usd from platform_spend where day = current_date"
            ).fetchone()
        return float(row["cost_usd"]) if row else 0.0

    def questions_this_month(self) -> int:
        with connect(self.org_id) as conn:
            row = require_row(conn.execute(
                "select count(*) as n from answers where org_id = %s"
                " and created_at >= date_trunc('month', now())",
                (self.org_id,),
            ).fetchone())
        return int(row["n"])

    def storage_usage(self) -> dict[str, int]:
        """Live document count and total content bytes for this org (excluding
        deleted documents). Used to enforce ingest caps."""
        with connect(self.org_id) as conn:
            row = require_row(conn.execute(
                "select count(*) as docs,"
                " coalesce(sum(octet_length(content)), 0) as bytes"
                " from documents where org_id = %s and status <> 'deleted'",
                (self.org_id,),
            ).fetchone())
        return {"docs": int(row["docs"]), "bytes": int(row["bytes"])}

    def count_audit(self) -> int:
        self.require_role("admin")
        with connect(self.org_id) as conn:
            return require_row(conn.execute(
                "select count(*) as n from audit_log where org_id = %s",
                (self.org_id,),
            ).fetchone())["n"]

    def list_audit(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Recent audit events for this org. Admin only."""
        self.require_role("admin")
        with connect(self.org_id) as conn:
            return conn.execute(
                "select a.action, a.detail, a.created_at, u.email as actor"
                " from audit_log a left join users u on u.id = a.actor_user_id"
                " where a.org_id = %s order by a.created_at desc"
                " limit %s offset %s",
                (self.org_id, limit, offset),
            ).fetchall()

    def top_queries(self, limit: int = 5) -> list[dict[str, Any]]:
        with connect(self.org_id) as conn:
            return conn.execute(
                "select question, count(*) as count from answers"
                " where org_id = %s and created_at >= date_trunc('month', now())"
                " group by question order by count desc, question limit %s",
                (self.org_id, limit),
            ).fetchall()

    def usage_summary(self) -> dict[str, Any]:
        """Everything the usage dashboard needs: volume and cost against their
        caps, storage against its cap, and the month's top questions. Admin
        only."""
        self.require_role("admin")
        storage = self.storage_usage()
        return {
            "questions": {
                "used": self.questions_this_month(),
                "cap": settings.monthly_question_cap,
            },
            "spend": {
                "used_usd": round(self.spend_last_24h(), 6),
                "budget_usd": settings.daily_budget_usd,
            },
            "storage": {
                "docs": storage["docs"],
                "doc_cap": settings.org_doc_cap,
                "bytes": storage["bytes"],
                "byte_cap": settings.org_storage_bytes_cap,
            },
            "top_queries": self.top_queries(),
        }

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

    def count_members(self) -> int:
        with connect(self.org_id) as conn:
            return require_row(conn.execute(
                "select count(*) as n from memberships where org_id = %s",
                (self.org_id,),
            ).fetchone())["n"]

    def list_members(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with connect(self.org_id) as conn:
            return conn.execute(
                "select u.id, u.email, m.role, m.created_at"
                " from memberships m join users u on u.id = m.user_id"
                " where m.org_id = %s order by u.email limit %s offset %s",
                (self.org_id, limit, offset),
            ).fetchall()
