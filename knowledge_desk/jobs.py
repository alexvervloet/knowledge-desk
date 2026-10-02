"""A durable, Postgres-backed job queue. No Redis: a `jobs` table plus
`SELECT ... FOR UPDATE SKIP LOCKED` gives at-least-once delivery with safe
concurrent workers, which is all ingestion needs. Jobs are idempotent by their
`idempotency_key`, so enqueuing the same unit of work twice is a no-op.

On row-level security: `jobs` carries an `org_id` and is the one such table
without an RLS policy, which is deliberate and worth stating because every other
org-scoped table has one. A worker claims the next due job without knowing whose
it is — that is the whole point of a shared queue — so it cannot set the tenant
GUC before the claim, and a policy here would make the queue unreadable to the
only process that drains it. The isolation that matters happens after the claim:
the job's `org_id` becomes the tenant context for the work itself, so
`process_ingest_document` runs inside the same RLS the API does. A job row holds
a document id, never document content.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Json

from knowledge_desk.config import settings
from knowledge_desk.db import connect


def enqueue(
    org_id: str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    max_attempts: int | None = None,
) -> bool:
    """Enqueue a job. Returns True if a new job was created, False if one with
    this idempotency_key already existed.
    """
    with connect() as conn:
        row = conn.execute(
            "insert into jobs(org_id, kind, payload, idempotency_key, max_attempts)"
            " values (%s, %s, %s, %s, %s)"
            " on conflict (idempotency_key) do nothing returning id",
            (
                org_id,
                kind,
                Json(payload),
                idempotency_key,
                max_attempts or settings.job_max_attempts,
            ),
        ).fetchone()
    return row is not None


def claim_one() -> dict[str, Any] | None:
    """Atomically claim the oldest due job, marking it running. Concurrent
    workers skip each other's locked rows.

    A `running` job whose claim is older than `job_stale_after_seconds` is due
    again. Without that, a process that dies between claim and mark leaves the
    job running forever and its document pending forever, and "at-least-once"
    would be a claim the queue does not keep. The handlers are idempotent, so
    running a job twice is safe. The reclaim still counts as an attempt.
    """
    with connect() as conn:
        return conn.execute(
            "update jobs set status = 'running', attempts = attempts + 1,"
            " updated_at = now()"
            " where id = ("
            "   select id from jobs"
            "   where (status = 'queued' and run_after <= now())"
            "      or (status = 'running'"
            "          and updated_at < now() - make_interval(secs => %s))"
            "   order by created_at for update skip locked limit 1)"
            " returning id, org_id, kind, payload, attempts, max_attempts",
            (settings.job_stale_after_seconds,),
        ).fetchone()


def seconds_until_due() -> float | None:
    """How long until `claim_one` could next hand something out: zero if a job
    is due now, None if no job will ever become due without a new enqueue.

    A queued job is due at its `run_after`; a running one is due when it would
    go stale, which only matters if its process died. This is what lets a
    drain stop instead of polling an empty table.
    """
    with connect() as conn:
        row = conn.execute(
            "select extract(epoch from min(due) - now())::float8 as wait"
            " from ("
            "   select run_after as due from jobs where status = 'queued'"
            "   union all"
            "   select updated_at + make_interval(secs => %s) from jobs"
            "   where status = 'running') pending",
            (settings.job_stale_after_seconds,),
        ).fetchone()
    # Clamped here rather than with `greatest`, which skips nulls and would turn
    # an empty queue into "due now".
    return None if row is None or row["wait"] is None else max(0.0, float(row["wait"]))


def mark_succeeded(job_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "update jobs set status = 'succeeded', last_error = null,"
            " updated_at = now() where id = %s",
            (job_id,),
        )


def _backoff_seconds(attempts: int) -> int:
    # int ** int is typed Any (a negative exponent gives a float), so pin it.
    return int(min(300, 2**attempts))


def mark_failed(job_id: str, error: str, backoff_seconds: int | None = None) -> str:
    """Record a failure. Requeue with a delay if attempts remain, otherwise
    dead-letter. Returns the resulting status ('queued' or 'dead').
    """
    with connect() as conn:
        job = conn.execute(
            "select attempts, max_attempts from jobs where id = %s", (job_id,)
        ).fetchone()
        if job is None:
            return "dead"
        if job["attempts"] >= job["max_attempts"]:
            conn.execute(
                "update jobs set status = 'dead', last_error = %s, updated_at = now()"
                " where id = %s",
                (error, job_id),
            )
            return "dead"
        delay = _backoff_seconds(job["attempts"]) if backoff_seconds is None else backoff_seconds
        conn.execute(
            "update jobs set status = 'queued', last_error = %s,"
            " run_after = now() + make_interval(secs => %s), updated_at = now()"
            " where id = %s",
            (error, delay, job_id),
        )
    return "queued"
