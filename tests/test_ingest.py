"""Ingestion end to end, through the API: upload, worker drain, and the states
that make resync cheap and failures safe (unchanged, edited, deleted, poison).
Plus the tenant guarantees: documents are org-scoped and only admins upload.
"""

import pytest
from fastapi.testclient import TestClient

from knowledge_desk import ingest
from knowledge_desk.db import connect
from knowledge_desk.embeddings import EMBED_FAIL_MARKER, MockEmbedder
from knowledge_desk.main import app

client = TestClient(app)

pytestmark = pytest.mark.usefixtures("clean_db")


def signup(slug: str, email: str) -> str:
    resp = client.post(
        "/auth/signup",
        json={"org_slug": slug, "org_name": slug.title(), "email": email, "password": "pw-supersecret"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def upload(token: str, docs: list[dict]):
    return client.post("/sources/folder", headers=auth(token), json={"documents": docs})


def docs_by_path(token: str) -> dict[str, dict]:
    return {d["path"]: d for d in client.get("/documents", headers=auth(token)).json()}


def drain_until_settled(max_rounds: int = 6) -> None:
    """Advance the queue through backoff without real sleeps: drain, then pull
    any requeued jobs' run_after to now, and repeat until nothing is queued.
    """
    for _ in range(max_rounds):
        ingest.run_pending()
        with connect() as conn:
            remaining = conn.execute(
                "select count(*) as n from jobs where status = 'queued'"
            ).fetchone()["n"]
            if remaining == 0:
                return
            conn.execute("update jobs set run_after = now() where status = 'queued'")


# --- happy path -----------------------------------------------------------


def test_upload_then_drain_ingests_with_chunks():
    token = signup("acme", "o@acme.test")
    resp = upload(token, [
        {"path": "a.txt", "content": "alpha " * 400},
        {"path": "b.txt", "content": "beta " * 400},
    ])
    assert resp.status_code == 202
    assert resp.json() == {"enqueued": 2, "unchanged": 0, "deleted": 0}

    ingest.run_pending()
    docs = docs_by_path(token)
    assert docs["a.txt"]["status"] == "ingested" and docs["a.txt"]["chunk_count"] > 0
    assert docs["b.txt"]["status"] == "ingested" and docs["b.txt"]["chunk_count"] > 0


def test_resync_identical_is_all_unchanged():
    token = signup("acme", "o@acme.test")
    docs = [{"path": "a.txt", "content": "alpha " * 400}]
    upload(token, docs)
    ingest.run_pending()
    assert upload(token, docs).json() == {"enqueued": 0, "unchanged": 1, "deleted": 0}


def test_edit_reembeds_only_changed():
    token = signup("acme", "o@acme.test")
    upload(token, [
        {"path": "a.txt", "content": "alpha " * 400},
        {"path": "b.txt", "content": "beta " * 400},
    ])
    ingest.run_pending()
    # Change only b.txt; a.txt is unchanged and must not be re-enqueued.
    result = upload(token, [
        {"path": "a.txt", "content": "alpha " * 400},
        {"path": "b.txt", "content": "beta EDITED " * 400},
    ]).json()
    assert result == {"enqueued": 1, "unchanged": 1, "deleted": 0}


def test_dropped_file_is_deleted_and_chunks_removed():
    token = signup("acme", "o@acme.test")
    upload(token, [
        {"path": "a.txt", "content": "alpha " * 400},
        {"path": "b.txt", "content": "beta " * 400},
    ])
    ingest.run_pending()
    # Re-upload without a.txt: it should be marked deleted with no chunks.
    result = upload(token, [{"path": "b.txt", "content": "beta " * 400}]).json()
    assert result["deleted"] == 1
    docs = docs_by_path(token)
    assert docs["a.txt"]["status"] == "deleted" and docs["a.txt"]["chunk_count"] == 0


def test_dropped_file_releases_its_bytes():
    """A dropped document stopped counting against the storage quota but kept
    its content in the table, so upload-then-drop in a loop grew the database
    without limit while the usage meter read zero — and text the tenant believed
    they had deleted was still there."""
    token = signup("acme", "o@acme.test")
    body = "x" * 50_000
    upload(token, [{"path": "big.txt", "content": body}])
    ingest.run_pending()

    upload(token, [])  # drop it
    quota = client.get("/usage", headers=auth(token)).json()["storage"]["bytes"]
    with connect() as conn:
        stored = conn.execute(
            "select coalesce(sum(octet_length(content)), 0) as n from documents"
        ).fetchone()["n"]
    assert quota == 0
    assert stored == 0, "bytes that stopped counting must not still be on disk"


def test_resync_after_a_drop_still_reingests():
    """The tombstone has to keep working: resync compares content_hash, which
    survives clearing the text, so re-uploading the same bytes is a real change
    rather than an 'unchanged' no-op that never re-embeds."""
    token = signup("acme", "o@acme.test")
    upload(token, [{"path": "a.txt", "content": "alpha " * 400}])
    ingest.run_pending()
    upload(token, [])
    upload(token, [{"path": "a.txt", "content": "alpha " * 400}])
    ingest.run_pending()

    doc = docs_by_path(token)["a.txt"]
    assert doc["status"] == "ingested" and doc["chunk_count"] > 0


# --- failure isolation ----------------------------------------------------


def test_poison_document_dead_letters_without_wedging_queue():
    token = signup("acme", "o@acme.test")
    upload(token, [
        {"path": "good.txt", "content": "hello " * 400},
        {"path": "poison.txt", "content": "intro " + EMBED_FAIL_MARKER + " tail"},
    ])
    drain_until_settled()

    docs = docs_by_path(token)
    # The good document still ingested; the poison one dead-lettered to 'failed'.
    assert docs["good.txt"]["status"] == "ingested"
    assert docs["poison.txt"]["status"] == "failed"
    with connect() as conn:
        dead = conn.execute(
            "select count(*) as n from jobs where status = 'dead'"
        ).fetchone()["n"]
    assert dead == 1


def test_short_embedding_batch_fails_instead_of_dropping_chunks(monkeypatch):
    """An embedder that returns fewer vectors than it was given texts used to be
    zipped short: the document was marked ingested holding a subset of its
    chunks, with nothing anywhere to say the rest were missing. That is a
    permanent, invisible hole in retrieval, so it must fail loudly instead."""
    class ShortEmbedder:
        def embed_documents(self, texts):
            return MockEmbedder().embed_documents(texts)[:-1]  # one vector short

    token = signup("acme", "o@acme.test")
    upload(token, [{"path": "long.txt", "content": "para " * 2000}])  # several chunks
    monkeypatch.setattr(ingest, "get_embedder", ShortEmbedder)
    drain_until_settled()

    doc = docs_by_path(token)["long.txt"]
    assert doc["status"] == "failed"
    assert doc["chunk_count"] == 0, "a partial document must not be left queryable"


# --- tenant guarantees ----------------------------------------------------


def test_documents_are_org_scoped():
    a = signup("acme", "o@acme.test")
    b = signup("globex", "o@globex.test")
    upload(a, [{"path": "secret.txt", "content": "acme only " * 50}])
    ingest.run_pending()
    assert "secret.txt" in docs_by_path(a)
    assert docs_by_path(b) == {}


def test_member_cannot_upload():
    owner = signup("acme", "o@acme.test")
    client.post(
        "/members",
        headers=auth(owner),
        json={"email": "dev@acme.test", "password": "pw-devsecret", "role": "member"},
    )
    member = client.post(
        "/auth/login",
        json={"email": "dev@acme.test", "password": "pw-devsecret", "org_slug": "acme"},
    ).json()["token"]
    assert upload(member, [{"path": "x.txt", "content": "hi"}]).status_code == 403
