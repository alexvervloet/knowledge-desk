# Changelog

Notable changes to Knowledge Desk, newest first.

This project has no tagged releases. It was built in numbered phases, each
ending in something checkable, and the sections below follow that arc rather
than a version series. Dates are when the work landed on `main`. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the categories
are the standard ones, and **Security** means a change to what an attacker can
do, not merely a change to security-adjacent code.

## 2026-08-10 — Audit remediation

An audit of code quality, resilience, security, structure, and documentation
raised seventeen findings. This covers seven of them, each reproduced against
the running app before the fix and re-checked after.

### Security

- Refuse to grant a role above the caller's own. `POST /members` validated the
  requested role against the `owner|admin|member` pattern and nothing else, so
  an admin could create an owner account with a password of their choosing, log
  in as it, and hold every owner power including the irreversible `DELETE /org`.
  `set_member_role` had the same hole from the other side. The admin UI only
  ever offered `member` and `admin`, so the restriction existed on screen and
  nowhere a caller with `curl` had to care about.
- Throttle `/auth/login` and `/auth/signup`. The token bucket had only ever sat
  in front of `/ask`, an endpoint unreachable without a session, so the two
  routes an anonymous caller can reach took requests as fast as they arrived —
  a brute-force channel, and a way to spend the machine's CPU from outside,
  since each attempt costs a bcrypt verification.
- Close the account-existence timing oracle on login. `verify_password` was
  skipped entirely when no user row came back, so a failed login finished in
  about 4 ms for an unknown email against roughly 240 ms for a known one. Both
  branches now hash.
- Stop streaming exception detail to the caller. An unexpected failure put
  `str(exc)` into the SSE frame the browser renders, so a database error handed
  the caller its host name and the role the app connects as. The detail goes to
  the log under a short reference and the caller gets the reference.

### Fixed

- Export the whole tenant. `/org/export` called the member and document
  listings with no arguments and inherited the `limit=100` default they grew
  when pagination landed, so a tenant with more than 100 of either received
  well-formed JSON that was quietly incomplete. It now pages to exhaustion.
- Bill a stream the client abandons. Usage was recorded only when the
  provider's final usage frame arrived, after the last token, so a client that
  disconnected one frame earlier left the answer at zero tokens and zero
  dollars while the model had already generated the response. Aborting each
  request just before the end was a way to spend without ever being billed.
  What was streamed is now estimated and booked.
- Fail ingestion rather than silently dropping chunks. An embedder returning
  fewer vectors than it was given texts was zipped short, leaving the document
  marked `ingested` while holding a subset of its chunks — a permanent hole in
  retrieval, invisible at every layer, that nothing downstream would ever see a
  reason to retry. It now raises, so the job retries and dead-letters visibly.

### Added

- `platform_spend`, a deployment-wide daily spend total, and
  `PLATFORM_DAILY_BUDGET_USD` to cap it. Every existing cost control was scoped
  to one org, and signup is unauthenticated and hands out a tenant with a fresh
  set of all of them, so the worst case was never the per-org budget — it was
  that budget times however many times someone posts to the signup form.
- `answers.usage_estimated`, so usage inferred from an interrupted stream is
  never presented as a provider-reported measurement.
- `CLIENT_IP_HEADER`, `AUTH_RATE_BURST`, and `AUTH_RATE_PER_MIN` settings. The
  header names where to read the caller's address behind a proxy, where the
  socket peer is the proxy and every visitor would otherwise share one bucket.

### Changed

- The public demo caps a day's spend at $3.00 regardless of how many orgs are
  created, and throttles auth to a burst of 5.
- `LESSONS.md` gains entries 24–28: a UI-only rule is not a rule, a default
  argument reaches call sites that never asked for one, stream metering is
  optional from the client's side, the limiter was aimed at the door that
  already needed a key, and per-tenant caps do not bound a bill when tenants
  are free.
- `WALKTHROUGH.md`'s "Where it will disappoint you" now names the operational
  limits alongside the product ones, including what this pass deliberately did
  not fix.

## 2026-08-09 — Deployment and dependency maintenance

### Fixed

- Provision the app role from `APP_DATABASE_URL` before migrating, so a managed
  database never inherits the throwaway password the RLS migration falls back
  to. The provider's default role carries `rolbypassrls`, which would have
  silently removed the third isolation layer in the only environment that
  matters.
- Close the connection pool when the seed script exits. Python 3.14 refuses to
  join the pool's worker threads at interpreter shutdown; 3.13 exited quietly.
- Import `Vector` from the pgvector top level, which relocated in 0.5.0.

### Changed

- CI tests on the interpreter the image ships, closing a gap that opened when
  the base image moved to 3.14 while CI stayed on 3.13.
- Dependabot patch and minor updates auto-merge once CI is green; majors are
  left open, because in this repo they have needed judgment every time.
- Upgraded React 19, Vite 8, TypeScript 7, pytest 9, bcrypt 5, Node 26, Python
  3.14, and the GitHub Actions.

## 2026-08-03 — Hardening, performance, and tooling

### Fixed

