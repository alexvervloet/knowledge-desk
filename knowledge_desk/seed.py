"""Seed two demo tenants with distinct, non-overlapping documents so a reviewer
can log into each and see isolation and access control live: neither org's
questions can retrieve the other's content.

    python -m knowledge_desk.seed

Idempotent: an org that already exists is left alone. Prints the demo logins.
"""

from __future__ import annotations

import sys
from typing import Any

from knowledge_desk import accounts, ingest
from knowledge_desk.db import close_pool, connect

DEMO_PASSWORD = "demo-password-123"

_ORGS: list[dict[str, Any]] = [
    {
        "slug": "acme",
        "name": "Acme Corp",
        "owner": "owner@acme.test",
        "documents": [
            {"path": "handbook.md", "content": "Acme refunds are processed within five business days of the request."},
            {"path": "security.md", "content": "Acme rotates all production API keys every 90 days and stores them in a vault."},
        ],
    },
    {
        "slug": "globex",
        "name": "Globex Inc",
        "owner": "owner@globex.test",
        "documents": [
            {"path": "products.md", "content": "Globex manufactures industrial widgets and ships them worldwide."},
            {"path": "onboarding.md", "content": "Globex new hires complete orientation during their first week."},
        ],
    },
]


def _org_exists(slug: str) -> bool:
    with connect() as conn:
        return conn.execute("select 1 from orgs where slug = %s", (slug,)).fetchone() is not None


def seed() -> None:
    for spec in _ORGS:
        if _org_exists(spec["slug"]):
            print(f"  skip {spec['slug']} (already exists)")
            continue
        ctx = accounts.create_org_with_owner(
            spec["slug"], spec["name"], spec["owner"], DEMO_PASSWORD
        )
        docs = [{**d, "acl": ["public-to-org"]} for d in spec["documents"]]
        ingest.sync_documents(ctx.org_id, "local-folder", docs)
        print(f"  seeded {spec['slug']} with {len(docs)} documents")

    processed = ingest.run_pending()
    print(f"  ingested: {processed}")
    print(f"\ndemo logins (password: {DEMO_PASSWORD}):")
    for spec in _ORGS:
        print(f"  org={spec['slug']:8} email={spec['owner']}")


if __name__ == "__main__":
    try:
        seed()
    finally:
        # The pool's finalizer tries to join its worker threads, which Python
        # 3.14 refuses at interpreter shutdown (PythonFinalizationError). Any
        # short-lived process that borrows a connection has to close the pool
        # itself rather than leave it to garbage collection.
        close_pool()
    sys.exit(0)
