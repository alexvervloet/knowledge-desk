# Changelog

Notable changes to Knowledge Desk, newest first.

This project has no tagged releases. It was built in numbered phases, each
ending in something checkable, and the sections below follow that arc rather
than a version series. Dates are when the work landed on `main`. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the categories
are the standard ones, and **Security** means a change to what an attacker can
do, not merely a change to security-adjacent code.

The migration files carry phase numbers in their comments as a record of when
each was written. The sections below name which phases those were.

## 2026-09-01 — Model pricing was wrong, and wrong silently

Found while building model-swap, which compares what the same workload costs on
different models and needs these numbers to be right.

### Fixed

- Claude Sonnet 5 was priced at $3.00/$15.00 per million tokens. It is
  $2.00/$10.00. Every Sonnet answer was billed 50% high, and because
  `finalize_answer` writes this figure to the column the per-org rolling budget
  and the platform daily cap are summed from, a Sonnet org's effective budget
  was a third smaller than the number in its settings.
- Claude Haiku 4.5 had no entry at all.
- `_cost` fell back to Opus rates for any model not in the table, silently. An
  operator setting `answer_model` to anything unlisted got numbers that looked
  right and were not, in whichever direction the real price happened to lie.
  The fallback now bills at the dearest known rate and logs a warning naming the
  model. Dearest rather than cheapest on purpose: over-counting stops a customer
  early, which they can ask about, while under-counting spends money they never
  agreed to.

The rates came from the current Anthropic pricing table rather than from
memory, which is how the Sonnet error was visible at all.

## 2026-09-01 — A deleted tenant no longer burns its owner's email

