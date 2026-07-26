"""Database access. One connection per unit of work in Phase 1; a pool is a
later optimization noted in the plan. Rows come back as dicts.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from knowledge_desk.config import settings


@contextmanager
def connect(org_id: str | None = None) -> Iterator[psycopg.Connection]:
    """A connection that commits on clean exit and rolls back on error. The
    pgvector adapter is registered so Python lists bind to `vector` columns.

    When `org_id` is given, it is set as the `app.current_org` GUC, which the
    row-level-security policies on org-scoped tables key on. With no org_id
    those policies see NULL and return no rows, so a query that forgets its
    tenant filter yields nothing rather than leaking across tenants.
    """
    conn = psycopg.connect(settings.app_database_url, row_factory=dict_row)
    register_vector(conn)
    if org_id is not None:
        conn.execute("select set_config('app.current_org', %s, false)", (org_id,))
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
