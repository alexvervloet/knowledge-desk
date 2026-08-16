"""Phase 5: operational controls. Per-org budget and question caps block the
model call with a loud frame; a per-user rate limit returns 429; ingest respects
storage caps; every answer records its usage; and key actions land in the audit
log an admin can read.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from knowledge_desk import accounts, assistant, ingest
from knowledge_desk.config import settings
from knowledge_desk.db import connect, require_row
from knowledge_desk.errors import QuotaExceeded
from knowledge_desk.main import app
from knowledge_desk.providers import MockAnswerProvider
from knowledge_desk.ratelimit import TokenBucketLimiter, auth_limiter
from knowledge_desk.tenancy import AuthContext, TenantScope

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
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


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
        row = require_row(
            conn.execute("select blocked from answers where id = %s", (aid,)).fetchone()
        )
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


def test_platform_budget_blocks_an_org_that_is_under_its_own(monkeypatch):
    """Per-org caps bound one tenant, not the bill: signup is open, so a fresh
    org comes with a fresh budget. The deployment-wide ceiling is the number
    that actually bounds a day's spend, and it has to bite a brand new org that
    has not spent a cent of its own allowance."""
    monkeypatch.setattr(settings, "platform_daily_budget_usd", 1.0)
    spent = signup("acme", "o@acme.test")
    _scope_for(spent).finalize_answer(
        _answered_id(spent), input_tokens=1, output_tokens=1, cost_usd=1.5,
    )

    # A different org, untouched budget of its own.
    fresh = signup("globex", "o@globex.test")
    assert _scope_for(fresh).spend_last_24h() == 0.0
    events = ask_events(fresh, "anything at all")
    error = next(e for e in events if e["type"] == "error")
    assert "service daily budget exhausted" in error["message"]


def _answered_id(token: str) -> str:
    """Ask once and return the answer row's id, so a test has something to bill."""
    return next(e for e in ask_events(token, "seed the ledger") if e["type"] == "meta")["answer_id"]


# --- auth rate limit and the timing oracle ---------------------------------


