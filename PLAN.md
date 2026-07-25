# Plan: phases and definitions of done

Knowledge Desk is a multi-tenant, permissions-aware knowledge assistant: each
organization connects its documents, the system indexes them per tenant, and
users ask questions and get grounded, cited answers drawn only from the
documents they are allowed to see. The point of this project is the operational
layer around the model (tenancy, access-controlled retrieval, background
ingestion, quotas, audit, governance), not the RAG technique, which is reused
from the earlier projects.

Every phase ends with something checkable: a passing run, a filled table, a
public URL. Not a feeling. Gotchas go in the log at the bottom the moment they
surprise you. Numbers go in tables, not prose.

## What makes this "enterprise" (the muscles each phase builds)

- **Multi-tenancy**: orgs, users, roles, hard data isolation between tenants (Phase 1)
- **Permissions-aware retrieval**: a user only ever retrieves from documents they can access (Phase 3, the crux)
- **Async processing**: durable job queue, background workers, retries, idempotency, incremental resync (Phase 2)
- **Operational controls**: per-org quotas, cost attribution, rate limits, graceful degradation (Phase 5)
- **Governance**: audit log, PII handling, retention and deletion, evals as a CI merge gate (Phase 6)

## Stack and key decisions

Reuses the house stack so the new effort goes into the enterprise parts.

- **Backend**: FastAPI, streaming answers over SSE (same shape as askrepo-live).
- **Store**: one Postgres with pgvector. Multi-tenant by shared database with an
  `org_id` on every row, scoped through a single tenant-aware data layer so no
  query can forget the filter. Postgres row-level security is added later as
  defense in depth (Phase 6), not relied on as the only guard.
- **Auth**: built-in email plus password with server-side sessions to start.
  Roles are `owner`, `admin`, `member`. The session carries the acting user and
  their current org. An OIDC seam is left open but not built in v1.
- **ACL model**: every document carries an access control list of principals
  (a user id or a group id). A user's principal set is {their user id} plus the
  groups they belong to. Retrieval filters candidate chunks to those whose
  document ACL intersects the caller's principal set, enforced in the SQL that
  fetches candidates, before ranking. This is the part most RAG demos skip.
- **Ingestion**: a durable job queue backed by a Postgres `jobs` table plus a
  worker loop (no Redis dependency in v1; the queue can move to arq later).
  Jobs are idempotent by content hash so a resync re-embeds only what changed.
- **Embeddings and answers**: Voyage for embeddings, Claude for answers, with
  the loud keyless mock fallback (banner plus provider label) from the other
  repos so the whole thing runs and tests green with no keys.
- **Frontend**: React plus Vite, an ask view and an admin view.
- **Observability**: Langfuse, one trace per question, tagged by org and user.
- **CI**: GitHub Actions. `check_setup.py` plus pytest plus a docker compose
  end-to-end, same as the other flagships.

## Phase 0: plumbing (done when the keyless smoke passes in CI)

- [x] Repo, per-repo `.venv`, `requirements.txt`, `pyproject.toml` (installable package)
- [x] `docker-compose.yml` brings up Postgres with pgvector on a dedicated port
      (5432 rag-at-scale, 5434 askrepo-live, 5433/5435 in use; this repo takes **5436**)
- [x] `python check_setup.py` all green (db reachable, pgvector v0.8.4 present, provider mode)
- [x] FastAPI skeleton with `/healthz` reporting provider (mock vs real)
- [x] `pytest` green: healthz, a mock ask returning a canned answer, 404 and 422 shapes
- [x] GitHub Actions runs check_setup plus pytest plus compose up on a fresh runner

**Phase 0 complete** (CI green 2026-07-25). Postgres 17.10 + pgvector 0.8.4 on
host port 5436; 6 smoke tests pass hermetically; the compose end-to-end is the
fresh-runner proof.

## Phase 1: tenancy and auth skeleton (done when isolation tests pass, no LLM yet)

Build the multi-tenant spine before any AI touches it. Two orgs, seeded users,
and a proof that one org cannot read the other's rows through any endpoint.

- [x] Schema and migrations: `orgs`, `users`, `memberships` (user, org, role),
      `groups`, `group_members`, `sessions`. Every domain table carries `org_id`.
      Plain-SQL migrations via a small runner (`python -m knowledge_desk.migrate`).
