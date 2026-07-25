"""Phase 0 smoke: the health probe, the mock ask contract, and the error shapes.

These run with no database and no keys: the skeleton must stand on its own so
CI has a fast, hermetic gate before the compose end-to-end.
"""

from fastapi.testclient import TestClient

from knowledge_desk.main import app
from knowledge_desk.providers import MOCK_BANNER

client = TestClient(app)


def test_healthz_reports_mock_provider():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["provider"] == "mock"


def test_ask_returns_labeled_mock_answer():
    resp = client.post("/ask", json={"question": "what is this?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "mock"
    assert MOCK_BANNER in body["answer"]
    assert "what is this?" in body["answer"]


def test_unknown_collection_is_404():
    resp = client.get("/collections/does-not-exist")
    assert resp.status_code == 404
    assert "unknown collection" in resp.json()["detail"]


def test_known_collection_ok():
    resp = client.get("/collections/demo")
    assert resp.status_code == 200
    assert resp.json()["name"] == "demo"


def test_empty_question_is_422():
    resp = client.post("/ask", json={"question": ""})
    assert resp.status_code == 422


def test_overlong_question_is_422():
    resp = client.post("/ask", json={"question": "x" * 501})
    assert resp.status_code == 422
