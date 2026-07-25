#!/usr/bin/env python3
"""Preflight check: is the local environment ready to develop against?

Verifies the database is reachable and the pgvector extension is available, and
reports which provider mode the current environment resolves to. Exits nonzero
on the first hard failure so CI can gate on it.

    python check_setup.py
"""

from __future__ import annotations

import sys

import psycopg

from knowledge_desk.config import settings


def _ok(msg: str) -> None:
    print(f"  ok   {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL {msg}")


def main() -> int:
    print("knowledge-desk preflight")
    failures = 0

    # 1. Database reachable.
    try:
        with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("select version()")
                version = cur.fetchone()[0]
            _ok(f"database reachable ({version.split(',')[0]})")

            # 2. pgvector available (create if missing; needs it for later phases).
            try:
                with conn.cursor() as cur:
                    cur.execute("create extension if not exists vector")
                conn.commit()
                with conn.cursor() as cur:
                    cur.execute("select extversion from pg_extension where extname = 'vector'")
                    row = cur.fetchone()
                if row:
                    _ok(f"pgvector extension present (v{row[0]})")
                else:
                    _fail("pgvector extension missing after create")
                    failures += 1
            except Exception as exc:  # noqa: BLE001
                _fail(f"pgvector not available: {exc}")
                failures += 1
    except Exception as exc:  # noqa: BLE001
        _fail(f"database unreachable at configured DATABASE_URL: {exc}")
        _fail("is docker compose up? (db on host port 5436)")
        failures += 1

    # 3. Provider mode (informational, never a failure by itself).
    if settings.provider == "real":
        _ok("provider: real (keys present)")
    elif settings.provider_strict:
        _fail("provider: mock but PROVIDER_STRICT=1 (set keys or unset strict)")
        failures += 1
    else:
        _ok("provider: mock (loud fallback; no keys needed for dev)")

    print()
    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