- Set the tenant GUC transaction-scoped rather than session-scoped. A
  session-scoped setting survives the commit and rides the connection back into
  the pool, so the next request to borrow it would inherit the previous
  tenant's org context.
- Deny by default when the GUC is an empty string rather than NULL, which is
  what a reverted transaction-scoped setting leaves behind.

### Security

- Treat retrieved documents as untrusted data: an explicit untrusted-content
  boundary in the prompt, forged delimiters neutralized, and a merge-gating
  prompt-injection eval that asserts the structural property rather than a
  model's wording.

### Added

- Pagination on the document, member, and audit listings, with `X-Total-Count`
  and a pager that hides itself when everything fits.
- A retrieval scale benchmark that seeds, times, and prints the query plan, and
  can A/B the vector index.
- Ruff, mypy, and advisory `pip-audit` / `npm audit` in CI; Dependabot across
  pip, npm, Actions, and Docker.

### Removed

- The HNSW vector index. Measured at 100k chunks, the planner does not use it
  for an access-scoped query while row-level security is enforced — the same
  query as a role that bypasses RLS does use it. Under RLS it bought nothing on
  reads and cost an order of magnitude on writes. Keeping RLS and accepting the
  scan is the deliberate trade; the escape hatch is documented.

## 2026-07-29 — Documentation

### Added

- `WALKTHROUGH.md`: an end-to-end trip through the app, with branch points,
  gotchas, and an honest account of where it disappoints.

## 2026-07-28 — Phase 9: observability

### Added

- One Langfuse trace per question, tagged by org and user: a retriever span
  recording how many of the org's chunks the caller was allowed to see (the ACL
  filter, made visible) and the answer as a generation with token usage and
  cost. Every tracer call is exception-proof and a no-op without keys, so
  observability can never take the product down.

## 2026-07-27 — Phases 7 and 8: admin surface, UI, and deployment

### Added

- Admin routes: member role changes and removal with their guards, group
  membership management, document ACL editing, and a usage summary.
- A React + Vite + TypeScript SPA: auth screen, role-gated tabs, streaming ask
  view with citations and feedback, sources view with upload and ACL editing,
  and a members and usage dashboard.
- The API can serve the built SPA same-origin, so production is one container.
- An idempotent demo seed with two orgs holding non-overlapping documents.
- A compose end-to-end CI job that builds the stack, seeds, signs up, and asks.
- `LESSONS.md`.

## 2026-07-26 — Phases 4, 5, and 6: assistant, operational controls, governance

### Added

- The assistant: retrieve within the caller's permissions, then stream a
  grounded answer over SSE. If nothing the caller may see comes back, it
  refuses rather than answering from the model's own knowledge, so the access
  boundary carries through to the generated text.
- Streaming answer providers, mock and Claude, both reporting token usage and a
  cost estimate. The mock is loud by design.
- Per-org rolling budget and monthly question caps, enforced before the model
  runs, with the blocked question recorded rather than silently dropped.
- A per-user token-bucket rate limiter and per-org ingest storage caps.
- An append-only audit log an org admin can query, with PII redacted from
  detail before storage.
- PII detection at ingest, surfaced in listings as a visibility signal.
- Document deletion, tenant export, and tenant deletion.
- Row-level security on every org-scoped table, enforced through a
  least-privilege `kd_app` role — a superuser or table owner bypasses RLS even
  with FORCE, so the split is what makes the layer real.
- Merge-gating evals for permission leak and grounded answer, wired into CI.

## 2026-07-25 — Phases 2 and 3: ingestion and permission-aware retrieval

### Added

- A durable Postgres-backed job queue using `SELECT ... FOR UPDATE SKIP
  LOCKED`, with retries, exponential backoff, dead-lettering, and idempotency
  by content hash.
- Ingestion: hash-based reconcile that re-embeds only what changed, marks
  deletions, and runs embedding off the request path behind the queue.
- Mock and Voyage embedders, both 1024-dimensional, with a deterministic mock
  so ingestion is reproducible with no keys or network.
- Access-scoped vector search. The ACL filter lives inside the candidate fetch,
  so a forbidden chunk is never ranked, never scored, and cannot leak through a
  missed post-filter. Principals are recomputed per query, so a group change
  takes effect on the next question with no cache to invalidate.

### Fixed

- Model embedding failure with a storable sentinel: Postgres text columns
  cannot hold a NUL byte, so binary content fails at the connector boundary.

## 2026-07-24 — Phases 0 and 1: skeleton and the multi-tenant spine

### Added

- FastAPI application, plain-SQL migration runner, and a preflight check.
- Configuration with provider mode derived from which keys are present, so the
  keyless mock path is the default rather than a special case.
- The tenancy spine: orgs, global user identities, memberships with roles,
  groups, and sessions.
- `TenantScope`, the single choke point that stamps `org_id` onto every
  org-scoped query, so cross-tenant leakage is a code-review target with one
  place to look instead of a property spread across every handler.
- bcrypt password hashing over a sha256 pre-hash, so passwords longer than
  bcrypt's 72-byte input limit are not silently truncated, and opaque session
  tokens stored only as their sha256.
- CI: compose, preflight, migrations, and pytest.
