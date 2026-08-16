"""Plain-SQL migration runner. Applies every `migrations/NNNN_*.sql` not yet
recorded in `schema_migrations`, in filename order, each in its own transaction.

    python -m knowledge_desk.migrate         # apply pending
    python -m knowledge_desk.migrate --status # list applied vs pending
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import sql

from knowledge_desk.config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def ensure_app_role() -> str | None:
    """Make the database agree with APP_DATABASE_URL about the app role.

    The app connects as a least-privilege role so row-level security applies to
    it (an owner or superuser bypasses RLS). That role has to exist with the
    right password, and the credential belongs in exactly one place: the
    APP_DATABASE_URL secret. Creating it here, from that URL, means a production
    database never inherits the throwaway password migration 0007 falls back to
    when nobody has provisioned the role.

    Idempotent, and a no-op when the app and owner URLs share a user, which is
    how a single-role setup is expressed. Returns the role name, or None.
    """
    app = urlsplit(settings.app_database_url)
    owner = urlsplit(settings.database_url)
    role, password = unquote(app.username or ""), unquote(app.password or "")
    if not role or role == (owner.username or ""):
        return None

    with psycopg.connect(settings.database_url) as conn:
        exists = conn.execute(
            "select 1 from pg_roles where rolname = %s", (role,)
        ).fetchone()
        # Identifiers cannot be parameterized; sql.Identifier quotes them safely.
        # The password is passed as a literal for the same reason.
        ident = sql.Identifier(role)
        if exists:
            conn.execute(sql.SQL("alter role {} login password {}").format(
                ident, sql.Literal(password)))
        else:
            conn.execute(sql.SQL("create role {} login password {}").format(
                ident, sql.Literal(password)))
        conn.commit()
    return role


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
    role = ensure_app_role()
    if role:
        print(f"  app role {role} present with the configured password")
    with psycopg.connect(settings.database_url) as conn:
        _ensure_table(conn)
        done = _applied(conn)
        for path in _files():
            version = path.stem
            if version in done:
                continue
            sql_text = path.read_text()
            with conn.transaction():
                # psycopg types `execute` as LiteralString-only, so that every
                # runtime-assembled query has to justify itself. This one is a
                # versioned file from this repo, not user input.
                #
                # Silenced for pyright rather than cast, because mypy erases
                # LiteralString to str and would then call the cast redundant.
                conn.execute(sql_text)  # pyright: ignore[reportCallIssue, reportArgumentType]
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
