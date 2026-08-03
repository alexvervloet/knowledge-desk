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

- [x] Document ACLs: `documents.acl` is a JSONB principal list (`public-to-org`,
      `user:<id>`, `group:<id>`); a caller's principals are `public-to-org` plus
      their own user principal plus one per group they belong to
- [x] Access-scoped retrieval: `TenantScope.search` filters candidates in SQL with
      `d.acl ?| principals` (GIN-indexed) inside the `org_id` and `status='ingested'`
      filter, so a forbidden chunk is never scored. Query vector binds via pgvector's
      `Vector` (a bare list is `double precision[]`, no `<=>` operator).
- [x] The permission-leak test: a secret readable only by user X is never returned
      to user Y, for the exact content, an unrelated query, and a "what is the secret"
      probe (the guarantee is structural, not ranking-dependent). Wired into CI now;
      it also becomes a hard eval gate in Phase 6.
- [x] Group-change correctness: principals are recomputed per query, so deleting a
      group membership revokes retrieval access on the very next search

**Phase 3 complete.** 41 tests green (adds 7 retrieval). Distinctive result: the
access filter is in the candidate fetch, so cross-user and cross-tenant leakage is
prevented structurally rather than by a post-filter a later bug could skip.

Deferred: a remove-from-group API (the revocation test deletes the membership row
directly); it belongs with group management in Phase 7.

## Phase 4: the assistant (done when a grounded, access-correct cited answer streams)

- [x] Ask endpoint: `POST /ask` retrieves within the caller's permissions and
      streams SSE (meta, sources, token, done). Answer model `claude-opus-5`
      (env-overridable) at low effort; loud mock fallback with no keys. Sources
      carry document_id/ordinal/path for the frontend to deep-link later.
- [x] Refusal when retrieval is empty: no sources frame, a plain "nothing I'm
      allowed to cite" message, no model call. The Phase 3 boundary carries
      through to the answer: user Y never sees user X's secret in a generated reply.
- [x] Feedback capture: `POST /feedback` (up/down plus optional note), one per
      user per answer, attached to a recorded `answers` row and org-scoped. The
      answer row is where Phase 9's trace id will attach.
- [ ] Frontend ask view: **deferred to Phase 7** to build all the React at once.
      The SSE contract is curl- and test-verifiable now; no UI yet.

**Phase 4 complete (backend).** 47 tests green (adds 8 assistant). The answer
stream is access-scoped end to end and refuses rather than answering ungrounded.
Real Claude answers are a `secrun` step (keyless mock by default).

## Phase 5: operational controls (done when the abuse and quota tests pass)

- [x] Cost attribution ledger: each answer records `input_tokens`, `output_tokens`,
      and `cost_usd` (finalized from the done frame) with `org_id` and user. A
      rolling per-org 24h budget is summed from the answers table and blocks the
      model call before it runs once spent.
- [x] Quotas: per-org monthly question cap, per-user token-bucket rate limit
      (in-memory, settings-driven, injectable clock), per-org document and content
      -byte caps enforced on ingest before any embedding work.
- [x] Loud degradation: over budget or over cap yields a `[LIMIT]` error frame and
      no model call; a provider exception yields an error frame instead of a 500;
      audit writes are best-effort so a logging failure never takes the app down.
- [x] Audit log: append-only `audit_log` of org.created, user.login, member.added,
      source.synced, question.asked, and question.blocked; readable by an org admin
      at `GET /audit`.
- [x] Table (defaults; all env-overridable):

| knob | default | env var |
|---|---|---|
| per-user rate (burst / sustained) | 5 / 30 per min | `RATE_BURST` / `RATE_PER_MIN` |
| per-org daily budget ($) | 5.00 per 24h | `DAILY_BUDGET_USD` |
| per-org monthly questions | 1000 | `MONTHLY_QUESTION_CAP` |
| per-org documents | 1000 | `ORG_DOC_CAP` |
| per-org storage | 50 MB | `ORG_STORAGE_BYTES_CAP` |

**Phase 5 complete.** 57 tests green (adds 10 ops). Budget and caps are enforced
before the model call, the rate limit returns 429 with `Retry-After`, and every
answer's cost is on its row for the Phase 7 usage view.

