"""Shared fixtures. Only tests that request `clean_db` touch the database, so
the Phase 0 smoke tests stay hermetic.
"""

import pytest

from knowledge_desk.db import connect
from knowledge_desk.migrate import apply_pending

_DOMAIN_TABLES = "orgs, users, memberships, groups, group_members, sessions"


@pytest.fixture(scope="session")
def _migrated():
    apply_pending()


@pytest.fixture
def clean_db(_migrated):
    """Truncate all domain tables before the test for a deterministic slate."""
    with connect() as conn:
        conn.execute(f"truncate {_DOMAIN_TABLES} cascade")
    yield
