"""Database access. One connection per unit of work in Phase 1; a pool is a
later optimization noted in the plan. Rows come back as dicts.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from knowledge_desk.config import settings


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """A connection that commits on clean exit and rolls back on error."""
    conn = psycopg.connect(settings.database_url, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