Found while building [model-swap](https://github.com/alexvervloet/model-swap),
which loads a corpus into a throwaway tenant and resets it between runs. The
reset deleted the tenant and then could not recreate it.

### Fixed

- `delete_org` cascades every org-scoped table but left the `users` rows
  behind, because users are deliberately not org-scoped: one person can belong
  to several orgs. For anyone whose only membership was that org, what survived
  was an account with no memberships. Nothing could log into it, since login
  resolves through a membership, and its email was refused for every future
  signup. Deleting a tenant permanently burned its owner's email address.
- `TenantScope.remove_member` had the same defect. Removing someone from their
  only org stranded the same row, so an admin who removed a member by mistake
  could not add them back.

Both now call `accounts.purge_stranded_users`, which deletes only the users the
caller just affected and only when no membership remains. A member of another
org keeps their account, which is the reason users are global in the first
place. Audit history survives either way: `audit_log.actor_user_id` is
`on delete set null` precisely so a user can be erased without erasing the
record of what they did.

This does not weaken the refusal `add_member` documents. Refusing an email that
belongs to a live account is right, because nothing in an admin's request is
evidence the account holder agreed to anything. A row with no memberships is not
an account, and nobody is protected by keeping it.

Four tests. Two fail without the fix; the other two are guards against it going
too far, checking that a member of a second org survives a tenant delete and
that a removed member cannot log in with their old password.

## 2026-08-30 — Six gaps in the static analysis, closed

An audit of what CI actually enforced. Ruff and mypy were doing real work; the
frontend had no linter, nothing scanned for secrets, and nobody knew what the
212 tests reached.

### Security

- Secret scanning with gitleaks, as a hard gate rather than an advisory one. The
  job takes the full history instead of the shallow checkout, so a key added and
  reverted inside a branch is still caught. All 311 commits scan in under a
  second and came back clean.
- Ruff's flake8-bandit rules (`S`) are on. Six findings, all benign, now carry a
  `noqa` recording why: two on `check_links.py`'s fixed-argv git call, three on
  seeded generators that must stay deterministic, one on the demo seed password.
  Tests ignore the assert and fixture-password rules, which would bury the rest.

### Added

- oxlint on the frontend, with type-aware rules and `react-hooks`. ESLint could
  not be used: typescript-eslint hard-refuses TypeScript 7, and its workaround is
  a second TypeScript installed alongside purely to lint. oxlint parses TS itself
  and takes type information from `oxlint-tsgolint`, which tracks the TS 7
  compiler. It found four unawaited promises in mount effects and a `setState`
  the App could decide before its first render.
- Coverage measurement with a floor of 85%. The measured figure is 88%, once
  `bench.py` is excluded as the developer harness it is. `worker.py` at 0%,
  `migrate.py` at 70%, and `tracing.py` at 73% are where the gap is.
- `ruff format --check` in CI. Formatting had never been enforced, and 33 of 53
  files had drifted.

### Changed

- mypy runs in strict mode. The previous settings were a documented compromise
  from a codebase not written mypy-first, and the remaining distance turned out
  to be 33 errors in 9 files: bare `dict` returns on the handlers, four
  unannotated functions, and `count(*)` coming back as `Any` through `DictRow`.

The dependency audits stay advisory on purpose. That was a considered choice
when they were added and the reasoning has not changed.

## 2026-08-30 — Citation evidence, and anchors that resolve to symbols

The last open security item, plus the tooling for a docs problem that had cost
three manual repair passes.

### Security

- Pin each cited claim to a quote from the passage it cites. Citation existence
  was checked and citation honesty was not: a model that reads a forged policy
  can attribute it to the real key of the passage that carried it, so the key
  check passes and the false claim reads as sourced. The system prompt now asks
  for a short verbatim quote after each `[n]`, and `check_answer` verifies the
  quote appears in that passage, comparing on whitespace-collapsed, case-folded
  text so line wrapping is not a false positive.
- A citation carrying no quote is reported as well. Without it, a model that
  quietly stops quoting disables the check and every finding keeps reading green.

This detects detached, invented, and stale citations. It does not prove
entailment, and `outputchecks.py` says so: a quote can be real, in the right
passage, and still not support the sentence built around it.

### Added

- `scripts/anchors.py`. `check_links.py` resolves link targets, so a line anchor
  passes whether or not it still points at the right code. A markdown link title
  now records which symbol a range means, resolved with `ast`, and CI gates on
  it. `--fix` repoints a moved symbol, `--tidy` pulls boundaries off blank lines,
  `--adopt` records a symbol where a range already matches one exactly.

### Fixed

- Nine stale doc ranges no previous check could see, `_SYSTEM` worst among them:
  the citation-evidence edit grew it past its anchor, leaving three documents
  pointing at a prompt cut off mid-string.

### Changed

- The mock provider quotes its cited passage, so the keyless path exercises the
  answer shape the output checks verify.

## 2026-08-30 — Grammar defusing, folding, and output checks

The four gaps the previous two entries named and left open, closed. Same sources:
the DeepDives prompt-injection and GenAI-security chapters.

### Security

- An empty ACL denies instead of defaulting to the whole org. `acl = item.get("acl")
  or DEFAULT_ACL` cannot distinguish an absent ACL from an empty one, so an upload
  naming `[]` was handed `["public-to-org"]`. The request asking for the tightest
  permission available got the loosest. Absent still takes the default; empty is
  stored as given and matches no principal.
- Defuse the prompt's whole grammar, not only the fence markers. A passage
  containing `[2]` could attribute its own claims to a real passage the asker was
  allowed to see, and a citation check validates that because the key exists. A
  `path:` line inside the fence forged our own label. Both are defused; the cost
  is that genuine footnote markers in a document go with them.
- Match after folding. A Cyrillic О or a zero-width space spells a marker that
  reads identically and compares unequal, and every marker pattern was reading
  raw bytes. Replacement still lands on the original text through an offset map,
  so the document handed to the model is the document that was uploaded.
- Paths reject invisible characters as well as control characters.

### Added

- `outputchecks.check_answer`: citations outside the retrieved range, the fence
  coming back out of the model, the system prompt repeated, and markdown images
  or links. Detectors rather than a gate, because the answer streams; findings
  ride out in the `done` frame and land in the audit log. A bare URL in prose is
  deliberately not detected, and a test asserts that so the omission stays a
  decision rather than an oversight.
- The number of grammar forgeries defused per question is written to the audit
  log. A corpus where that is nonzero and rising is one somebody is writing into,
  and nothing else here would surface it.
- An `output-checks` eval, and tests for the folding and its offset map.

### Changed

- The `done` SSE frame carries a `warnings` array, and the UI shows it under the
  answer.

## 2026-08-30 — Per-request fence around retrieved passages

Follow-on to the review above, applying two controls the
[DeepDives](https://github.com/alexvervloet/DeepDives) prompt-injection and
GenAI-security chapters name explicitly and this project did not have.

### Security

- Fence untrusted passages with a per-request nonce. The markers now carry digits
  minted for the request and named in the user turn. A fixed delimiter is one the
  attacker can simply type; a document written before the request that retrieves
  it cannot contain a value that did not exist yet. This is the boundary the
  previous pass's neutralization was standing in for.
- Move the document path inside the fence. Only the minted `[n]` citation label
  stays outside now. Neutralizing the path made the previous attack fail, but the
  path was still rendered in the one region a fence cannot protect, which is a
  property of the assembly rather than of the fence.
- Match marker *shapes* rather than two exact strings. A model will honour a
  close marker that is merely close enough, and four of five near-miss spellings
  (spaced, lowercased, trailing space, a different dialect) passed through the
  exact-match version untouched.

### Added

- `unfenced_untrusted`, which asks which uploader-supplied values appear in the
  part of the prompt the fence does not cover, matching on any run of 24
  characters so a truncated quote is caught as well as a whole field. The
  per-field evals gate the two fields they name; this gates the property.
- A `fence-integrity` eval covering both of the above, plus an assertion that the
  markers actually depend on the nonce. That last one was added because without
  it the entire per-request boundary could be deleted with every eval still
  passing (see LESSONS #35).

### Changed

- Exercise 2 is rebuilt around the two deletions and what each one does to the
  gate, since its original edit no longer fails anything.

## 2026-08-29 — Security review remediation

A review of prompt-injection defense and general application security raised
five findings. All are fixed here, each reproduced against the running code
before the fix and covered by a test that fails without it.

### Security

- Neutralize the document path when building the answer prompt. `_render_context`
  neutralized a passage's text but interpolated its path raw, on the citation
  line *outside* the untrusted-content markers. A path is uploaded text with the
  same provenance as the content, so a forged closing marker there did not merely
  close the fence early — it landed the payload where the model reads
  instructions, which is the one region the system prompt's "passages are data"
  rule does not claim to cover. A path carrying both markers could also forge an
  extra `[n]` passage citing a file the asker was not permitted to see.
- Refuse control characters in an uploaded document path. Second, independent
  defense for the same vector: a newline needs no marker to escape the citation
  line, and a real file path has no use for one.
- Refuse to add a member whose email already has an account. `add_member` reused
  the existing user row and inserted a membership, silently discarding the
  password in the request, so any org admin could attach a stranger's account to
  their own tenant without knowing anything about it. It also broke the victim's
  slugless login by making their membership ambiguous. Joining an existing
  account to a second org needs an invitation flow, which does not exist; until
  it does this is a 409 that says so.
- Send security response headers on every reply. The built SPA is served
  same-origin from the API, so the page holding the session token had no CSP, no
  `nosniff`, no framing rule, and no referrer policy. `script-src` is `'self'`
  with no inline escape, which the Vite build already satisfies.
- Resolve a group invitation's email inside the org. The route resolved the
  address globally and checked membership afterwards, producing two
  distinguishable 404s — one for an address with no account anywhere, one for an
  address with an account in another tenant. Any org admin could use it to probe
  for accounts across the whole platform.

### Changed

- Redact PII from everything sent to Langfuse: question, answer, and document
  paths. The same text is stored unredacted in Postgres deliberately, and that
  argument rests on the reader already being an admin of the asker's own org,
  which does not hold for a third-party service. Answers are redacted after the
  stream is joined, because a pattern straddling two tokens is invisible in
  either alone. Traces now tag the user by id rather than email address.

### Added

- A second injection eval covering the document path, and unit tests for the
  prompt boundary. The existing eval only ever put its payload in the content
  field, so the path vector could regress without failing the build.

## 2026-08-10 — Audit remediation

An audit of code quality, resilience, security, structure, and documentation
raised seventeen findings. All of them are addressed here, each reproduced
against the running app before the fix and re-checked after, along with one bug
the audit missed that surfaced while fixing another.

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
- Re-ingest a document that comes back after being dropped. Ingest jobs are
  idempotent by document and content hash, and dropping a document deletes its
  chunks, so re-uploading the identical file produced the identical key,
  collided with the succeeded job from before the deletion, and enqueued
  nothing — leaving the document at `pending` with no chunks, permanently
  invisible to retrieval. Found while fixing the storage leak below rather than
  by the audit. A revision counter, bumped when chunks are destroyed, expresses
  what the content hash could not.
- Release a dropped document's bytes. The row kept its text while
  `storage_usage` stopped counting it, so upload-then-drop in a loop grew the
  database without limit while the usage meter read zero, and content a tenant
  believed they had deleted was still there.
- Enforce the storage caps inside the write transaction. They were read in one
  transaction and the documents written in another, so two concurrent uploads
  could each see room only one of them had.
- Evict idle rate-limiter buckets, which were kept for the life of the process,
  one per user id or client address.

### Added

- `POST /me/password`, and an Account tab in the UI. There was no way to change
  a password at all, so a member added by an admin was stuck with whatever that
  admin chose. Self-service only, deliberately: an admin who could reset an
  owner's password could sign in as them. Changing it revokes the user's other
  sessions and keeps the caller's own.
- A request body size limit at the ASGI layer (`MAX_REQUEST_BYTES`), applied
  before the body is read. The upload caps were enforced only after FastAPI had
  parsed the whole request, so a 60 MB payload against a 50 MB cap took peak RSS
  from 66 MB to 373 MB before returning its 413. Measured after: no growth.
- Expired session purging, run hourly from the worker.

### Changed

- Every role gate moved into `TenantScope`. They had been split between the
  route layer and the data layer with no rule saying which lived where, so
  reading `main.py` gave a wrong picture of the authorization model — the exact
  property the tenancy design exists to avoid. Six of ten admin-only operations
  were unguarded when called as a method rather than through their endpoint.
  `DELETE /org` keeps its gate at the route, being an account operation rather
  than a scoped one, and says so.
- Chunk inserts use one `executemany` instead of a round trip per chunk.
- Request and response bodies moved out of the route module into `schemas.py`.
  They are the trust boundary, and their bounds read better as one policy than
  scattered between the handlers they belong to.
- Documented two deliberate asymmetries that had looked accidental: why `jobs`
  is the one `org_id`-carrying table without an RLS policy, and why question
  text is stored unredacted while audit detail is not.

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

- Frontend tests, and `npm test` in CI, which previously ran a build and
  stopped. The SSE frame parser and the pager arithmetic are the two places
  with real logic in them; the parser tests feed frames split mid-JSON and one
  byte at a time, because the server's framing does not survive TCP.

### Removed

- `GET /collections/{name}`, Phase 0 scaffolding that nothing called.
- A duplicated tear-down step in the compose CI job.

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
