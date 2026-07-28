"""Phase 9 observability: the tracer is inert without keys and never breaks a
request, and retrieval_stats exposes the ACL filter (org total vs what a caller
may see) that the retriever trace span reports.
"""

import pytest
from fastapi.testclient import TestClient

from knowledge_desk import accounts, ingest
from knowledge_desk.main import app
from knowledge_desk.tenancy import TenantScope
from knowledge_desk.tracing import AskTracer

client = TestClient(app)

pytestmark = pytest.mark.usefixtures("clean_db")

PW = "pw-supersecret"


def test_tracer_is_inert_without_keys():
    # Every method must be a safe no-op when Langfuse is not configured.
    t = AskTracer("q", "org", "user", "e@x.test", "mock", "mock")
    assert t.active is False
    t.sources([{"path": "a"}], {"org_chunks": 3, "allowed_chunks": 1})
    t.token("hello ")
    t.done(1, 2, 0.0)
    t.finish()  # no raise


def test_ask_still_streams_with_tracing_path():
    token = client.post("/auth/signup", json={
        "org_slug": "acme", "org_name": "Acme", "email": "o@acme.test", "password": PW,
    }).json()["token"]
    client.post("/sources/folder", headers={"Authorization": f"Bearer {token}"},
                json={"documents": [{"path": "a.txt", "content": "refunds take five business days", "acl": ["public-to-org"]}]})
    ingest.run_pending()
    resp = client.post("/ask", headers={"Authorization": f"Bearer {token}"},
                       json={"question": "refunds take five business days"})
    assert resp.status_code == 200
    assert '"type": "done"' in resp.text and '"type": "sources"' in resp.text


def test_retrieval_stats_exposes_acl_filter():
    owner = accounts.create_org_with_owner("acme", "Acme", "o@acme.test", PW)
    x_id = accounts.add_member(owner.org_id, "x@acme.test", PW, "member")
    accounts.add_member(owner.org_id, "y@acme.test", PW, "member")
    ingest.sync_documents(owner.org_id, "local-folder", [
        {"path": "public.txt", "content": "everyone can read this", "acl": ["public-to-org"]},
        {"path": "secret.txt", "content": "only x can read this", "acl": [f"user:{x_id}"]},
    ])
    ingest.run_pending()

    x = TenantScope(accounts.authenticate("x@acme.test", PW, "acme"))
    y = TenantScope(accounts.authenticate("y@acme.test", PW, "acme"))

    xs, ys = x.retrieval_stats(), y.retrieval_stats()
    assert xs["org_chunks"] == 2  # both documents, one chunk each
    assert xs["allowed_chunks"] == 2  # X sees the public and its own secret
    assert ys["org_chunks"] == 2
    assert ys["allowed_chunks"] == 1  # Y sees only the public document
