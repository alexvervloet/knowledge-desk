"""Shared fixtures. Only tests that request `clean_db` touch the database, so
the Phase 0 smoke tests stay hermetic.
"""

import psycopg
import pytest

from knowledge_desk.config import settings
from knowledge_desk.migrate import apply_pending
from knowledge_desk.ratelimit import auth_limiter, limiter

_DOMAIN_TABLES = "orgs, users, memberships, groups, group_members, sessions"


@pytest.fixture(scope="session")
def _migrated():
    apply_pending()


@pytest.fixture
def clean_db(_migrated):
    """Truncate all domain tables before the test for a deterministic slate.
    Truncating orgs cascades to every org-scoped table (documents, answers,
    audit_log, ...). Runs as the owner (TRUNCATE is not granted to the app role,
    and is not subject to RLS anyway). Both in-memory rate limiters are reset too
    so per-test request counts start fresh; the auth one keys on client address,
    which every test shares, so without this the suite would throttle itself."""
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(f"truncate {_DOMAIN_TABLES} cascade")
        conn.commit()
    limiter.reset()
    auth_limiter.reset()
    yield
