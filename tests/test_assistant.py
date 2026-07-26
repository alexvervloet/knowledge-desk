"""Phase 4: the assistant. A grounded answer streams with access-scoped sources;
when nothing the caller may see matches, it refuses instead of answering from the
model. Feedback attaches to a recorded answer, one per user, org-scoped.
"""

import json

import pytest
from fastapi.testclient import TestClient

from knowledge_desk import ingest
from knowledge_desk.main import app
from knowledge_desk.providers import MOCK_BANNER

client = TestClient(app)

pytestmark = pytest.mark.usefixtures("clean_db")

PW = "pw-supersecret"
SECRET = "The vault combination is seven-lion-north."


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
    resp = client.post(
        "/members", headers=auth(owner),
        json={"email": email, "password": PW, "role": "member"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["user_id"]


def login(email: str, slug: str) -> str:
    return client.post(
        "/auth/login", json={"email": email, "password": PW, "org_slug": slug}
    ).json()["token"]


def upload(token: str, docs: list[dict]) -> None:
    assert client.post("/sources/folder", headers=auth(token), json={"documents": docs}).status_code == 202
    ingest.run_pending()


def ask(token: str, question: str, k: int | None = None) -> list[dict]:
    body = {"question": question}
    if k is not None:
        body["k"] = k
    resp = client.post("/ask", headers=auth(token), json=body)
    assert resp.status_code == 200, resp.text
    events = []
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def _tokens(events: list[dict]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "token")


def _by_type(events: list[dict], t: str) -> list[dict]:
    return [e for e in events if e["type"] == t]


# --- grounded answer ------------------------------------------------------


def test_grounded_answer_streams_meta_sources_tokens_done():
    token = signup("acme", "o@acme.test")
    upload(token, [{"path": "vault.txt", "content": SECRET, "acl": ["public-to-org"]}])
    events = ask(token, SECRET)

    meta = _by_type(events, "meta")[0]
    assert meta["provider"] == "mock" and meta["answer_id"]
    sources = _by_type(events, "sources")[0]["sources"]
    assert any(s["path"] == "vault.txt" for s in sources)
    assert MOCK_BANNER in _tokens(events)
    done = _by_type(events, "done")[0]
    assert "input_tokens" in done["usage"] and "output_tokens" in done["usage"]


# --- refusal carries the permission boundary through -----------------------


def test_refuses_when_org_has_no_documents():
    token = signup("acme", "o@acme.test")
    events = ask(token, "what is the vault combination?")
    assert _by_type(events, "sources") == []  # nothing to cite
    assert "don't have anything" in _tokens(events).lower()


def test_refuses_when_context_is_not_permitted():
    owner = signup("acme", "o@acme.test")
    x_id = add_member(owner, "x@acme.test")
    add_member(owner, "y@acme.test")
    upload(owner, [{"path": "secret.txt", "content": SECRET, "acl": [f"user:{x_id}"]}])

    # X can ground an answer on it; Y gets a refusal with no sources.
    x_events = ask(login("x@acme.test", "acme"), SECRET)
    assert any(s["path"] == "secret.txt" for s in _by_type(x_events, "sources")[0]["sources"])

    y_events = ask(login("y@acme.test", "acme"), SECRET)
    assert _by_type(y_events, "sources") == []
    assert "seven-lion-north" not in _tokens(y_events)


# --- validation and recording ---------------------------------------------


def test_empty_and_overlong_questions_are_422():
    token = signup("acme", "o@acme.test")
    assert client.post("/ask", headers=auth(token), json={"question": ""}).status_code == 422
    assert client.post("/ask", headers=auth(token), json={"question": "x" * 501}).status_code == 422


def test_answer_is_recorded_as_refused_when_no_context():
    from knowledge_desk.db import connect

    token = signup("acme", "o@acme.test")
    answer_id = _by_type(ask(token, "anything?"), "meta")[0]["answer_id"]
    org_id = client.get("/me", headers=auth(token)).json()["org_id"]
    with connect(org_id) as conn:  # RLS: reads need the org context set
        row = conn.execute("select refused from answers where id = %s", (answer_id,)).fetchone()
    assert row["refused"] is True


# --- feedback -------------------------------------------------------------


def answer_id_for(token: str, question: str) -> str:
    return _by_type(ask(token, question), "meta")[0]["answer_id"]


def test_feedback_records_once_per_user():
    token = signup("acme", "o@acme.test")
    aid = answer_id_for(token, "hello?")
    assert client.post("/feedback", headers=auth(token), json={"answer_id": aid, "rating": "up"}).status_code == 201
    # A second rating for the same answer by the same user conflicts.
    assert client.post("/feedback", headers=auth(token), json={"answer_id": aid, "rating": "down"}).status_code == 409


def test_feedback_rating_is_validated():
    token = signup("acme", "o@acme.test")
    aid = answer_id_for(token, "hello?")
    assert client.post("/feedback", headers=auth(token), json={"answer_id": aid, "rating": "meh"}).status_code == 422


def test_feedback_on_foreign_answer_is_404():
    a = signup("acme", "o@acme.test")
    b = signup("globex", "o@globex.test")
    b_answer = answer_id_for(b, "hello?")
    # Org A cannot leave feedback on org B's answer.
    resp = client.post("/feedback", headers=auth(a), json={"answer_id": b_answer, "rating": "up"})
    assert resp.status_code == 404