## Phase 6: governance and evals-as-gate (done when CI blocks a permission regression)

- [x] Eval suite wired into CI as a required check: `python -m evals.run` gates
      the build on the permission-leak eval and a grounded-answer eval. A change
      that reintroduces a leak fails CI. Same evals asserted from `test_evals.py`.
- [x] PII pass: `pii.py` detects email/phone/ssn/card shapes; documents are flagged
      at ingest (`documents.pii_types`, shown in listings) and PII is redacted from
      audit-log detail. Data handling is documented in `.env.example` and the plan.
- [x] Retention and deletion: `DELETE /documents/{id}` removes a document and its
      chunks (ACL lives on the row); `GET /org/export` returns members + documents;
      `DELETE /org` (owner only) cascades the whole tenant. Verified by tests.
- [x] Postgres row-level security as defense in depth: FORCE RLS with an
      `org_isolation` policy keyed on the `app.current_org` GUC, on every org table
      that carries `org_id`. The app connects as a least-privilege role (`kd_app`)
      so RLS actually applies (owner/superuser bypasses it); migrations run as the
      owner. A test proves a raw query with no org context returns nothing.

**Phase 6 complete.** 67 tests green (adds 8 governance) plus the CI eval gate.
The distinctive result: tenant isolation is now enforced in three independent
layers, the data layer's `org_id` filter, the ACL candidate-fetch, and RLS
underneath, so no single missed filter can leak across tenants.

## Phase 7: admin dashboard and ask UI (done when an org admin can run the org from the UI)

- [x] Ask view: renders the streaming SSE answer, its citations, the mock/real
      provider banner, thumbs up/down to `/feedback`, and a Stop button that aborts
      the stream mid-answer
- [x] Members and roles: add member, change role, remove (with self and last-owner
      guards on the backend); group create/delete and add/remove members. The
      remove-from-group API deferred in Phase 3 is built here.
- [x] Sources: upload text files, document list with status, chunk count, PII
      flags, and per-document ACL view and edit; delete
- [x] Usage: questions, spend, and storage against their caps (with meters), the
      month's top queries, and the recent audit events table
- [x] Mobile pass: responsive CSS (media queries, `overflow-x` on tables,
      full-width 40px touch targets under 480px), role-gated tabs
- [ ] Deferred: the scripted demo GIF (Playwright + ffmpeg, like askrepo-live's
      `demo:gif`) is a nice-to-have left for a later pass

**Phase 7 complete.** React + Vite + TS frontend under `frontend/`, `npm run build`
clean and gated by a CI `web` job. Stack: FastAPI (CORS for the dev origin) plus a
typed client with SSE parsing. 77 backend tests green (adds 10 admin); the UI was
verified against a live uvicorn (signup, admin usage, CORS preflight).

## Phase 8: containerize and deploy (done when a seeded multi-tenant demo is public)

- [x] Multi-stage Dockerfile: node builds the SPA, the python image serves it
      same-origin with the API (`SERVE_STATIC=1`). The worker runs from the same
      image with a different command. Migrations are self-contained (a `0000`
      migration creates the pgvector extension) so the container needs no preflight.
- [x] `docker compose up --build` runs api, worker, and pgvector end to end, and a
      CI `compose` job proves it on a clean runner: build, wait for healthy, seed,
      signup, ask, and confirm the SPA is served. Green.
- [x] Seed script (`python -m knowledge_desk.seed`, idempotent) creates two demo
      orgs (`acme`, `globex`) with distinct, non-overlapping documents, so a
      reviewer can log into each and see that neither can retrieve the other's.
- [ ] Deploy (fly.io, managed Postgres): `fly.toml` committed (web + worker
      processes, release-command migrations, always-on off, ~$3-4/month). Runbook
      below; the deploy itself is a [Alex] step (account + cost).
- [ ] First-week table (fill after deploy):

| week of | orgs | questions | est. spend ($) | host bill ($) |
|---|---|---|---|---|

### Deploy runbook (fly.io)

