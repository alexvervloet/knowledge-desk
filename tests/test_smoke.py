"""Smoke tests: the health probe, and that the assistant is behind auth.

These run with no database and no keys, so CI has a fast, hermetic gate before
the suites that need Postgres. The ask contract itself is an authenticated SSE
stream, covered in test_assistant.py.
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


def test_ask_requires_auth():
    resp = client.post("/ask", json={"question": "hello"})
    assert resp.status_code == 401
