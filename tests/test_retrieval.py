"""Phase 3, the crux: retrieval must return only what the caller is allowed to
see. The load-bearing test seeds a secret one user owns and proves another user
can never retrieve it, for any query. Access is enforced in the candidate fetch,
so these guarantees hold structurally, not by ranking luck.
"""

import pytest
from fastapi.testclient import TestClient

from knowledge_desk import ingest
from knowledge_desk.db import connect
from knowledge_desk.main import app

client = TestClient(app)

pytestmark = pytest.mark.usefixtures("clean_db")

PW = "pw-supersecret"
SECRET = "The launch code is orange-tiger-42."


def signup(slug: str, email: str) -> str:
    resp = client.post(
        "/auth/signup",
        json={"org_slug": slug, "org_name": slug.title(), "email": email, "password": PW},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def add_member(owner: str, email: str, role: str = "member") -> str:
    resp = client.post(
        "/members", headers=auth(owner),
        json={"email": email, "password": PW, "role": role},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["user_id"]


def login(email: str, slug: str) -> str:
    resp = client.post("/auth/login", json={"email": email, "password": PW, "org_slug": slug})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def upload(token: str, docs: list[dict]) -> None:
    assert client.post("/sources/folder", headers=auth(token), json={"documents": docs}).status_code == 202
    ingest.run_pending()


def found(token: str, query: str, k: int = 50) -> set[str]:
    resp = client.post("/search", headers=auth(token), json={"query": query, "k": k})
    assert resp.status_code == 200, resp.text
    return {r["path"] for r in resp.json()}


# --- the load-bearing leak test -------------------------------------------


def test_user_scoped_secret_never_leaks_to_another_user():
    owner = signup("acme", "owner@acme.test")
    x_id = add_member(owner, "x@acme.test")
    add_member(owner, "y@acme.test")
    upload(owner, [{"path": "secret.txt", "content": SECRET, "acl": [f"user:{x_id}"]}])

    x, y = login("x@acme.test", "acme"), login("y@acme.test", "acme")
    # X owns the ACL and can retrieve it; Y cannot, querying the exact content.
    assert "secret.txt" in found(x, SECRET)
    assert "secret.txt" not in found(y, SECRET)


def test_forbidden_doc_absent_for_any_query():
    """The guarantee is structural, so even an unrelated query, or a direct
    'what is the secret' probe, can never surface the forbidden chunk.
    """
    owner = signup("acme", "owner@acme.test")
    x_id = add_member(owner, "x@acme.test")
    add_member(owner, "y@acme.test")
    upload(owner, [{"path": "secret.txt", "content": SECRET, "acl": [f"user:{x_id}"]}])
    y = login("y@acme.test", "acme")
    for probe in (SECRET, "what is the launch code", "orange tiger", "unrelated text"):
        assert "secret.txt" not in found(y, probe)


# --- public and group visibility ------------------------------------------


def test_public_to_org_visible_to_all_members():
    owner = signup("acme", "owner@acme.test")
    add_member(owner, "y@acme.test")
    upload(owner, [{"path": "handbook.txt", "content": "Company handbook for everyone.", "acl": ["public-to-org"]}])
    y = login("y@acme.test", "acme")
    assert "handbook.txt" in found(y, "Company handbook for everyone.")


def test_group_scoped_visible_only_to_group_members():
    owner = signup("acme", "owner@acme.test")
    add_member(owner, "dev@acme.test")
    add_member(owner, "other@acme.test")
    gid = client.post("/groups", headers=auth(owner), json={"name": "eng"}).json()["id"]
    client.post(f"/groups/{gid}/members", headers=auth(owner), json={"email": "dev@acme.test"})
    upload(owner, [{"path": "eng.txt", "content": "Engineering runbook secret.", "acl": [f"group:{gid}"]}])

    dev, other = login("dev@acme.test", "acme"), login("other@acme.test", "acme")
    assert "eng.txt" in found(dev, "Engineering runbook secret.")
    assert "eng.txt" not in found(other, "Engineering runbook secret.")


def test_removing_from_group_revokes_access_immediately():
    owner = signup("acme", "owner@acme.test")
    dev_id = add_member(owner, "dev@acme.test")
    gid = client.post("/groups", headers=auth(owner), json={"name": "eng"}).json()["id"]
    client.post(f"/groups/{gid}/members", headers=auth(owner), json={"email": "dev@acme.test"})
    upload(owner, [{"path": "eng.txt", "content": "Engineering runbook secret.", "acl": [f"group:{gid}"]}])

    dev = login("dev@acme.test", "acme")
    assert "eng.txt" in found(dev, "Engineering runbook secret.")
    # Principals are recomputed per query, so revocation takes effect at once.
    with connect() as conn:
        conn.execute("delete from group_members where group_id = %s and user_id = %s", (gid, dev_id))
    assert "eng.txt" not in found(dev, "Engineering runbook secret.")


# --- tenant and status boundaries -----------------------------------------


def test_cross_org_never_leaks_even_for_identical_content():
    a = signup("acme", "owner@acme.test")
    b = signup("globex", "owner@globex.test")
    upload(a, [{"path": "shared-name.txt", "content": SECRET, "acl": ["public-to-org"]}])
    # Org B uploads nothing; identical query returns nothing from org A.
    assert found(b, SECRET) == set()


def test_deleted_document_not_retrieved():
    owner = signup("acme", "owner@acme.test")
    upload(owner, [{"path": "a.txt", "content": "alpha content here", "acl": ["public-to-org"]}])
    assert "a.txt" in found(owner, "alpha content here")
    # Re-upload without a.txt marks it deleted; its chunks are gone.
    client.post("/sources/folder", headers=auth(owner), json={"documents": []})
    assert "a.txt" not in found(owner, "alpha content here")