- [x] Tenant-aware data layer: `TenantScope` stamps and filters `org_id` on every
      org-scoped read and write; a foreign-org id is a 404, indistinguishable from
      absent. Identity and grants (users, memberships, sessions) live in `accounts`;
      org data lives behind the scope. Convention for now, not yet a lint rule.
- [x] Auth: signup creates an org plus an owner; login issues a session (bearer
      token, sha256-hashed at rest); logout; `require_role` gate for admin routes.
      Passwords: bcrypt over a sha256 pre-hash (no 72-byte truncation).
- [x] Isolation test suite: user in org A gets 404 on org B resources; role gates
      (member cannot create groups or add members, admin can); session expiry,
      logout, and revoked-membership all invalidate a token
- [x] Fixtures: `clean_db` truncates between DB tests; smoke tests stay hermetic.
      Two orgs (`acme`, `globex`) built per test rather than a shared seed.

**Phase 1 complete.** 21 tests green (6 hermetic smoke plus 15 tenancy). The
load-bearing test: org A gets a 404, not a 403, on org B's group id, so existence
does not leak across tenants.

## Phase 2: ingestion pipeline (done when a resync re-embeds only what changed)

Local folder connector first: deterministic, testable in CI, no external OAuth.
An org uploads a folder, a worker chunks, embeds, and stores per tenant.

- [x] `jobs` table plus a worker loop: claim (`for update skip locked`), run,
      retry with backoff, dead-letter after `max_attempts`, idempotency key on the
      job (`ingest:<doc_id>:<content_hash>`) so re-upload of identical bytes is a no-op
- [x] Local folder connector: upload captures one `documents` row per file with a
      captured ACL (Phase 3 uses it), chunk, embed (Voyage, mock fallback), vectors
      scoped to `org_id`. Content is captured at upload; the worker embeds off the
      request path.
- [x] Incremental resync: re-uploading re-embeds only changed/new files (by hash),
      marks deletions (status `deleted`, chunks removed), leaves the rest untouched
- [x] Failure handling: a poison document dead-letters (status `failed`) without
      wedging the queue; the good documents in the same batch still ingest
- [x] Table (mock embedder, 25 synthetic markdown files, this laptop; a keyed
      Voyage run with real cost is a `secrun` step):

| run | files | chunks | drain (embed+store) | resync unchanged |
|---|---|---|---|---|
| mock | 25 | 100 | 1377ms | 12ms |

Resync of an unchanged corpus is ~115x cheaper than the first drain (12ms vs
1377ms): the hash check skips embedding entirely, which is the whole point.

**Phase 2 complete.** 34 tests green (adds 6 queue, 7 ingestion). Embedding is
off the request path behind a durable, idempotent, retrying queue.

## Phase 3: permissions-aware retrieval (done when the leak test cannot leak)

The crux. Retrieval must never surface a chunk from a document the caller cannot
access, and this must hold at the SQL layer, not as a post-filter that a bug
could skip.

- [ ] Document ACLs: each document lists permitted principals; a `public-to-org`
      shortcut for org-wide docs; group membership resolves into the principal set
- [ ] Access-scoped retrieval: candidate fetch joins the ACL and the caller's
      principals so forbidden chunks are never even ranked; `org_id` filter on top
- [ ] The permission-leak eval: seed a document readable only by user X containing
      a unique secret string; assert user Y's retrieval and answer never contain it,
      across paraphrases and direct "what is the secret" prompts. This eval is a
      hard gate in CI (Phase 6), not a one-off check.
- [ ] Group-change correctness: removing a user from a group immediately removes
      access on the next query (no stale index of permissions)

## Phase 4: the assistant (done when a grounded, access-correct cited answer streams)

- [ ] Ask endpoint: retrieve within the caller's permissions, answer with Claude,
      stream SSE (meta, sources, tokens, done), citations deep-link to the source doc
- [ ] Refusal when retrieval is empty: says it has nothing it is allowed to cite,
      rather than answering from model priors
- [ ] Feedback capture: thumbs plus optional note per answer, stored with the trace id
- [ ] Frontend ask view renders streaming answer, citations, and the provider label

## Phase 5: operational controls (done when the abuse and quota tests pass)

- [ ] Cost attribution ledger: per-answer token and dollar estimate written with
      `org_id` and user; a rolling per-org daily budget that blocks the model call
      with a friendly frame once spent
