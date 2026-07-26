"""Shared fixtures. Only tests that request `clean_db` touch the database, so
the Phase 0 smoke tests stay hermetic.
"""

import pytest

from knowledge_desk.db import connect
from knowledge_desk.migrate import apply_pending
from knowledge_desk.ratelimit import limiter

_DOMAIN_TABLES = "orgs, users, memberships, groups, group_members, sessions"


@pytest.fixture(scope="session")
def _migrated():
    apply_pending()


@pytest.fixture
def clean_db(_migrated):
    """Truncate all domain tables before the test for a deterministic slate.
    Truncating orgs cascades to every org-scoped table (documents, answers,
    audit_log, ...). The in-memory rate limiter is reset too so per-test
    request counts start fresh."""
    with connect() as conn:
        conn.execute(f"truncate {_DOMAIN_TABLES} cascade")
    limiter.reset()
    yield
