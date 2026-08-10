"""Phase 1: the multi-tenant spine. The load-bearing test is that org A cannot
reach org B's data through any endpoint, plus role gates and session lifecycle.
"""

import pytest
from fastapi.testclient import TestClient

from knowledge_desk.db import connect
from knowledge_desk.main import app

client = TestClient(app)

pytestmark = pytest.mark.usefixtures("clean_db")


def signup(slug: str, email: str, password: str = "pw-supersecret") -> str:
    resp = client.post(
        "/auth/signup",
        json={"org_slug": slug, "org_name": slug.title(), "email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


PW = "pw-supersecret"


def login(email: str, slug: str) -> str:
    resp = client.post("/auth/login", json={"email": email, "password": PW, "org_slug": slug})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def add_member(owner: str, email: str, role: str = "member") -> str:
    return client.post("/members", headers=auth(owner),
                       json={"email": email, "password": PW, "role": role}).json()["user_id"]


# --- signup / login -------------------------------------------------------


def test_signup_makes_owner_and_me_reflects_it():
    token = signup("acme", "owner@acme.test")
    me = client.get("/me", headers=auth(token)).json()
    assert me["role"] == "owner"
    assert me["email"] == "owner@acme.test"


def test_login_wrong_password_is_401():
    signup("acme", "owner@acme.test")
    resp = client.post("/auth/login", json={"email": "owner@acme.test", "password": "nope-nope-nope"})
    assert resp.status_code == 401


def test_login_unknown_org_is_401():
    signup("acme", "owner@acme.test")
    resp = client.post(
        "/auth/login",
        json={"email": "owner@acme.test", "password": "pw-supersecret", "org_slug": "globex"},
    )
    assert resp.status_code == 401


# --- cross-tenant isolation (the load-bearing test) -----------------------


def test_org_cannot_read_another_orgs_group():
    a = signup("acme", "owner@acme.test")
    b = signup("globex", "owner@globex.test")

    group_id = client.post("/groups", headers=auth(a), json={"name": "engineering"}).json()["id"]

    # Owner of A sees it; owner of B gets a 404, indistinguishable from absent.
    assert client.get(f"/groups/{group_id}", headers=auth(a)).status_code == 200
    assert client.get(f"/groups/{group_id}", headers=auth(b)).status_code == 404


def test_members_list_is_org_scoped():
    a = signup("acme", "owner@acme.test")
    signup("globex", "owner@globex.test")
    emails = {m["email"] for m in client.get("/members", headers=auth(a)).json()}
    assert emails == {"owner@acme.test"}


def test_same_group_name_allowed_in_different_orgs():
    a = signup("acme", "owner@acme.test")
    b = signup("globex", "owner@globex.test")
    assert client.post("/groups", headers=auth(a), json={"name": "eng"}).status_code == 201
    assert client.post("/groups", headers=auth(b), json={"name": "eng"}).status_code == 201


def test_duplicate_group_name_in_same_org_is_409():
    a = signup("acme", "owner@acme.test")
    client.post("/groups", headers=auth(a), json={"name": "eng"})
    assert client.post("/groups", headers=auth(a), json={"name": "eng"}).status_code == 409


# --- role gates -----------------------------------------------------------


def test_member_cannot_create_group_but_can_list():
    owner = signup("acme", "owner@acme.test")
    client.post(
        "/members",
        headers=auth(owner),
        json={"email": "dev@acme.test", "password": "pw-devsecret", "role": "member"},
    )
    member = client.post(
        "/auth/login",
        json={"email": "dev@acme.test", "password": "pw-devsecret", "org_slug": "acme"},
    ).json()["token"]

    assert client.post("/groups", headers=auth(member), json={"name": "x"}).status_code == 403
    assert client.get("/groups", headers=auth(member)).status_code == 200


def test_member_cannot_add_members():
    owner = signup("acme", "owner@acme.test")
    client.post(
        "/members",
        headers=auth(owner),
        json={"email": "dev@acme.test", "password": "pw-devsecret", "role": "member"},
    )
    member = client.post(
        "/auth/login",
        json={"email": "dev@acme.test", "password": "pw-devsecret", "org_slug": "acme"},
    ).json()["token"]
    resp = client.post(
        "/members",
        headers=auth(member),
        json={"email": "x@acme.test", "password": "pw-another1", "role": "member"},
    )
    assert resp.status_code == 403


def test_duplicate_member_is_409():
    owner = signup("acme", "owner@acme.test")
    body = {"email": "dev@acme.test", "password": "pw-devsecret", "role": "member"}
    assert client.post("/members", headers=auth(owner), json=body).status_code == 201
    assert client.post("/members", headers=auth(owner), json=body).status_code == 409


# --- group membership across orgs ----------------------------------------


def test_cannot_add_foreign_user_to_group():
    a = signup("acme", "owner@acme.test")
    signup("globex", "outsider@globex.test")
    group_id = client.post("/groups", headers=auth(a), json={"name": "eng"}).json()["id"]
    # The user exists globally but is not a member of acme.
    resp = client.post(
        f"/groups/{group_id}/members", headers=auth(a), json={"email": "outsider@globex.test"}
    )
    assert resp.status_code == 404


# --- session lifecycle ----------------------------------------------------


def test_missing_and_bad_tokens_are_401():
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers=auth("not-a-real-token")).status_code == 401


def test_expired_session_is_401():
    token = signup("acme", "owner@acme.test")
    assert client.get("/me", headers=auth(token)).status_code == 200
    with connect() as conn:
        conn.execute("update sessions set expires_at = now() - interval '1 hour'")
    assert client.get("/me", headers=auth(token)).status_code == 401


def test_logout_invalidates_session():
    token = signup("acme", "owner@acme.test")
    assert client.post("/auth/logout", headers=auth(token)).status_code == 204
    assert client.get("/me", headers=auth(token)).status_code == 401


def test_revoked_membership_kills_session():
    token = signup("acme", "owner@acme.test")
    with connect() as conn:
        conn.execute("delete from memberships")
    # The session row still exists, but with no membership it no longer resolves.
    assert client.get("/me", headers=auth(token)).status_code == 401


# --- changing your own password --------------------------------------------


def test_change_password_and_log_in_with_the_new_one():
    token = signup("acme", "o@acme.test")
    resp = client.post("/me/password", headers=auth(token),
                       json={"current_password": PW, "new_password": "brand-new-pw-1"})
    assert resp.status_code == 204
    assert client.post("/auth/login",
                       json={"email": "o@acme.test", "password": PW}).status_code == 401
    assert client.post("/auth/login",
                       json={"email": "o@acme.test",
                             "password": "brand-new-pw-1"}).status_code == 200


def test_change_password_requires_the_current_one():
    token = signup("acme", "o@acme.test")
    assert client.post("/me/password", headers=auth(token),
                       json={"current_password": "not-the-password",
                             "new_password": "brand-new-pw-1"}).status_code == 401


def test_changing_password_revokes_other_sessions_but_not_this_one():
    """The usual reason to change a password is that someone else may know it,
    so other sessions must not survive it. The caller's own does, or changing
    it would log you out of the tab you are in."""
    signup("acme", "o@acme.test")
    stale = login("o@acme.test", "acme")
    current = login("o@acme.test", "acme")

    assert client.post("/me/password", headers=auth(current),
                       json={"current_password": PW,
                             "new_password": "brand-new-pw-1"}).status_code == 204
    assert client.get("/me", headers=auth(stale)).status_code == 401
    assert client.get("/me", headers=auth(current)).status_code == 200


def test_an_admin_cannot_change_someone_elses_password():
    """There is no route for it, deliberately: an admin who could reset an
    owner's password could log in as them, which is the escalation the
    role-grant ceiling closed arriving through another door."""
    owner = signup("acme", "o@acme.test")
    uid = add_member(owner, "dev@acme.test")
    for path in (f"/members/{uid}/password", f"/users/{uid}/password"):
        resp = client.post(path, headers=auth(owner),
                           json={"new_password": "brand-new-pw-1"})
        assert resp.status_code == 404, f"{path} should not exist"
