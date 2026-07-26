"""Phase 0 smoke: the health probe and the collection error shapes.

These run with no database and no keys: the skeleton must stand on its own so
CI has a fast, hermetic gate before the compose end-to-end. The ask contract
moved to an authenticated SSE stream in Phase 4 and is covered in
test_assistant.py.
"""

from fastapi.testclient import TestClient

from knowledge_desk.main import app

client = TestClient(app)


def test_healthz_reports_mock_provider():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["provider"] == "mock"


def test_unknown_collection_is_404():
    resp = client.get("/collections/does-not-exist")
    assert resp.status_code == 404
    assert "unknown collection" in resp.json()["detail"]


def test_known_collection_ok():
    resp = client.get("/collections/demo")
    assert resp.status_code == 200
    assert resp.json()["name"] == "demo"


def test_ask_requires_auth():
    resp = client.post("/ask", json={"question": "hello"})
    assert resp.status_code == 401
