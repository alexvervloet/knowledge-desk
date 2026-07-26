"""Phase 5: operational controls. Per-org budget and question caps block the
model call with a loud frame; a per-user rate limit returns 429; ingest respects
storage caps; every answer records its usage; and key actions land in the audit
log an admin can read.
"""

import json

import pytest
from fastapi.testclient import TestClient

from knowledge_desk import ingest
from knowledge_desk.config import settings
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


def login(email: str, slug: str) -> str:
    return client.post("/auth/login", json={"email": email, "password": PW, "org_slug": slug}).json()["token"]


def add_member(owner: str, email: str) -> str:
    return client.post("/members", headers=auth(owner),
                       json={"email": email, "password": PW, "role": "member"}).json()["user_id"]


def upload(token: str, docs: list[dict]):
    resp = client.post("/sources/folder", headers=auth(token), json={"documents": docs})
    if resp.status_code == 202:
        ingest.run_pending()
    return resp


def ask_events(token: str, question: str) -> list[dict]:
    resp = client.post("/ask", headers=auth(token), json={"question": question})
    assert resp.status_code == 200, resp.text
    return [json.loads(l[6:]) for l in resp.text.splitlines() if l.startswith("data: ")]


def types(events: list[dict]) -> set[str]:
    return {e["type"] for e in events}


# --- budget and question caps ---------------------------------------------


def test_over_budget_blocks_the_model_call(monkeypatch):
    monkeypatch.setattr(settings, "daily_budget_usd", 0.0)  # everything is over budget
    token = signup("acme", "o@acme.test")
    events = ask_events(token, "anything?")
    error = next(e for e in events if e["type"] == "error")
    assert "[LIMIT]" in error["message"] and "budget" in error["message"]
    assert "sources" not in types(events)  # never retrieved, never answered


def test_monthly_question_cap_blocks_after_limit(monkeypatch):
    monkeypatch.setattr(settings, "monthly_question_cap", 1)
    token = signup("acme", "o@acme.test")
    first = ask_events(token, "first?")
    assert "error" not in types(first)  # under the cap
    second = ask_events(token, "second?")
    error = next(e for e in second if e["type"] == "error")
    assert "[LIMIT]" in error["message"] and "monthly" in error["message"]


def org_of(token: str) -> str:
    return client.get("/me", headers=auth(token)).json()["org_id"]


def test_blocked_question_is_recorded(monkeypatch):
    monkeypatch.setattr(settings, "daily_budget_usd", 0.0)
    token = signup("acme", "o@acme.test")
    aid = next(e for e in ask_events(token, "q?") if e["type"] == "meta")["answer_id"]
    with connect(org_of(token)) as conn:  # RLS: reads need the org context set
        row = conn.execute("select blocked from answers where id = %s", (aid,)).fetchone()
    assert row["blocked"] is True


# --- per-user rate limit ---------------------------------------------------


def test_rate_limit_returns_429(monkeypatch):
    monkeypatch.setattr(settings, "rate_burst", 2)
    monkeypatch.setattr(settings, "rate_per_min", 1)  # negligible refill during the test
    token = signup("acme", "o@acme.test")
    codes = [client.post("/ask", headers=auth(token), json={"question": "hi"}).status_code
             for _ in range(3)]
    assert codes == [200, 200, 429]


def test_rate_limit_is_per_user(monkeypatch):
    monkeypatch.setattr(settings, "rate_burst", 1)
    monkeypatch.setattr(settings, "rate_per_min", 1)
    owner = signup("acme", "o@acme.test")
    add_member(owner, "dev@acme.test")
    dev = login("dev@acme.test", "acme")
    assert client.post("/ask", headers=auth(owner), json={"question": "hi"}).status_code == 200
    # The owner is now rate-limited, but the member has an independent bucket.
    assert client.post("/ask", headers=auth(owner), json={"question": "hi"}).status_code == 429
    assert client.post("/ask", headers=auth(dev), json={"question": "hi"}).status_code == 200


# --- ingest storage cap ----------------------------------------------------


def test_storage_cap_rejects_oversized_upload(monkeypatch):
    monkeypatch.setattr(settings, "org_storage_bytes_cap", 100)
    token = signup("acme", "o@acme.test")
    resp = upload(token, [{"path": "big.txt", "content": "x" * 500}])
    assert resp.status_code == 413 and "storage" in resp.json()["detail"]


def test_document_cap_rejects_too_many(monkeypatch):
    monkeypatch.setattr(settings, "org_doc_cap", 1)
    token = signup("acme", "o@acme.test")
    resp = upload(token, [{"path": "a.txt", "content": "a"}, {"path": "b.txt", "content": "b"}])
    assert resp.status_code == 413 and "document" in resp.json()["detail"]


# --- cost ledger -----------------------------------------------------------


def test_answer_records_usage():
    token = signup("acme", "o@acme.test")
    upload(token, [{"path": "doc.txt", "content": "the sky is blue today", "acl": ["public-to-org"]}])
    aid = next(e for e in ask_events(token, "the sky is blue today") if e["type"] == "meta")["answer_id"]
    with connect(org_of(token)) as conn:
        row = conn.execute(
            "select output_tokens, refused, blocked from answers where id = %s", (aid,)
        ).fetchone()
    assert row["output_tokens"] > 0 and not row["refused"] and not row["blocked"]


# --- audit log -------------------------------------------------------------


def test_audit_records_key_actions():
    owner = signup("acme", "o@acme.test")
    add_member(owner, "dev@acme.test")
    upload(owner, [{"path": "a.txt", "content": "hello world", "acl": ["public-to-org"]}])
    ask_events(owner, "hello world")

    entries = client.get("/audit", headers=auth(owner)).json()
    actions = {e["action"] for e in entries}
    assert {"org.created", "member.added", "source.synced", "question.asked"} <= actions


def test_audit_is_admin_only():
    owner = signup("acme", "o@acme.test")
    add_member(owner, "dev@acme.test")
    dev = login("dev@acme.test", "acme")
    assert client.get("/audit", headers=auth(dev)).status_code == 403
