"""The durable job queue: idempotent enqueue, single-claim, and the
retry-then-dead-letter path that makes at-least-once delivery safe.
"""

import pytest

from knowledge_desk import accounts, jobs
from knowledge_desk.db import connect, require_row

pytestmark = pytest.mark.usefixtures("clean_db")


def _org() -> str:
    return accounts.create_org_with_owner("acme", "Acme", "o@acme.test", "pw-supersecret").org_id


def _claim() -> dict:
    """Claim a job the test knows is waiting, and say so if it is not."""
    job = jobs.claim_one()
    assert job is not None, "expected a claimable job"
    return job


def test_enqueue_is_idempotent():
    org = _org()
    assert jobs.enqueue(org, "noop", {"n": 1}, "same-key") is True
    assert jobs.enqueue(org, "noop", {"n": 1}, "same-key") is False


def test_claim_returns_none_when_empty():
    assert jobs.claim_one() is None


def test_claim_marks_running_and_is_not_reclaimed():
    org = _org()
    jobs.enqueue(org, "noop", {}, "k")
    first = jobs.claim_one()
    assert first is not None and first["attempts"] == 1
    assert jobs.claim_one() is None  # already running, not re-handed-out


def test_claim_is_fifo():
    org = _org()
    jobs.enqueue(org, "noop", {"i": 1}, "k1")
    jobs.enqueue(org, "noop", {"i": 2}, "k2")
    assert _claim()["payload"]["i"] == 1
    assert _claim()["payload"]["i"] == 2


def test_retry_then_dead_letter():
    org = _org()
    jobs.enqueue(org, "noop", {}, "k")  # default max_attempts = 3

    assert jobs.mark_failed(_claim()["id"], "boom", backoff_seconds=0) == "queued"
    assert jobs.mark_failed(_claim()["id"], "boom", backoff_seconds=0) == "queued"
    assert jobs.mark_failed(_claim()["id"], "boom", backoff_seconds=0) == "dead"

    assert jobs.claim_one() is None  # dead jobs are never reclaimed
    with connect() as conn:
        row = require_row(conn.execute("select status, last_error from jobs").fetchone())
    assert row["status"] == "dead" and row["last_error"] == "boom"


def test_backoff_delays_reclaim():
    org = _org()
    jobs.enqueue(org, "noop", {}, "k")
    # A real (non-zero) backoff pushes run_after into the future.
    jobs.mark_failed(_claim()["id"], "boom")
    assert jobs.claim_one() is None
