"""A durable, Postgres-backed job queue. No Redis: a `jobs` table plus
`SELECT ... FOR UPDATE SKIP LOCKED` gives at-least-once delivery with safe
concurrent workers, which is all Phase 2 needs. Jobs are idempotent by their
`idempotency_key`, so enqueuing the same unit of work twice is a no-op.
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
            (org_id, kind, Json(payload), idempotency_key,
             max_attempts or settings.job_max_attempts),
        ).fetchone()
    return row is not None


def claim_one() -> dict[str, Any] | None:
    """Atomically claim the oldest due job, marking it running. Concurrent
    workers skip each other's locked rows.
    """
    with connect() as conn:
        return conn.execute(
            "update jobs set status = 'running', attempts = attempts + 1,"
            " updated_at = now()"
            " where id = ("
            "   select id from jobs"
            "   where status = 'queued' and run_after <= now()"
            "   order by created_at for update skip locked limit 1)"
            " returning id, org_id, kind, payload, attempts, max_attempts",
        ).fetchone()


def mark_succeeded(job_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "update jobs set status = 'succeeded', last_error = null,"
            " updated_at = now() where id = %s",
            (job_id,),
        )


def _backoff_seconds(attempts: int) -> int:
    return min(300, 2 ** attempts)


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
