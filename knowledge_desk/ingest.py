"""Ingestion: turn uploaded files into embedded, per-tenant chunks.

Two halves. `sync_documents` runs on the request path: it captures content,
detects what actually changed by hash, marks deletions, and enqueues a job per
changed document. `process_ingest_document` runs in the worker: it chunks,
embeds, and replaces a document's chunks. Splitting them keeps embedding (slow,
and for Voyage a network call) off the request and behind the retrying queue.
"""

from __future__ import annotations

import hashlib
from typing import Any

from psycopg.types.json import Json

from knowledge_desk import jobs, pii
from knowledge_desk.chunking import chunk_text
from knowledge_desk.db import connect, require_row
from knowledge_desk.embeddings import get_embedder

DEFAULT_ACL = ["public-to-org"]


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def sync_documents(
    org_id: str, source: str, items: list[dict[str, Any]]
) -> dict[str, int]:
    """Reconcile an org's documents for one source against `items` (each with
    path, content, and optional acl). Enqueues an ingest job per new/changed
    document, leaves unchanged ones alone, and marks missing ones deleted.
    """
    enqueued = unchanged = 0
    incoming_paths = {item["path"] for item in items}
    to_enqueue: list[tuple[str, str]] = []  # (document_id, content_hash)

    with connect(org_id) as conn:
        existing = {
            r["path"]: r
            for r in conn.execute(
                "select id, path, content_hash, status from documents"
                " where org_id = %s and source = %s",
                (org_id, source),
            ).fetchall()
        }

        for item in items:
            content = item["content"]
            content_hash = _hash(content)
            acl = item.get("acl") or DEFAULT_ACL
            prior = existing.get(item["path"])

            if (
                prior is not None
                and prior["content_hash"] == content_hash
                and prior["status"] == "ingested"
            ):
                unchanged += 1
                continue

            pii_types = pii.detect_types(content)
            doc = require_row(conn.execute(
                "insert into documents(org_id, source, path, content, content_hash, acl, pii_types, status)"
                " values (%s, %s, %s, %s, %s, %s, %s, 'pending')"
                " on conflict (org_id, source, path) do update set"
                " content = excluded.content, content_hash = excluded.content_hash,"
                " acl = excluded.acl, pii_types = excluded.pii_types,"
                " status = 'pending', updated_at = now()"
                " returning id, revision",
                (org_id, source, item["path"], content, content_hash, Json(acl),
                 Json(pii_types)),
            ).fetchone())
            enqueued += 1
            # Enqueue after the row is committed by the surrounding block. The
            # key includes the hash so a re-upload of identical bytes is a no-op,
            # and the revision so that identical bytes uploaded *after* a
            # deletion are not — the chunks the earlier job produced are gone.
            to_enqueue.append((str(doc["id"]), f"{content_hash}:{doc['revision']}"))

        # Mark deletions: previously known paths no longer present.
        deleted = 0
        for path, row in existing.items():
            if path not in incoming_paths and row["status"] != "deleted":
                # Clear the content, not just the status. The row stays as a
                # tombstone so a later resync can tell "gone" from "never seen",
                # but keeping the text meant a document the tenant deleted was
                # still sitting in the table, and storage_usage stops counting a
                # deleted row, so those bytes also vanished from the quota while
                # staying on disk. Resync compares content_hash, which is kept,
                # so the tombstone still does its job.
                conn.execute(
                    "update documents set status = 'deleted', content = '',"
                    " revision = revision + 1, updated_at = now() where id = %s",
                    (row["id"],),
                )
                conn.execute("delete from chunks where document_id = %s", (row["id"],))
                deleted += 1

    # Enqueue outside the document transaction so a job is never queued for a
    # write that rolled back.
    for doc_id, content_hash in to_enqueue:
        jobs.enqueue(
            org_id,
            "ingest_document",
            {"document_id": doc_id},
            idempotency_key=f"ingest:{doc_id}:{content_hash}",
        )

    return {"enqueued": enqueued, "unchanged": unchanged, "deleted": deleted}


def process_ingest_document(org_id: str, payload: dict[str, Any]) -> None:
    """Worker side: chunk and embed one document, replacing its chunks. Raises
    on failure so the queue can retry and eventually dead-letter.
    """
    document_id = payload["document_id"]
    with connect(org_id) as conn:
        doc = conn.execute(
            "select id, content, status, acl from documents"
            " where id = %s and org_id = %s",
            (document_id, org_id),
        ).fetchone()
    if doc is None or doc["status"] == "deleted":
        return  # nothing to do; the document was removed before we ran

    texts = chunk_text(doc["content"])
    embeddings = get_embedder().embed_documents(texts) if texts else []

    with connect(org_id) as conn:
        conn.execute("delete from chunks where document_id = %s", (document_id,))
        # strict: a short embedding list would otherwise truncate silently, and
        # the document would be marked ingested holding a subset of its chunks.
        # That is a permanent, invisible hole in retrieval for that document,
        # and nothing downstream would ever see a reason to retry. Raising sends
        # it back through the queue and eventually dead-letters it visibly.
        for ordinal, (text, embedding) in enumerate(zip(texts, embeddings, strict=True)):
            # acl is denormalized from the parent document so that access-scoped
            # vector search can filter and order on the same relation (see
            # migration 0010). update_document_acl keeps the copies in sync.
            conn.execute(
                "insert into chunks(org_id, document_id, ordinal, text, embedding, acl)"
                " values (%s, %s, %s, %s, %s, %s)",
                (org_id, document_id, ordinal, text, embedding, Json(doc["acl"])),
            )
        conn.execute(
            "update documents set status = 'ingested', updated_at = now() where id = %s",
            (document_id,),
        )


DISPATCH = {"ingest_document": process_ingest_document}


def run_pending(max_jobs: int = 1000) -> dict[str, int]:
    """Drain due jobs until the queue is empty or `max_jobs` is reached. This is
    the worker's inner step, and stands in for a running worker in tests.
    """
    counts = {"processed": 0, "succeeded": 0, "requeued": 0, "dead": 0}
    for _ in range(max_jobs):
        job = jobs.claim_one()
        if job is None:
            break
        counts["processed"] += 1
        handler = DISPATCH.get(job["kind"])
        try:
            if handler is None:
                raise ValueError(f"no handler for job kind: {job['kind']}")
            handler(str(job["org_id"]), job["payload"])
            jobs.mark_succeeded(str(job["id"]))
            counts["succeeded"] += 1
        except Exception as exc:  # noqa: BLE001 - the queue is the safety net
            outcome = jobs.mark_failed(str(job["id"]), repr(exc))
            counts["dead" if outcome == "dead" else "requeued"] += 1
            if outcome == "dead" and job["kind"] == "ingest_document":
                _mark_document_failed(str(job["org_id"]), job["payload"]["document_id"])
    return counts


def _mark_document_failed(org_id: str, document_id: str) -> None:
    with connect(org_id) as conn:
        conn.execute(
            "update documents set status = 'failed', updated_at = now()"
            " where id = %s and org_id = %s",
            (document_id, org_id),
        )
