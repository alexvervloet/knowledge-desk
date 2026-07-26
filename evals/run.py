"""Merge-gating evals. These run the real app end to end and assert the two
guarantees that must never regress: a user cannot retrieve or be answered from
another user's document (the permission leak), and a grounded question is
answered from the permitted context.

    python -m evals.run     # prints a report, exits nonzero if any eval fails

Wired as a required CI step, so a change that reintroduces a leak fails the
build. The same functions are asserted from tests/test_evals.py for local runs.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import psycopg
from fastapi.testclient import TestClient

from knowledge_desk import ingest
from knowledge_desk.config import settings
from knowledge_desk.main import app

client = TestClient(app)

_ALL_TABLES = (
    "orgs, users, memberships, groups, group_members, sessions,"
    " documents, chunks, jobs, answers, feedback, audit_log"
)
SECRET = "the passphrase is copper-moon-42"
PW = "pw-supersecret"


def _reset() -> None:
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(f"truncate {_ALL_TABLES} cascade")
        conn.commit()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _signup(slug: str, email: str) -> str:
    return client.post(
        "/auth/signup",
        json={"org_slug": slug, "org_name": slug, "email": email, "password": PW},
    ).json()["token"]


def _add_member(owner: str, email: str) -> str:
    return client.post(
        "/members", headers=_headers(owner),
        json={"email": email, "password": PW, "role": "member"},
    ).json()["user_id"]


def _login(email: str, slug: str) -> str:
    return client.post(
        "/auth/login", json={"email": email, "password": PW, "org_slug": slug}
    ).json()["token"]


def _upload(token: str, docs: list[dict]) -> None:
    client.post("/sources/folder", headers=_headers(token), json={"documents": docs})
    ingest.run_pending()


def _ask(token: str, question: str) -> list[dict]:
    resp = client.post("/ask", headers=_headers(token), json={"question": question})
    return [json.loads(l[6:]) for l in resp.text.splitlines() if l.startswith("data: ")]


def _sources(events: list[dict]) -> list[dict]:
    got = [e for e in events if e["type"] == "sources"]
    return got[0]["sources"] if got else []


def _text(events: list[dict]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "token")


def permission_leak_eval() -> dict[str, Any]:
    """A secret only user X may see must never reach user Y, via search or ask."""
    _reset()
    owner = _signup("acme", "owner@acme.test")
    x_id = _add_member(owner, "x@acme.test")
    _add_member(owner, "y@acme.test")
    _upload(owner, [{"path": "secret.txt", "content": SECRET, "acl": [f"user:{x_id}"]}])

    x, y = _login("x@acme.test", "acme"), _login("y@acme.test", "acme")

    x_can_read = any(s["path"] == "secret.txt" for s in _sources(_ask(x, SECRET)))
    y_search = client.post("/search", headers=_headers(y), json={"query": SECRET, "k": 50}).json()
    y_events = _ask(y, SECRET)
    y_leaked = (
        any(s["path"] == "secret.txt" for s in y_search)
        or "copper-moon-42" in _text(y_events)
        or bool(_sources(y_events))
    )
    passed = x_can_read and not y_leaked
    return {"name": "permission-leak", "passed": passed,
            "detail": f"x_can_read={x_can_read} y_leaked={y_leaked}"}


def grounded_answer_eval() -> dict[str, Any]:
    """A permitted, matching document is cited in the answer's sources."""
    _reset()
    token = _signup("acme", "owner@acme.test")
    _upload(token, [{"path": "policy.txt", "content": "refunds take five business days",
                     "acl": ["public-to-org"]}])
    events = _ask(token, "refunds take five business days")
    cited = any(s["path"] == "policy.txt" for s in _sources(events))
    return {"name": "grounded-answer", "passed": cited,
            "detail": f"cited_policy_doc={cited}"}


def run_all() -> list[dict[str, Any]]:
    return [permission_leak_eval(), grounded_answer_eval()]


def main() -> int:
    results = run_all()
    print("eval gate")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"  {mark}  {r['name']:20} {r['detail']}")
    failed = [r for r in results if not r["passed"]]
    print()
    if failed:
        print(f"{len(failed)} eval(s) failed")
        return 1
    print("all evals passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
