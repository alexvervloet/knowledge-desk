"""Phase 9 observability: the tracer is inert without keys and never breaks a
request, and retrieval_stats exposes the ACL filter (org total vs what a caller
may see) that the retriever trace span reports.
"""

import contextlib
from typing import Any

import langfuse
import pytest
from fastapi.testclient import TestClient

from knowledge_desk import accounts, ingest, tracing
from knowledge_desk.main import app
from knowledge_desk.tenancy import TenantScope
from knowledge_desk.tracing import AskTracer

client = TestClient(app)

pytestmark = pytest.mark.usefixtures("clean_db")

PW = "pw-supersecret"


def test_tracer_is_inert_without_keys():
    # Every method must be a safe no-op when Langfuse is not configured.
    t = AskTracer("q", "org", "user", "mock", "mock")
    assert t.active is False
    t.sources([{"path": "a"}], {"org_chunks": 3, "allowed_chunks": 1})
    t.token("hello ")
    t.done(1, 2, 0.0)
    t.finish()  # no raise


class _FakeObs:
    def __init__(self):
        self.start_kwargs: dict[str, Any] = {}
        self.updates: list[dict[str, Any]] = []
        self.children: list[_FakeObs] = []
        self.ended = False
        self.trace_io: dict[str, Any] = {}

    def start_observation(self, **kw):
        child = _FakeObs()
        child.start_kwargs = kw
        self.children.append(child)
        return child

    def update(self, **kw):
        self.updates.append(kw)

    def end(self):
        self.ended = True

    def set_trace_io(self, **kw):
        self.trace_io = kw


class _FakeClient:
    def __init__(self):
        self.root: _FakeObs | None = None

    def start_observation(self, **kw):
        self.root = _FakeObs()
        self.root.start_kwargs = kw
        return self.root


def test_tracer_records_spans_when_enabled(monkeypatch):
    # Prove the active path calls the SDK correctly, without a real Langfuse.
    @contextlib.contextmanager
    def _noop_attrs(**_kw):
        yield

    monkeypatch.setattr(langfuse, "propagate_attributes", _noop_attrs)
    fake = _FakeClient()
    monkeypatch.setattr(tracing, "_client", fake)

    t = AskTracer("what is x?", "org-1", "user-1", "claude", "claude-opus-5")
    assert t.active is True
    t.sources([{"path": "a.txt"}], {"org_chunks": 5, "allowed_chunks": 2})
    t.token("the answer ")
    t.done(100, 20, 0.0012)
    t.finish()

    root = fake.root
    assert root is not None, "the active tracer must have opened a root observation"
    assert root.start_kwargs["name"] == "ask"
    assert root.start_kwargs["metadata"]["org_id"] == "org-1"
    retrieval, generation = root.children  # retriever then generation
    assert retrieval.ended and retrieval.updates[0]["output"]["acl"] == {
        "org_chunks": 5,
        "allowed_chunks": 2,
    }
    assert generation.start_kwargs["as_type"] == "generation"
    assert generation.updates[0]["usage_details"] == {"input": 100, "output": 20}
    assert generation.updates[0]["cost_details"] == {"total": 0.0012}
    assert root.trace_io["output"] == "the answer "
    assert root.ended


def test_tracer_redacts_pii_before_it_leaves_for_langfuse(monkeypatch):
    """The question and answer are stored unredacted in Postgres deliberately.
    Langfuse is a third party, so the same text is redacted on the way there."""

    @contextlib.contextmanager
    def _noop_attrs(**_kw):
        yield

    monkeypatch.setattr(langfuse, "propagate_attributes", _noop_attrs)
    fake = _FakeClient()
    monkeypatch.setattr(tracing, "_client", fake)

    t = AskTracer("what did dana@acme.test file?", "org-1", "user-1", "claude", "m")
    t.sources([{"path": "hr/dana@acme.test-review.txt"}], None)
    t.token("her SSN is ")
    t.token("123-45-6789")
    t.done(10, 5, 0.001)
    t.finish()

    root = fake.root
    retrieval, generation = root.children
    assert "dana@acme.test" not in root.start_kwargs["input"]
    assert "[REDACTED-EMAIL]" in root.start_kwargs["input"]
    assert "dana@acme.test" not in str(retrieval.updates[0]["output"])
    assert "123-45-6789" not in generation.updates[0]["output"]
    assert "[REDACTED-SSN]" in generation.updates[0]["output"]
    assert "123-45-6789" not in str(root.trace_io)


def test_tracer_does_not_send_the_users_email_address(monkeypatch):
    """user_id already ties a trace to a person. The address is the one field
    here that identifies one on its own, so it does not go."""

    @contextlib.contextmanager
    def _noop_attrs(**_kw):
        yield

    monkeypatch.setattr(langfuse, "propagate_attributes", _noop_attrs)
    fake = _FakeClient()
    monkeypatch.setattr(tracing, "_client", fake)

    AskTracer("q", "org-1", "user-1", "claude", "m").finish()
    assert "email" not in fake.root.start_kwargs["metadata"]


def test_a_streamed_secret_split_across_tokens_is_still_redacted(monkeypatch):
    """Redaction happens at the join. Per token, "123-45-" and "6789" each look
    harmless, and the pattern only exists once they are back together."""

    @contextlib.contextmanager
    def _noop_attrs(**_kw):
        yield

    monkeypatch.setattr(langfuse, "propagate_attributes", _noop_attrs)
    fake = _FakeClient()
    monkeypatch.setattr(tracing, "_client", fake)

    t = AskTracer("q", "org-1", "user-1", "claude", "m")
    t.sources([], None)
    for piece in ("123", "-45", "-6789"):
        t.token(piece)
    t.done(1, 1, 0.0)
    assert fake.root.children[1].updates[0]["output"] == "[REDACTED-SSN]"


def test_ask_still_streams_with_tracing_path():
    token = client.post(
        "/auth/signup",
        json={
            "org_slug": "acme",
            "org_name": "Acme",
            "email": "o@acme.test",
            "password": PW,
        },
    ).json()["token"]
    client.post(
        "/sources/folder",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "documents": [
                {
                    "path": "a.txt",
                    "content": "refunds take five business days",
                    "acl": ["public-to-org"],
                }
            ]
        },
    )
    ingest.run_pending()
    resp = client.post(
        "/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "refunds take five business days"},
    )
    assert resp.status_code == 200
    assert '"type": "done"' in resp.text and '"type": "sources"' in resp.text


def test_retrieval_stats_exposes_acl_filter():
    owner = accounts.create_org_with_owner("acme", "Acme", "o@acme.test", PW)
    x_id = accounts.add_member(owner.org_id, "x@acme.test", PW, "member")
    accounts.add_member(owner.org_id, "y@acme.test", PW, "member")
    ingest.sync_documents(
        owner.org_id,
        "local-folder",
        [
            {"path": "public.txt", "content": "everyone can read this", "acl": ["public-to-org"]},
            {"path": "secret.txt", "content": "only x can read this", "acl": [f"user:{x_id}"]},
        ],
    )
    ingest.run_pending()

    x = TenantScope(accounts.authenticate("x@acme.test", PW, "acme"))
    y = TenantScope(accounts.authenticate("y@acme.test", PW, "acme"))

    xs, ys = x.retrieval_stats(), y.retrieval_stats()
    assert xs["org_chunks"] == 2  # both documents, one chunk each
    assert xs["allowed_chunks"] == 2  # X sees the public and its own secret
    assert ys["org_chunks"] == 2
    assert ys["allowed_chunks"] == 1  # Y sees only the public document
