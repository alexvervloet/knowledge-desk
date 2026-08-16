"""Phase 6 governance: PII flagging at ingest, document/tenant deletion and
export, and proof that row-level security blocks a query that lacks org context.
"""

import pytest
from fastapi.testclient import TestClient

from knowledge_desk import ingest
from knowledge_desk.db import connect, require_row
from knowledge_desk.main import app
from knowledge_desk.tenancy import TenantScope

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


def test_export_is_complete_past_the_default_page_size():
    """The export must not inherit the listings' pagination default.

    export() used to call list_documents() bare, so a tenant with more than 100
    documents got a well-formed export containing 100 of them and no indication
    that the rest were missing.
    """
    token = signup("acme", "o@acme.test")
    # Enqueued, not drained: the export lists documents whatever their status,
    # and embedding 120 of them would only make the test slow.
    paths = [f"doc{i:03}.txt" for i in range(120)]
    client.post("/sources/folder", headers=auth(token),
                json={"documents": [{"path": p, "content": p} for p in paths]})

    export = client.get("/org/export", headers=auth(token)).json()
    assert {d["path"] for d in export["documents"]} == set(paths)


def test_export_sweep_pages_to_exhaustion(monkeypatch):
    """The sweep keeps fetching until a short page arrives, including when the
    row count is an exact multiple of the page size (the off-by-one that would
    otherwise drop the final page or loop forever)."""
    monkeypatch.setattr(TenantScope, "_SWEEP_PAGE", 2)
    token = signup("acme", "o@acme.test")
    for i in range(3):  # 4 members total with the owner: an exact multiple of 2
        add_member(token, f"m{i}@acme.test")
    paths = [f"d{i}.txt" for i in range(5)]  # not a multiple of 2
    client.post("/sources/folder", headers=auth(token),
                json={"documents": [{"path": p, "content": p} for p in paths]})

    export = client.get("/org/export", headers=auth(token)).json()
    assert len(export["members"]) == 4
    assert {d["path"] for d in export["documents"]} == set(paths)


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


def test_pooled_connection_does_not_inherit_previous_tenant():
    """The pooling landmine: the tenant GUC must be transaction-scoped. If it
    were session-scoped it would survive the commit, ride the connection back
    into the pool, and hand the next borrower the previous tenant's context.
    Borrowing repeatedly until the same connection is reused proves it does not.
    """
    a = signup("acme", "o@acme.test")
    upload(a, [{"path": "acme-only.txt", "content": "acme confidential data", "acl": ["public-to-org"]}])
    org_a = org_of(a)

    seen_ids = set()
    for _ in range(5):
        # A scoped borrow, exactly as a request would do.
        with connect(org_a) as conn:
            seen_ids.add(id(conn))
            assert require_row(conn.execute("select count(*) as n from documents").fetchone())["n"] == 1
        # An unscoped borrow: whatever connection this gets, it must see nothing.
        with connect() as conn:
            seen_ids.add(id(conn))
            assert require_row(conn.execute("select count(*) as n from documents").fetchone())["n"] == 0

    # The pool really did hand back the same connection object at least once,
    # so the assertions above exercised reuse rather than fresh connections.
    assert len(seen_ids) < 10


def test_rls_blocks_query_without_org_context():
    token = signup("acme", "o@acme.test")
    upload(token, [{"path": "a.txt", "content": "hello world", "acl": ["public-to-org"]}])
    org_id = org_of(token)

    # As the app role with no org GUC set, RLS returns zero rows even though the
    # document exists; with the org context set, it is visible.
    with connect() as conn:
        assert require_row(conn.execute("select count(*) as n from documents").fetchone())["n"] == 0
    with connect(org_id) as conn:
        assert require_row(conn.execute("select count(*) as n from documents").fetchone())["n"] == 1
