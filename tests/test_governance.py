"""Phase 6 governance: PII flagging at ingest, document/tenant deletion and
export, and proof that row-level security blocks a query that lacks org context.
"""

import pytest
from fastapi.testclient import TestClient

from knowledge_desk import ingest
from knowledge_desk.db import connect
from knowledge_desk.main import app

client = TestClient(app)

pytestmark = pytest.mark.usefixtures("clean_db")

PW = "pw-supersecret"


def signup(slug: str, email: str) -> str:
    resp = client.post(
        "/auth/signup",
        json={"org_slug": slug, "org_name": slug.title(), "email": email, "password": PW},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def add_member(owner: str, email: str) -> str:
    return client.post("/members", headers=auth(owner),
                       json={"email": email, "password": PW, "role": "member"}).json()["user_id"]


def login(email: str, slug: str) -> str:
    return client.post("/auth/login", json={"email": email, "password": PW, "org_slug": slug}).json()["token"]


def upload(token: str, docs: list[dict]) -> None:
    assert client.post("/sources/folder", headers=auth(token), json={"documents": docs}).status_code == 202
    ingest.run_pending()


def docs_by_path(token: str) -> dict[str, dict]:
    return {d["path"]: d for d in client.get("/documents", headers=auth(token)).json()}


def org_of(token: str) -> str:
    return client.get("/me", headers=auth(token)).json()["org_id"]


# --- PII flagging ----------------------------------------------------------


def test_ingest_flags_pii():
    token = signup("acme", "o@acme.test")
    upload(token, [
        {"path": "hr.txt", "content": "Reach Jane at jane@acme.test or SSN 123-45-6789.",
         "acl": ["public-to-org"]},
        {"path": "clean.txt", "content": "The weather is nice today.", "acl": ["public-to-org"]},
    ])
    docs = docs_by_path(token)
    assert set(docs["hr.txt"]["pii_types"]) == {"email", "ssn"}
    assert docs["clean.txt"]["pii_types"] == []


# --- document deletion -----------------------------------------------------


def test_delete_document_removes_it_and_its_chunks():
    token = signup("acme", "o@acme.test")
    upload(token, [{"path": "a.txt", "content": "alpha content here", "acl": ["public-to-org"]}])
    doc_id = docs_by_path(token)["a.txt"]["id"]

    assert client.delete(f"/documents/{doc_id}", headers=auth(token)).status_code == 204
    assert docs_by_path(token) == {}
    # Its chunks are gone too, so retrieval no longer surfaces it.
    assert client.post("/search", headers=auth(token), json={"query": "alpha content here"}).json() == []


def test_delete_document_requires_admin():
    owner = signup("acme", "o@acme.test")
    add_member(owner, "dev@acme.test")
    upload(owner, [{"path": "a.txt", "content": "alpha", "acl": ["public-to-org"]}])
    doc_id = docs_by_path(owner)["a.txt"]["id"]
    dev = login("dev@acme.test", "acme")
    assert client.delete(f"/documents/{doc_id}", headers=auth(dev)).status_code == 403


def test_delete_unknown_document_is_404():
    token = signup("acme", "o@acme.test")
    import uuid
    assert client.delete(f"/documents/{uuid.uuid4()}", headers=auth(token)).status_code == 404


# --- tenant export and deletion --------------------------------------------


def test_export_returns_members_and_documents():
    token = signup("acme", "o@acme.test")
    upload(token, [{"path": "a.txt", "content": "hello", "acl": ["public-to-org"]}])
    export = client.get("/org/export", headers=auth(token)).json()
    assert any(m["email"] == "o@acme.test" for m in export["members"])
    assert any(d["path"] == "a.txt" for d in export["documents"])


def test_export_requires_admin():
    owner = signup("acme", "o@acme.test")
    add_member(owner, "dev@acme.test")
    dev = login("dev@acme.test", "acme")
    assert client.get("/org/export", headers=auth(dev)).status_code == 403


def test_delete_tenant_is_owner_only_and_cascades():
    owner = signup("acme", "o@acme.test")
    add_member(owner, "dev@acme.test")
    dev = login("dev@acme.test", "acme")
    # A member cannot delete the tenant.
    assert client.delete("/org", headers=auth(dev)).status_code == 403
    # The owner can; afterward the session no longer resolves (cascade removed it).
    assert client.delete("/org", headers=auth(owner)).status_code == 204
    assert client.get("/me", headers=auth(owner)).status_code == 401


# --- row-level security ----------------------------------------------------


def test_rls_blocks_query_without_org_context():
    token = signup("acme", "o@acme.test")
    upload(token, [{"path": "a.txt", "content": "hello world", "acl": ["public-to-org"]}])
    org_id = org_of(token)

    # As the app role with no org GUC set, RLS returns zero rows even though the
    # document exists; with the org context set, it is visible.
    with connect() as conn:
        assert conn.execute("select count(*) as n from documents").fetchone()["n"] == 0
    with connect(org_id) as conn:
        assert conn.execute("select count(*) as n from documents").fetchone()["n"] == 1
