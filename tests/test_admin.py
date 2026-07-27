"""Phase 7 admin surface: member role changes and removal with their guards,
group membership management (including the remove-from-group API deferred from
Phase 3), document ACL editing, and the usage summary.
"""

import pytest
from fastapi.testclient import TestClient

from knowledge_desk import ingest
from knowledge_desk.main import app

client = TestClient(app)

pytestmark = pytest.mark.usefixtures("clean_db")

PW = "pw-supersecret"


def signup(slug: str, email: str) -> str:
    return client.post(
        "/auth/signup",
        json={"org_slug": slug, "org_name": slug.title(), "email": email, "password": PW},
    ).json()["token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def add_member(owner: str, email: str, role: str = "member") -> str:
    return client.post("/members", headers=auth(owner),
                       json={"email": email, "password": PW, "role": role}).json()["user_id"]


def login(email: str, slug: str) -> str:
    return client.post("/auth/login", json={"email": email, "password": PW, "org_slug": slug}).json()["token"]


def me_id(token: str) -> str:
    return client.get("/me", headers=auth(token)).json()["user_id"]


# --- member role and removal ----------------------------------------------


def test_change_member_role():
    owner = signup("acme", "o@acme.test")
    uid = add_member(owner, "dev@acme.test")
    assert client.patch(f"/members/{uid}", headers=auth(owner), json={"role": "admin"}).status_code == 200
    roles = {m["email"]: m["role"] for m in client.get("/members", headers=auth(owner)).json()}
    assert roles["dev@acme.test"] == "admin"


def test_cannot_change_own_role():
    owner = signup("acme", "o@acme.test")
    assert client.patch(f"/members/{me_id(owner)}", headers=auth(owner), json={"role": "member"}).status_code == 403


def test_cannot_demote_last_owner():
    owner = signup("acme", "o@acme.test")
    admin_id = add_member(owner, "adm@acme.test", "admin")
    admin = login("adm@acme.test", "acme")
    # An admin tries to demote the sole owner: refused to keep an owner in the org.
    assert client.patch(f"/members/{me_id(owner)}", headers=auth(admin), json={"role": "member"}).status_code == 403


def test_remove_member():
    owner = signup("acme", "o@acme.test")
    uid = add_member(owner, "dev@acme.test")
    assert client.delete(f"/members/{uid}", headers=auth(owner)).status_code == 204
    assert "dev@acme.test" not in {m["email"] for m in client.get("/members", headers=auth(owner)).json()}


def test_cannot_remove_self_or_last_owner():
    owner = signup("acme", "o@acme.test")
    assert client.delete(f"/members/{me_id(owner)}", headers=auth(owner)).status_code == 403


def test_member_cannot_administer():
    owner = signup("acme", "o@acme.test")
    uid = add_member(owner, "dev@acme.test")
    member = login("dev@acme.test", "acme")
    assert client.patch(f"/members/{uid}", headers=auth(member), json={"role": "admin"}).status_code == 403


# --- group management ------------------------------------------------------


def test_group_member_add_list_remove():
    owner = signup("acme", "o@acme.test")
    add_member(owner, "dev@acme.test")
    gid = client.post("/groups", headers=auth(owner), json={"name": "eng"}).json()["id"]

    client.post(f"/groups/{gid}/members", headers=auth(owner), json={"email": "dev@acme.test"})
    members = client.get(f"/groups/{gid}/members", headers=auth(owner)).json()
    assert {m["email"] for m in members} == {"dev@acme.test"}

    uid = members[0]["id"]
    assert client.delete(f"/groups/{gid}/members/{uid}", headers=auth(owner)).status_code == 204
    assert client.get(f"/groups/{gid}/members", headers=auth(owner)).json() == []


def test_delete_group():
    owner = signup("acme", "o@acme.test")
    gid = client.post("/groups", headers=auth(owner), json={"name": "eng"}).json()["id"]
    assert client.delete(f"/groups/{gid}", headers=auth(owner)).status_code == 204
    assert client.get("/groups", headers=auth(owner)).json() == []


# --- document ACL editing --------------------------------------------------


def test_edit_document_acl_changes_visibility():
    owner = signup("acme", "o@acme.test")
    x_id = add_member(owner, "x@acme.test")
    add_member(owner, "y@acme.test")
    client.post("/sources/folder", headers=auth(owner),
                json={"documents": [{"path": "d.txt", "content": "shared secret text", "acl": ["public-to-org"]}]})
    ingest.run_pending()
    doc_id = client.get("/documents", headers=auth(owner)).json()[0]["id"]

    y = login("y@acme.test", "acme")
    assert client.post("/search", headers=auth(y), json={"query": "shared secret text"}).json()  # visible now

    # Restrict to user X; Y loses access, X keeps it.
    assert client.patch(f"/documents/{doc_id}/acl", headers=auth(owner),
                        json={"acl": [f"user:{x_id}"]}).status_code == 200
    assert client.post("/search", headers=auth(y), json={"query": "shared secret text"}).json() == []
    x = login("x@acme.test", "acme")
    assert client.post("/search", headers=auth(x), json={"query": "shared secret text"}).json()


# --- usage summary ---------------------------------------------------------


def test_usage_summary_shape_and_admin_only():
    owner = signup("acme", "o@acme.test")
    add_member(owner, "dev@acme.test")
    dev = login("dev@acme.test", "acme")
    assert client.get("/usage", headers=auth(dev)).status_code == 403

    body = client.get("/usage", headers=auth(owner)).json()
    assert set(body) == {"questions", "spend", "storage", "top_queries"}
    assert body["questions"]["cap"] >= 1
    assert "budget_usd" in body["spend"]
