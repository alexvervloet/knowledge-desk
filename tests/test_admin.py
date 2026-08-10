"""Phase 7 admin surface: member role changes and removal with their guards,
group membership management (including the remove-from-group API deferred from
Phase 3), document ACL editing, and the usage summary.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from knowledge_desk import ingest
from knowledge_desk.errors import Forbidden
from knowledge_desk.main import app
from knowledge_desk.tenancy import AuthContext, TenantScope

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
    add_member(owner, "adm@acme.test", "admin")
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


# --- the role-grant ceiling ------------------------------------------------
#
# Granting a role you do not hold is privilege escalation with an extra step:
# whoever creates the account also chooses its password, so an admin who can mint
# an owner can log in as it and hold every owner power.


def test_admin_cannot_create_an_owner():
    owner = signup("acme", "o@acme.test")
    add_member(owner, "adm@acme.test", "admin")
    admin = login("adm@acme.test", "acme")
    resp = client.post("/members", headers=auth(admin),
                       json={"email": "puppet@acme.test", "password": PW, "role": "owner"})
    assert resp.status_code == 403
    assert "cannot grant role owner" in resp.json()["detail"]
    # And the account does not exist, so it cannot be logged into.
    assert client.post("/auth/login",
                       json={"email": "puppet@acme.test", "password": PW}).status_code == 401


def test_admin_cannot_promote_anyone_to_owner():
    owner = signup("acme", "o@acme.test")
    add_member(owner, "adm@acme.test", "admin")
    uid = add_member(owner, "dev@acme.test")
    admin = login("adm@acme.test", "acme")
    assert client.patch(f"/members/{uid}", headers=auth(admin),
                        json={"role": "owner"}).status_code == 403
    roles = {m["email"]: m["role"] for m in client.get("/members", headers=auth(owner)).json()}
    assert roles["dev@acme.test"] == "member"


def test_admin_can_still_grant_up_to_its_own_rank():
    owner = signup("acme", "o@acme.test")
    add_member(owner, "adm@acme.test", "admin")
    admin = login("adm@acme.test", "acme")
    assert client.post("/members", headers=auth(admin),
                       json={"email": "a@acme.test", "password": PW,
                             "role": "admin"}).status_code == 201
    uid = client.post("/members", headers=auth(admin),
                      json={"email": "b@acme.test", "password": PW,
                            "role": "member"}).json()["user_id"]
    assert client.patch(f"/members/{uid}", headers=auth(admin),
                        json={"role": "admin"}).status_code == 200


def test_owner_can_still_grant_ownership():
    owner = signup("acme", "o@acme.test")
    uid = add_member(owner, "co@acme.test", "owner")
    assert client.patch(f"/members/{uid}", headers=auth(owner),
                        json={"role": "member"}).status_code == 200
    co = add_member(owner, "co2@acme.test")
    assert client.patch(f"/members/{co}", headers=auth(owner),
                        json={"role": "owner"}).status_code == 200


# --- where authorization lives ---------------------------------------------


ADMIN_ONLY_SCOPE_CALLS = [
    ("sync_source", lambda s: s.sync_source("local-folder", [])),
    ("delete_document", lambda s: s.delete_document(str(uuid.uuid4()))),
    ("update_document_acl", lambda s: s.update_document_acl(str(uuid.uuid4()), [])),
    ("export", lambda s: s.export()),
    ("count_audit", lambda s: s.count_audit()),
    ("list_audit", lambda s: s.list_audit()),
    ("usage_summary", lambda s: s.usage_summary()),
    ("create_group", lambda s: s.create_group("g")),
    ("delete_group", lambda s: s.delete_group(str(uuid.uuid4()))),
    ("remove_member", lambda s: s.remove_member(str(uuid.uuid4()))),
]


@pytest.mark.parametrize("name,call", ADMIN_ONLY_SCOPE_CALLS, ids=[n for n, _ in ADMIN_ONLY_SCOPE_CALLS])
def test_admin_only_operations_are_gated_in_the_data_layer(name, call):
    """Role checks used to be split between the route layer and the data layer,
    with no rule saying which lived where, so reading main.py gave a wrong
    picture of the authorization model. They are all in TenantScope now, which
    is the layer a new route cannot bypass — so the gate has to hold when the
    method is called directly, not only through its endpoint."""
    owner = signup("acme", "o@acme.test")
    add_member(owner, "dev@acme.test")
    me = client.get("/me", headers=auth(login("dev@acme.test", "acme"))).json()
    member_scope = TenantScope(AuthContext(me["user_id"], me["org_id"], "member", me["email"]))

    with pytest.raises(Forbidden):
        call(member_scope)


def test_document_listing_is_open_to_members_on_purpose():
    """The one listing with no role gate. Seeing which documents the org holds
    is not the same as being able to read them: retrieval enforces the ACL per
    document, and the Sources tab is for everyone."""
    owner = signup("acme", "o@acme.test")
    client.post("/sources/folder", headers=auth(owner),
                json={"documents": [{"path": "a.txt", "content": "hello",
                                     "acl": ["public-to-org"]}]})
    ingest.run_pending()
    add_member(owner, "dev@acme.test")
    dev = login("dev@acme.test", "acme")
    assert client.get("/documents", headers=auth(dev)).status_code == 200


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


# --- pagination ------------------------------------------------------------


def test_documents_paginate():
    owner = signup("acme", "o@acme.test")
    docs = [{"path": f"doc{i:02d}.txt", "content": f"content {i}", "acl": ["public-to-org"]}
            for i in range(5)]
    client.post("/sources/folder", headers=auth(owner), json={"documents": docs})
    ingest.run_pending()

    page1 = client.get("/documents?limit=2&offset=0", headers=auth(owner)).json()
    page2 = client.get("/documents?limit=2&offset=2", headers=auth(owner)).json()
    assert [d["path"] for d in page1] == ["doc00.txt", "doc01.txt"]
    assert [d["path"] for d in page2] == ["doc02.txt", "doc03.txt"]
    # Pages are disjoint, which is what makes offset paging usable at all.
    assert not {d["id"] for d in page1} & {d["id"] for d in page2}


def test_members_paginate():
    owner = signup("acme", "o@acme.test")
    for i in range(3):
        add_member(owner, f"dev{i}@acme.test")
    first = client.get("/members?limit=2", headers=auth(owner)).json()
    assert len(first) == 2
    rest = client.get("/members?limit=100&offset=2", headers=auth(owner)).json()
    assert len(rest) == 2  # 4 members total: the owner plus three


def test_pagination_reports_the_total_independent_of_the_page():
    """The client cannot build paging controls from a page alone, so the total
    rides along in a header. It must count everything, not the slice."""
    owner = signup("acme", "o@acme.test")
    docs = [{"path": f"doc{i:02d}.txt", "content": f"content {i}", "acl": ["public-to-org"]}
            for i in range(5)]
    client.post("/sources/folder", headers=auth(owner), json={"documents": docs})
    ingest.run_pending()

    page = client.get("/documents?limit=2&offset=0", headers=auth(owner))
    assert len(page.json()) == 2
    assert page.headers["X-Total-Count"] == "5"

    last = client.get("/documents?limit=2&offset=4", headers=auth(owner))
    assert len(last.json()) == 1  # partial final page
    assert last.headers["X-Total-Count"] == "5"

    # Members and audit expose it too, and the count is org-scoped.
    assert client.get("/members", headers=auth(owner)).headers["X-Total-Count"] == "1"
    assert int(client.get("/audit", headers=auth(owner)).headers["X-Total-Count"]) > 0


def test_total_count_is_org_scoped():
    a = signup("acme", "o@acme.test")
    b = signup("globex", "o@globex.test")
    client.post("/sources/folder", headers=auth(a),
                json={"documents": [{"path": "a.txt", "content": "x", "acl": ["public-to-org"]}]})
    ingest.run_pending()
    assert client.get("/documents", headers=auth(a)).headers["X-Total-Count"] == "1"
    assert client.get("/documents", headers=auth(b)).headers["X-Total-Count"] == "0"


def test_pagination_rejects_bad_bounds():
    owner = signup("acme", "o@acme.test")
    assert client.get("/documents?limit=0", headers=auth(owner)).status_code == 422
    assert client.get("/documents?limit=99999", headers=auth(owner)).status_code == 422
    assert client.get("/documents?offset=-1", headers=auth(owner)).status_code == 422


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