def test_login_is_throttled(monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_burst", 3)
    monkeypatch.setattr(settings, "auth_rate_per_min", 1)  # negligible refill
    signup("acme", "o@acme.test")
    auth_limiter.reset()  # signup consumed a token from the same bucket
    bad = {"email": "o@acme.test", "password": "wrongbutlongenough"}
    codes = [client.post("/auth/login", json=bad).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]


def test_signup_is_throttled(monkeypatch):
    """The per-org spend and question caps are only a bound if orgs are not free
    to mint, so the signup route needs the same throttle as login."""
    monkeypatch.setattr(settings, "auth_rate_burst", 2)
    monkeypatch.setattr(settings, "auth_rate_per_min", 1)
    codes = [
        client.post("/auth/signup", json={
            "org_slug": f"org-{i}", "org_name": "O", "email": f"o{i}@x.test", "password": PW,
        }).status_code
        for i in range(3)
    ]
    assert codes == [201, 201, 429]


def test_auth_limit_is_independent_of_the_ask_limit(monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_burst", 1)
    monkeypatch.setattr(settings, "auth_rate_per_min", 1)
    token = signup("acme", "o@acme.test")
    # Signup exhausted the auth bucket, but asking is a separate limiter.
    assert client.post("/auth/login", json={"email": "o@acme.test", "password": PW}).status_code == 429
    assert client.post("/ask", headers=auth(token), json={"question": "hi"}).status_code == 200


def test_login_costs_the_same_whether_or_not_the_account_exists():
    """A miss used to skip bcrypt entirely, so response time answered "does this
    email have an account" — roughly 4ms against 240ms. Both paths must hash."""
    signup("acme", "o@acme.test")
    bad = "wrongbutlongenough"

    def timed(email: str) -> float:
        auth_limiter.reset()
        start = time.perf_counter()
        assert client.post("/auth/login", json={"email": email, "password": bad}).status_code == 401
        return time.perf_counter() - start

    known = min(timed("o@acme.test") for _ in range(3))
    unknown = min(timed("nobody@acme.test") for _ in range(3))
    # Generous bound: the point is that one is not an order of magnitude faster,
    # not that a shared CI runner produces stable timings.
    assert 0.5 < unknown / known < 2.0, f"known={known:.3f}s unknown={unknown:.3f}s"


def test_idle_rate_limit_buckets_are_evicted():
    """One entry per key, kept for the life of the process, is a slow leak. A
    bucket idle long enough has refilled to full, so it is indistinguishable
    from a key never seen and there is nothing to lose by dropping it."""
    clock = [0.0]
    limiter = TokenBucketLimiter(clock=lambda: clock[0])
    for i in range(1200):
        limiter.check(f"key-{i}")
    assert len(limiter._buckets) == 1200

    clock[0] += TokenBucketLimiter._EVICT_AFTER_SECONDS + 1
    limiter.check("someone-new")
    assert len(limiter._buckets) == 1, "idle buckets should be gone"


def test_expired_sessions_are_purged():
    token = signup("acme", "o@acme.test")
    with connect() as conn:
        conn.execute("update sessions set expires_at = now() - interval '1 day'")

    assert client.get("/me", headers=auth(token)).status_code == 401  # already refused
    assert accounts.purge_expired_sessions() == 1
    with connect() as conn:
        assert require_row(conn.execute("select count(*) as n from sessions").fetchone())["n"] == 0


def test_concurrent_uploads_cannot_both_pass_the_same_cap(monkeypatch):
    """The cap was read in one transaction and the write happened in another, so
    two uploads could each see room only one of them had. The check now runs
    inside the write transaction behind a lock on the org row."""
    monkeypatch.setattr(settings, "org_doc_cap", 10)
    token = signup("acme", "o@acme.test")
    me = client.get("/me", headers=auth(token)).json()
    scope = TenantScope(AuthContext(me["user_id"], me["org_id"], me["role"], me["email"]))

    docs = [{"path": f"a{i}.txt", "content": "x"} for i in range(6)]
    other = [{"path": f"b{i}.txt", "content": "x"} for i in range(6)]
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(scope.sync_source, f"src-{n}", d)
                   for n, d in enumerate((docs, other))]
        for f in futures:
            try:
                results.append(f.result())
            except QuotaExceeded:
                results.append("rejected")

    assert "rejected" in results, "6 + 6 documents cannot both fit under a cap of 10"
    with connect(scope.org_id) as conn:
        total = require_row(conn.execute(
            "select count(*) as n from documents where org_id = %s", (scope.org_id,)
        ).fetchone())["n"]
    assert total <= 10


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
        row = require_row(conn.execute(
            "select output_tokens, refused, blocked, usage_estimated"
            " from answers where id = %s", (aid,)
        ).fetchone())
    assert row["output_tokens"] > 0 and not row["refused"] and not row["blocked"]
    assert row["usage_estimated"] is False  # reported by the provider, not inferred


# --- billing a stream that does not finish ---------------------------------


def _answer_row(token: str, answer_id: str) -> dict:
    with connect(org_of(token)) as conn:
        return require_row(conn.execute(
            "select input_tokens, output_tokens, cost_usd, usage_estimated"
            " from answers where id = %s", (answer_id,)
        ).fetchone())


def _scope_for(token: str):
    me = client.get("/me", headers=auth(token)).json()
    return TenantScope(AuthContext(me["user_id"], me["org_id"], me["role"], me["email"]))


def test_abandoned_stream_is_still_billed():
    """A client that disconnects before the final usage frame used to leave the
    row at zero tokens and zero dollars, so the budget never advanced even
    though the model had already generated. Aborting every request just before
    the end was a way to spend without ever being billed."""
    token = signup("acme", "o@acme.test")
    upload(token, [{"path": "doc.txt", "content": "the sky is blue today", "acl": ["public-to-org"]}])

    stream = assistant.answer_stream(_scope_for(token), "the sky is blue today", 5)
    answer_id = next(e for e in stream if e["type"] == "meta")["answer_id"]
    next(e for e in stream if e["type"] == "token")  # consume one token, then walk away
    stream.close()

    row = _answer_row(token, answer_id)
    assert row["output_tokens"] > 0, "an abandoned stream must still book what it consumed"
    assert row["usage_estimated"] is True


def test_a_stream_that_never_reaches_the_model_is_not_billed():
    """The estimate is keyed on having streamed something, so a refusal — where
    no provider call happens at all — must not invent a charge."""
    token = signup("acme", "o@acme.test")
    events = ask_events(token, "nothing here matches this")
    answer_id = next(e for e in events if e["type"] == "meta")["answer_id"]

    row = _answer_row(token, answer_id)
    assert (row["input_tokens"], row["output_tokens"], row["cost_usd"]) == (0, 0, 0.0)
    assert row["usage_estimated"] is False


def test_abandoned_stream_counts_toward_the_org_budget():
    """The point of billing it: the spend the budget sees must move."""
    token = signup("acme", "o@acme.test")
    upload(token, [{"path": "doc.txt", "content": "the sky is blue today", "acl": ["public-to-org"]}])
    scope = _scope_for(token)

    # The mock provider is free, so price the estimate to prove the wiring.
    with_cost = {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.25}
    stream = assistant.answer_stream(scope, "the sky is blue today", 5)
    next(e for e in stream if e["type"] == "token")
    with mock.patch.object(MockAnswerProvider, "estimate", return_value=with_cost):
        stream.close()

    assert scope.spend_last_24h() == pytest.approx(0.25)


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
