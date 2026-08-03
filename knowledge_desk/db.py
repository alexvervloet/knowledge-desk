"""Database access: a shared connection pool, and the per-request tenant context
that row-level security keys on.

The tenant GUC is set **transaction-scoped** (`set_config(..., true)`), which is
load-bearing once connections are pooled. A session-scoped setting survives the
commit and rides the connection back into the pool, so the next request to borrow
that connection would silently inherit the previous tenant's org context. A
transaction-scoped setting reverts on commit, so a recycled connection always
starts with no tenant and the RLS policies deny by default.

Because the GUC is transaction-scoped, every statement in a `connect(org_id)`
block must run inside the same transaction. psycopg opens one implicitly on the
first statement and holds it until the pool commits at block exit, so this is the
default behavior; committing in the middle of a block would drop the context.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from knowledge_desk.config import settings

_pool: ConnectionPool[psycopg.Connection[DictRow]] | None = None


def _configure(conn: psycopg.Connection[DictRow]) -> None:
    """Run once per physical connection, not per checkout."""
    register_vector(conn)
    # Iterative scan makes the HNSW index safe under our ACL filter. Without it
    # the index returns k candidates, the permission filter removes most of them,
    # and the caller silently gets fewer results than they asked for. Relaxed
    # order lets pgvector re-probe until enough rows survive the filter.
    #
    # Session-scoped on purpose, unlike the tenant GUC: this setting is identical
    # for every tenant, so a pooled connection carrying it between requests is
    # correct rather than a leak.
    conn.execute("set hnsw.iterative_scan = relaxed_order")
    conn.commit()


def get_pool() -> ConnectionPool[psycopg.Connection[DictRow]]:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.app_database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            kwargs={"row_factory": dict_row},
            configure=_configure,
            check=ConnectionPool.check_connection,  # discard connections killed server-side
            open=True,
        )
    return _pool


def require_row(row: DictRow | None) -> DictRow:
    """Unwrap a query that is guaranteed to produce a row.

    An aggregate, or an INSERT with RETURNING, always yields exactly one row, so
    None means the query or the schema changed underneath us. Failing loudly here
    beats a TypeError three frames away, and it lets the type checker see that
    the caller is not indexing an Optional.
    """
    if row is None:
        raise RuntimeError("query returned no row where one was guaranteed")
    return row


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connect(org_id: str | None = None) -> Iterator[psycopg.Connection[DictRow]]:
    """Borrow a pooled connection. Commits on clean exit, rolls back on error.

    When `org_id` is given it is set as the transaction-scoped `app.current_org`
    GUC that the RLS policies read. With no org_id the policies see an empty
    setting and return no rows, so a query that forgets its tenant filter yields
    nothing rather than leaking across tenants.
    """
    with get_pool().connection() as conn:
        if org_id is not None:
            conn.execute("select set_config('app.current_org', %s, true)", (org_id,))
        yield conn