Steps marked [Alex] create accounts or cost money.

- [ ] [Alex] `fly launch --no-deploy` (reuse the committed `fly.toml`)
- [ ] [Alex] Managed Postgres: `fly postgres create` then `fly postgres attach`
      (sets `DATABASE_URL`). The attached role is not a superuser, so FORCE RLS
      applies to it.
- [ ] [Alex] Create the app role and set `APP_DATABASE_URL` secret to the `kd_app`
      DSN on the same host (least privilege). The `0007` migration creates `kd_app`;
      alternatively point `APP_DATABASE_URL` at the attached role (RLS still holds
      via FORCE, but without the privilege separation).
- [ ] [Alex] Optional real mode: set `ANTHROPIC_API_KEY` / `VOYAGE_API_KEY` secrets
      from the Keychain via `secrun`. Keyless runs in the loud mock fallback.
- [ ] [Alex] `fly deploy` (release command runs migrations once), then
      `fly ssh console -C "python -m knowledge_desk.seed"` for the demo orgs
- [ ] [Alex] Put the public URL and the two demo logins in the README

**Phase 8 complete except the [Alex] deploy.** The image, the full compose stack,
and the seed are all proven green in CI on a clean runner; only the fly.io launch
(which needs an account and costs money) is left, and its files and runbook are
ready.

## Phase 9: observability (done when the dashboard shows per-tenant traffic)

- [x] One Langfuse trace per question, tagged by org (metadata) and user (trace
      user), with a retriever span that records `org_chunks` vs `allowed_chunks`
      (the ACL filter made visible), and the answer as a generation carrying token
      usage and cost. No-op without keys; every tracer call is exception-proof so
      observability can never take the product down; explicit span objects, not
      context managers, because the answer streams and interleaves.
- [x] Verified without a live account: the no-op path is tested, and the active
      path is proven against a fake client (spans, ACL counts, usage, trace io).
