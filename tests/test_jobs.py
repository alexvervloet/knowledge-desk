"""The durable job queue: idempotent enqueue, single-claim, and the
retry-then-dead-letter path that makes at-least-once delivery safe.
"""

import pytest

from knowledge_desk import accounts, jobs
from knowledge_desk.config import settings
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


def _age_claim(seconds: int) -> None:
    """Pretend every running job was claimed `seconds` ago."""
    with connect() as conn:
        conn.execute(
            "update jobs set updated_at = now() - make_interval(secs => %s)"
            " where status = 'running'",
            (seconds,),
        )


def test_stale_running_job_is_reclaimed():
    # The claiming process died before marking it: the machine stopped mid-job.
    org = _org()
    jobs.enqueue(org, "noop", {}, "k")
    _claim()
    _age_claim(settings.job_stale_after_seconds + 1)
    again = _claim()
    assert again["attempts"] == 2  # the lost run still counts


def test_running_job_is_not_reclaimed_before_stale():
    org = _org()
    jobs.enqueue(org, "noop", {}, "k")
    _claim()
    _age_claim(settings.job_stale_after_seconds - 60)
    assert jobs.claim_one() is None


def test_seconds_until_due():
    assert jobs.seconds_until_due() is None  # empty queue: nothing will ever be due

    org = _org()
    jobs.enqueue(org, "noop", {}, "k")
    assert jobs.seconds_until_due() == 0  # due now

    jobs.mark_failed(_claim()["id"], "boom", backoff_seconds=30)
    wait = jobs.seconds_until_due()
    assert wait is not None and 25 < wait <= 30  # waiting on the retry

    with connect() as conn:
        conn.execute("update jobs set status = 'succeeded'")
    assert jobs.seconds_until_due() is None  # finished work is never due


def test_seconds_until_due_counts_a_running_job_going_stale():
    org = _org()
    jobs.enqueue(org, "noop", {}, "k")
    _claim()
    wait = jobs.seconds_until_due()
    stale = settings.job_stale_after_seconds
    assert wait is not None and stale - 5 < wait <= stale
