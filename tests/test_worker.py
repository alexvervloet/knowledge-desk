"""The in-process drain: it runs what is due, waits out a retry, and stops when
the queue is empty, which is what lets an idle deployment send no queries.
"""

import pytest

from knowledge_desk import accounts, ingest, jobs, worker
from knowledge_desk.db import connect

pytestmark = pytest.mark.usefixtures("clean_db")


def _org() -> str:
    return accounts.create_org_with_owner("acme", "Acme", "o@acme.test", "pw-supersecret").org_id


def _statuses() -> list[str]:
    with connect() as conn:
        rows = conn.execute("select status from jobs order by created_at").fetchall()
    return [r["status"] for r in rows]


@pytest.fixture
def ran(monkeypatch):
    """A `noop` job kind that records each run."""
    calls: list[dict] = []
    monkeypatch.setitem(ingest.DISPATCH, "noop", lambda org, payload: calls.append(payload))
    return calls


def test_drain_runs_due_jobs_then_returns(ran):
    org = _org()
    jobs.enqueue(org, "noop", {"i": 1}, "k1")
    jobs.enqueue(org, "noop", {"i": 2}, "k2")
    worker.drain()
    assert [c["i"] for c in ran] == [1, 2]
    assert _statuses() == ["succeeded", "succeeded"]


def test_drain_waits_out_a_retry(monkeypatch):
    attempts = []

    def flaky(org, payload):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")

    monkeypatch.setitem(ingest.DISPATCH, "noop", flaky)
    jobs.enqueue(_org(), "noop", {}, "k")
    worker.drain()  # first attempt fails, backoff is 2s, then it succeeds
    assert len(attempts) == 2
    assert _statuses() == ["succeeded"]


def test_kick_drains_in_the_background(ran, monkeypatch):
    monkeypatch.setattr(worker.settings, "drain_in_process", True)
    jobs.enqueue(_org(), "noop", {}, "k")
    worker.kick()
    thread = worker._thread
    assert thread is not None
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert _statuses() == ["succeeded"]
    assert worker._thread is None  # stopped, so the next kick starts a fresh one


def test_kick_does_nothing_when_disabled(ran):
    jobs.enqueue(_org(), "noop", {}, "k")
    worker.kick()
    assert worker._thread is None
    assert _statuses() == ["queued"]