- [ ] [Alex] Langfuse Cloud account; set `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
      / `LANGFUSE_HOST` as secrets (Keychain via `secrun`, like askrepo-live)
- [ ] [Alex] Per-org dashboard of volume, cost, and latency; note the first week's
      trend and put a screenshot and link in the README

**Phase 9 complete except the [Alex] Langfuse account.** 80 tests green. The
distinctive touch: the retriever span carries the org-total and caller-allowed
chunk counts, so the permission filter that Phase 3 enforces is now visible per
request in the trace, not just asserted in tests.

The build is feature-complete: all ten phases are done in code and green in CI.
What remains is external-account work (the fly deploy and the Langfuse account),
both prepared and documented, both [Alex].

## Hardening pass (post Phase 9)

A review pass looking for what a senior reviewer would find first. Each item was
measured rather than assumed.

- [x] **Connection pooling**, with the tenancy bug it exposes. The RLS tenant GUC
      was session-scoped, which is invisible with a connection per request and a
      cross-tenant leak the moment connections are reused. Now transaction-scoped,
      with a test that borrows until the pool reuses a connection and asserts it
      carries no tenant context. The policies also `nullif` the cleared setting,
      because a reverted GUC is an empty string rather than NULL.
- [x] **Prompt injection.** Retrieved documents are attacker-controlled input.
      They are now delimited as untrusted content, the system prompt states that
      context is data and not instructions, forged delimiters are neutralized, and
      an eval gates merges on it.
- [x] **Architecture diagram** in the README (mermaid, renders on GitHub), showing
      the request path and the three isolation layers.
- [x] **Retrieval at scale, measured** (`python -m knowledge_desk.bench`). 100k
      chunks, 1024-dim, this laptop:

| configuration | p50 | p95 | plan |
|---|---|---|---|
| no vector index, RLS on | 569 ms | 2272 ms | seq scan |
| HNSW index, RLS on (this system) | 575 ms | 595 ms | seq scan, index unused |
| HNSW index, RLS bypassed | 367 ms | 392 ms | index scan |

  The result is negative and worth keeping: **row-level security stops the planner
  from using the vector index** for our access-scoped query. Same query, same data,
  same session settings, a role that bypasses RLS uses the index and the app role
  does not. Marking pgvector's `cosine_distance` LEAKPROOF was tested and did not
  change the plan. The index also took bulk loading from 28s to 355s, so under RLS
  it is pure cost. It was added (0010), measured, and dropped again (0011). We keep
  RLS and accept the scan; the escape hatch is documented in WALKTHROUGH.md.

  Kept from that work: the ACL is denormalized onto `chunks`, because filtering on
  the joined `documents` table defeated the index even before RLS did, and the
  chunk-local filter is the shape that would benefit if the index ever returns.

- [x] **Lint and type checking in CI.** Ruff (lint only, not the formatter, which
      collapses deliberately split SQL into unreadable one-line concatenations) and
      mypy, both clean and both gating. Typing the pool as `Connection[DictRow]`
      removed 48 of 74 mypy errors by itself; the rest were unchecked `fetchone()`
      results, now unwrapped by `require_row` at the sites where a row is genuinely
      guaranteed (inserts with RETURNING, aggregates) and left as explicit None
      checks where matching nothing is a real outcome.
- [x] **Dependency hygiene.** Dependabot for pip, npm, Actions, and Docker, plus
      advisory `pip-audit` and `npm audit` steps.
- [x] **Pagination** on the document, member, and audit listings (`limit`,
      `offset`, bounded 1 to 500), with tests for bounds and disjoint pages.

## Open decisions to revisit

- Job queue stays Postgres-backed until a phase actually needs concurrency it
  cannot give; only then consider arq or RQ. Note the trigger when it happens.
- Groups are flat in v1. Nested groups and inherited ACLs are a later concern;
  record the moment flat groups stop being enough.
- Auth stays built-in until a reviewer or a real user needs SSO; the OIDC seam is
  left open but unbuilt.
- **Session token lives in `localStorage`**, which any successful XSS can read.
  The alternative, an httpOnly cookie, moves the risk rather than removing it:
  the token becomes unreadable to script but the browser then attaches it
  automatically, so the app needs CSRF protection it does not need today, and
  the SPA loses the ability to talk to an API on another origin without cookie
  and CORS gymnastics. Neither option is strictly better. Keeping it explicit:
  the current choice trades XSS exposure for a simpler, origin-independent API,
  and it is the wrong trade the moment this app renders untrusted HTML or
  embeds third-party script. Revisit then, and revisit before any real customer
  data lands.
- **Dependency audits are advisory** (`continue-on-error`) rather than blocking,
  because a fresh CVE in a transitive pin should not stop unrelated work on a
  portfolio project. Promote to a hard gate once the noise level is known.

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
- **2026-07-27**: the container runs only migrations, not `check_setup`, so the
  pgvector extension was never created and migration 0002's `vector(1024)` column
  failed with "type vector does not exist" (the api container exited 1 on boot).
  Fix: a `0000_extensions.sql` migration creates the extension, making migrations
  self-contained. Lesson: anything the app needs at runtime must be a migration,
  not a preflight-only step. (Adding api/worker to compose also meant the plain
  `docker compose up -d` in the test job started them too; scoped it to `db`.)
- **2026-07-27**: local Docker could not pull `node:20-slim` (registry pulls hang,
  the same machine-level flakiness as askrepo-live's gotcha log), so the image was
  never built on this laptop. CI is the fresh-machine proof instead, and a stronger
  one: the `compose` job builds from scratch and runs the full stack every push.
- **2026-07-25**: a plain Python list of floats binds as `double precision[]`,
  which has no `<=>` operator against `vector`, so a similarity query fails with
  `UndefinedFunction` even though inserting the same list into a `vector` column
  works (the column supplies the type there). Wrap query vectors in pgvector's
  `Vector`. In pgvector 0.3.6 it imports from `pgvector.psycopg`, not `pgvector`.
- **2026-07-25**: incremental resync is worth the content-hash bookkeeping. An
  unchanged 25-file corpus resyncs in ~12ms versus ~1377ms for the first drain,
  because the hash match skips chunking and embedding entirely. At real Voyage
  latency and cost the gap is far larger, since the skipped work is the network
  embed calls, not the local hash.
