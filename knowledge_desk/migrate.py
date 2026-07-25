"""Plain-SQL migration runner. Applies every `migrations/NNNN_*.sql` not yet
recorded in `schema_migrations`, in filename order, each in its own transaction.

    python -m knowledge_desk.migrate         # apply pending
    python -m knowledge_desk.migrate --status # list applied vs pending
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from knowledge_desk.config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _ensure_table(conn: psycopg.Connection) -> None:
    conn.execute(
        "create table if not exists schema_migrations ("
        "  version text primary key,"
        "  applied_at timestamptz not null default now())"
    )
    conn.commit()


def _applied(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("select version from schema_migrations").fetchall()
    return {r[0] for r in rows}


def _files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def apply_pending() -> list[str]:
    """Apply pending migrations; return the versions applied this run."""
    applied_now: list[str] = []
    with psycopg.connect(settings.database_url) as conn:
        _ensure_table(conn)
        done = _applied(conn)
        for path in _files():
            version = path.stem
            if version in done:
                continue
            sql = path.read_text()
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "insert into schema_migrations(version) values (%s)", (version,)
                )
            print(f"  applied {version}")
            applied_now.append(version)
    if not applied_now:
        print("  (no pending migrations)")
    return applied_now


def status() -> None:
    with psycopg.connect(settings.database_url) as conn:
        _ensure_table(conn)
        done = _applied(conn)
    for path in _files():
        mark = "applied" if path.stem in done else "PENDING"
        print(f"  {mark:8} {path.stem}")


def main(argv: list[str]) -> int:
    if "--status" in argv:
        status()
    else:
        print("migrating")
        apply_pending()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