- [ ] Quotas: per-org monthly question cap, per-user rate limit (token bucket),
      per-org document and storage cap on ingest
- [ ] Loud degradation: provider down or over budget returns a clear frame and a
      banner, never a silent wrong answer; observability failures never take the app down
- [ ] Audit log: append-only record of logins, invites, ingests, and questions
      (who, which org, what, when), queryable by an org admin
- [ ] Table (fill once chosen, all env-overridable):

| knob | value | env var |
|---|---|---|
| per-user rate | | |
| per-org daily budget ($) | | |
| per-org monthly questions | | |
| per-org storage cap | | |

## Phase 6: governance and evals-as-gate (done when CI blocks a permission regression)

- [ ] Eval suite wired into CI as a required check: grounded-answer quality plus
      the Phase 3 permission-leak eval; a PR that reintroduces a leak fails to merge
- [ ] PII pass: detect and flag obvious PII at ingest; document what is stored and
      where; redact PII from logs and traces
- [ ] Retention and deletion: an org can delete a document (vectors, chunks, ACLs
      all removed) and export or delete its whole tenant; verified by a test
- [ ] Postgres row-level security added as defense in depth behind the data layer,
      with a test that a raw query without the org context returns nothing

## Phase 7: admin dashboard (done when an org admin can run the org from the UI)

- [ ] Members and roles: invite, change role, remove; group management
- [ ] Sources: connected folders, last sync, per-document ACL view and edit
- [ ] Usage: questions, cost, quota headroom, top queries, recent audit events
- [ ] Mobile pass and a demo GIF (scripted, like askrepo-live's `demo:gif`)

## Phase 8: containerize and deploy (done when a seeded multi-tenant demo is public)

- [ ] Multi-stage Dockerfile serves UI plus API; worker runs as a second process
- [ ] `docker compose up --build` runs api, worker, and pgvector end to end in CI
- [ ] Seed script creates two demo orgs with distinct, non-overlapping documents
      so a reviewer can log into each and see isolation and access control live
- [ ] Deploy (fly.io, managed Postgres). Public URL with two demo logins in README
- [ ] First-week table:

| week of | orgs | questions | est. spend ($) | host bill ($) |
|---|---|---|---|---|

## Phase 9: observability (done when the dashboard shows per-tenant traffic)

- [ ] One Langfuse trace per question, tagged by org and user, with a retriever
      span (how many candidates before and after the ACL filter) and the generation
- [ ] A per-org view of volume, cost, and latency; note what the first week showed
- [ ] Dashboard screenshot and link in README

## Open decisions to revisit

- Job queue stays Postgres-backed until a phase actually needs concurrency it
  cannot give; only then consider arq or RQ. Note the trigger when it happens.
- Groups are flat in v1. Nested groups and inherited ACLs are a later concern;
  record the moment flat groups stop being enough.
- Auth stays built-in until a reviewer or a real user needs SSO; the OIDC seam is
  left open but unbuilt.

## Gotcha log

(keep a running log here the moment something surprises you; this becomes the
best part of the write-up)

- **2026-07-25**: Postgres `text` columns cannot store a NUL (0x00) byte at all;
  the insert fails with `DataError` before any application code runs. So a binary
  or garbage file cannot be "captured then failed at embed time" the way I first
  modeled it. Two consequences: binary filtering belongs at the connector
  boundary (the local-folder connector should skip non-text files), and the
  reachable worker-side failure to test is an embedder/provider rejection, not a
  NUL byte. The mock embedder now fails on a storable sentinel (`[[EMBED-FAIL]]`)
  instead. Binary-file filtering at the connector is deferred (noted here).
- **2026-07-25**: the local dev pgvector container keeps stopping between work
  sessions. Cause is benign: clean shutdown (exit 0, not OOM) with a multi-hour
  jump in the log timestamps, i.e. the Mac slept and Docker Desktop paused it.
  `docker start knowledge-desk-pg` brings it back with the volume intact. CI is
  unaffected (it starts its own). Same family as askrepo-live's Docker sleep
  quirk. Not a code issue; just restart the container after the laptop sleeps.
- **2026-07-25**: incremental resync is worth the content-hash bookkeeping. An
  unchanged 25-file corpus resyncs in ~12ms versus ~1377ms for the first drain,
  because the hash match skips chunking and embedding entirely. At real Voyage
  latency and cost the gap is far larger, since the skipped work is the network
  embed calls, not the local hash.
