"""Security response headers. Hermetic: no route here touches the database.

The SPA is served same-origin from the API in production, so these land on the
page that holds the session token.
"""

from fastapi.testclient import TestClient

from knowledge_desk.main import app
from knowledge_desk.securityheaders import HEADERS

client = TestClient(app)


def test_every_header_is_present_on_an_api_response():
    resp = client.get("/healthz")
    for name, value in HEADERS.items():
        assert resp.headers.get(name) == value, name


def test_headers_are_present_on_an_error_response_too():
    """A 401 is still a response the browser renders, and the paths that return
    one are the paths an attacker reaches for."""
    resp = client.get("/me")
    assert resp.status_code == 401
    assert "content-security-policy" in resp.headers


def test_scripts_are_restricted_to_our_own_origin():
    csp = client.get("/healthz").headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "'unsafe-eval'" not in csp


def test_the_page_cannot_be_framed_or_have_its_base_rewritten():
    csp = client.get("/healthz").headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "object-src 'none'" in csp


def test_docs_keep_the_other_headers_but_not_the_csp():
    """Swagger UI loads its bundle from a CDN, which script-src 'self' blocks.
    The exemption is narrow on purpose: that page renders our own schema, and
    every route that touches a session still carries the policy."""
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "content-security-policy" not in resp.headers
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
