"""Retrieval scale benchmark: how does access-scoped search behave as a tenant's
corpus grows, and what does the vector index actually buy?

    python -m knowledge_desk.bench --chunks 100000 [--queries 20]

Seeds one org with synthetic chunks via COPY (embedding a real corpus this size
would cost money and prove nothing about the query plan), then times the real
`TenantScope.search` path, ACL filter included. Prints the query plan so a
sequential scan cannot hide behind a fast wall-clock number on a warm cache.

Destructive: it truncates the domain tables. Run it against a local database.
"""

from __future__ import annotations

import argparse
import random
import statistics
import time

import psycopg

from knowledge_desk import accounts
from knowledge_desk.config import settings
from knowledge_desk.db import close_pool, connect
from knowledge_desk.embeddings import EMBED_DIM
from knowledge_desk.tenancy import TenantScope

_ALL_TABLES = (
    "orgs, users, memberships, groups, group_members, sessions,"
    " documents, chunks, jobs, answers, feedback, audit_log"
)


def _rand_vec(rng: random.Random) -> list[float]:
    return [rng.uniform(-1.0, 1.0) for _ in range(EMBED_DIM)]


def _seed(chunks: int, seed: int = 7) -> tuple[str, str]:
    rng = random.Random(seed)
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(f"truncate {_ALL_TABLES} cascade")
        conn.commit()

    ctx = accounts.create_org_with_owner("bench", "Bench", "o@bench.test", "pw-supersecret")

    # One parent document per 500 chunks keeps the join realistic without
    # creating a document row per chunk.
    per_doc = 500
    doc_ids: list[str] = []
    with connect(ctx.org_id) as conn:
        for i in range((chunks + per_doc - 1) // per_doc):
            row = conn.execute(
                "insert into documents(org_id, source, path, content, content_hash, acl, status)"
                " values (%s, 'bench', %s, '', %s, '[\"public-to-org\"]'::jsonb, 'ingested')"
                " returning id",
                (ctx.org_id, f"bench/doc{i:05d}.md", f"hash{i:05d}"),
            ).fetchone()
            doc_ids.append(str(row["id"]))

    # Bulk load as the owner: Postgres refuses COPY FROM on a table with row-level
    # security ("COPY FROM not supported with row-level security"), so the
    # least-privilege app role cannot bulk load at all. Real bulk imports have the
    # same constraint and belong on an admin connection like this one.
    t0 = time.perf_counter()
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor().copy(
            "copy chunks (org_id, document_id, ordinal, text, embedding) from stdin"
        ) as copy:
            for i in range(chunks):
                vec = "[" + ",".join(f"{v:.5f}" for v in _rand_vec(rng)) + "]"
                copy.write_row((ctx.org_id, doc_ids[i // per_doc], i % per_doc,
                                f"synthetic chunk {i}", vec))
        conn.commit()
    load = time.perf_counter() - t0
    print(f"  seeded {chunks} chunks in {load:.1f}s")
    return ctx.org_id, ctx.user_id


def _time_search(scope: TenantScope, queries: int, rng: random.Random) -> list[float]:
    timings = []
    for _ in range(queries):
        vec = _rand_vec(rng)
        t0 = time.perf_counter()
        scope.search(vec, k=6)
        timings.append((time.perf_counter() - t0) * 1000)
    return timings


def main() -> int:
    try:
        return _main()
    finally:
        close_pool()  # otherwise pool threads outlive an error path and warn


def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", type=int, default=100_000)
    ap.add_argument("--queries", type=int, default=20)
    ap.add_argument("--no-seed", action="store_true", help="reuse the existing bench org")
    args = ap.parse_args()

    if args.no_seed:
        ctx = accounts.authenticate("o@bench.test", "pw-supersecret", "bench")
        org_id, user_id = ctx.org_id, ctx.user_id
    else:
        org_id, user_id = _seed(args.chunks)

    ctx = accounts.authenticate("o@bench.test", "pw-supersecret", "bench")
    scope = TenantScope(ctx)
    rng = random.Random(11)

    with connect(org_id) as conn:
        n = conn.execute("select count(*) as n from chunks").fetchone()["n"]
        has_index = conn.execute(
            "select count(*) as n from pg_indexes where tablename = 'chunks'"
            " and indexdef ilike '%%hnsw%%'"
        ).fetchone()["n"] > 0

    _time_search(scope, 3, rng)  # warm the cache so we measure steady state
    timings = _time_search(scope, args.queries, rng)

    print(f"\n  chunks={n}  hnsw_index={'yes' if has_index else 'no'}  queries={args.queries}")
    print(f"  p50 {statistics.median(timings):8.1f} ms")
    print(f"  p95 {sorted(timings)[int(len(timings) * 0.95) - 1]:8.1f} ms")
    print(f"  max {max(timings):8.1f} ms")

    # The plan is the real evidence: a warm sequential scan can still look fast.
    with connect(org_id) as conn:
        from pgvector.psycopg import Vector

        principals = scope.principals()
        vec = Vector(_rand_vec(rng))
        plan = conn.execute(
            "explain analyze select c.id from chunks c"
            " join documents d on d.id = c.document_id"
            " where c.org_id = %s and d.status = 'ingested' and d.acl ?| %s"
            " order by c.embedding <=> %s limit 6",
            (org_id, principals, vec),
        ).fetchall()
    print("\n  plan:")
    for row in plan[:6]:
        # The query vector literal is 1024 floats wide; truncate so the plan reads.
        line = row["QUERY PLAN"]
        print("   ", line[:120] + (" ..." if len(line) > 120 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
