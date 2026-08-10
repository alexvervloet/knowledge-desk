# LESSONS

Engineering lessons from building a multi-tenant, permissions-aware knowledge
assistant: the operational layer around an LLM app, not the retrieval technique.
Most of these are about tenant isolation guarantees, the places Postgres behaves
differently than you assume at the edges, and the gap between "filtered in
application code" and "enforced by the database." Each lesson is tied to the
concrete moment that taught it. Running log, oldest first.

---

## 1. Give cross-tenant leakage one place to be a bug

Every org-scoped read and write goes through a single `TenantScope` object that
stamps and filters `org_id` on each query. The point is not tidiness. Tenant
isolation is the kind of property that fails silently and catastrophically, and
spreading the `org_id` filter across forty handlers means forty chances to
forget it and no single place to audit. With one choke point, "can this query
leak across tenants" becomes a question you answer by reading one file, and a new
query that touches org data and is not a method there is visibly the thing to
review.

Takeaway: for a property that must hold everywhere, a single chokepoint is worth
more than the sum of forty careful handlers. Make the dangerous thing have one
address.

## 2. Postgres text columns cannot hold a NUL byte

The plan was to capture a document's content, then let its embedding job fail if
the content was binary garbage. It never got the chance: a string with a `\x00`
in it fails to `INSERT` into a `text` column with a `DataError`, before any
application code runs. So the "poison document" I meant to test could not even be
stored. Two consequences fell out of that one error. Binary filtering belongs at
the connector boundary, where files are read, not at the embedder. And the
worker-side failure actually worth testing is a provider rejecting an input, not
a byte the database refuses outright, so the mock embedder now fails on a
storable sentinel instead.

Takeaway: know what your storage layer rejects before you design a failure mode
around storing it. The edges of "just a text column" have opinions.

## 3. Idempotency by content hash makes the queue safe and resync nearly free

The ingest queue is Postgres-backed and at-least-once: a job can be delivered
twice. That is only safe because the unit of work is idempotent. Each job's
idempotency key is `ingest:<doc_id>:<content_hash>`, so re-enqueuing the same
bytes is a no-op, and a re-upload of an unchanged corpus re-embeds nothing. The
same hash bookkeeping that makes retries safe also made resync cheap: an
unchanged 25-file corpus resynced in 12ms against 1377ms for the first drain,
because the hash match skips chunking and embedding entirely.

Takeaway: content-addressing is one decision that buys two properties. Make the
work idempotent and both "safe to retry" and "cheap to skip" come for free.

## 4. Access control belongs in the candidate fetch, not a post-filter

The permission-leak test seeds a secret only user X may see and asserts user Y
can never retrieve it, for any query. The tempting implementation is to retrieve
normally and drop forbidden results afterward, but a post-filter is one missed
branch away from a leak, and a leak here is the whole product's credibility. So
the ACL check lives inside the SQL that fetches candidates (`d.acl ?|
principals`), which means a forbidden chunk is never scored, never ranked, and
never present to be accidentally returned. The guarantee becomes structural
rather than dependent on every later code path remembering to filter.

Takeaway: a safety filter you apply after retrieval is a filter a future bug can
skip. Push it into the query that produces the candidates, so the forbidden rows
never exist in the result to begin with.

## 5. A Python list is `double precision[]`, not `vector`

Inserting a list of floats into a `vector` column worked fine for two phases.
Then the first similarity query failed with `operator does not exist: vector <=>
double precision[]`. The list had been binding as a float array all along; on
`INSERT` the target column supplied the type and Postgres coerced it, but in a
bare `embedding <=> %s` expression there is no column to infer from, so the
parameter stayed a float array and no operator matched. The fix is to wrap query
vectors in pgvector's `Vector` so they bind with the right type OID. (And in
pgvector 0.3.6 that class imports from `pgvector.psycopg`, not `pgvector`.)

Takeaway: a value that "obviously" has a type may only get it from context. When
the same binding works in one position and fails in another, suspect that the
first position was quietly supplying the type the second one lacks.

## 6. Recompute permissions per query instead of caching them

A user's principal set (their id, plus a principal per group they belong to) is
rebuilt from the database on every single search. The obvious optimization is to
cache it, and the obvious bug that follows is a revoked group membership that
keeps working until the cache expires. Because the set is recomputed each time,
removing a user from a group revokes their retrieval access on the very next
query, with no cache to invalidate and no staleness window to reason about. The
per-query query is cheap; the correctness it buys is not.

Takeaway: permissions are the last thing you should cache. A stale allow is a
security bug, and "recompute it every time" is often fast enough to make the
whole class of staleness bugs impossible.

## 7. Enforce the spend cap before the model runs, and record the refusal

The per-org budget and question caps are checked before any model call, so a
blocked request never reaches the provider and never costs money. But the block
still writes an answer row marked `blocked` and an audit event, so a user who
hits their cap shows up in the ledger and the dashboard rather than vanishing.
The two limits also express themselves differently on purpose: the per-user rate
limit is an HTTP 429 before the stream opens, while an over-budget org gets a
loud limit frame inside the SSE stream, because by then the client is already
reading a stream and deserves a message in the channel it is listening on.

Takeaway: a guardrail that silently drops work is a guardrail that generates
support tickets. Stop the expensive action early, but still record that it was
asked for, and signal the refusal in whatever channel the caller is already
watching.

## 8. Row-level security is only real against a non-superuser

I enabled `FORCE ROW LEVEL SECURITY`, wrote the policies, set the org GUC, and a
raw query with no org context still returned every row. The reason is that the
compose Postgres user is a superuser, and superusers bypass RLS unconditionally,
FORCE or not. RLS is not a thing you turn on; it is a thing that applies to a
role that is neither the table owner (which FORCE covers) nor a superuser (which
nothing covers). The fix was the real production pattern: migrations run as the
owner, but the app connects as a dedicated least-privilege role, and only then do
the policies bite. The reward is a test that proves a query with no org context
returns nothing, which is a much stronger statement than "the application always
remembers to filter."

Takeaway: RLS is a property of the connecting role, not of the table. If your app
connects as a superuser or the table owner, your policies are decoration. Give
the app its own unprivileged role and verify the deny from a raw connection.

## 9. Defense in depth means the guarantee survives any single layer's bug

By the end, tenant isolation was enforced three independent times: the data
layer's `org_id` filter in application code, the ACL check inside the retrieval
candidate fetch, and Postgres RLS underneath. That is not redundancy for its own
sake. Each layer fails in a different way (a forgotten filter, a bad ACL query, a
missing GUC), and no single one of those failures leaks data, because the other
two still hold. The RLS layer in particular is the one that keeps holding when
the application code is wrong, which is exactly the case you cannot test your way
out of.

Takeaway: for a guarantee that matters, count how many independent things have to
be correct for it to hold, and aim for more than one. The layer that protects you
from your own future bug is worth its cost.

## 10. Anything the app needs at runtime is a migration, not a preflight

The container built, started, and immediately exited. The cause: schema creation
assumed the `vector` extension already existed, because in local dev and CI the
`check_setup` preflight had always created it first. The container runs only
migrations, not the preflight, so the `vector(1024)` column in an early migration
hit "type vector does not exist" and the API died on boot. Moving the `CREATE
EXTENSION` into a `0000` migration made the schema self-contained, and the
container stopped depending on a step that only ran in the environments I happened
to develop in.

Takeaway: a preflight script is a convenience for humans, not a dependency your
runtime can rely on. If the app needs something to exist, put it where the app
provisions itself (the migrations), not in the checklist you run by hand.

## 11. CI is the fresh machine, and it reviews your work better than you do

Local Docker could not pull base images the entire phase (the same registry
flakiness this laptop has shown before), so the image was never built here at
all. That turned out fine, because the CI compose job builds from scratch on a
clean runner and runs the whole stack (api, worker, db) end to end on every push,
which is a stronger fresh-machine proof than my laptop could ever be. It also
caught two bugs I would have shipped: the missing `vector` extension above, and a
demo org slug two characters under the validation minimum that returned a 422. I
did not find those; the end-to-end job did.

Takeaway: when the definition of done is "works on a clean machine," the clean
machine is CI, not your laptop with three years of accumulated state. An
end-to-end job that actually runs the product is a reviewer that never gets
tired.

## 12. The last mile of a deploy is an account decision, not an engineering one

The deploy was fully prepared: a multi-stage image, a two-process fly config,
release-command migrations, a seed script, all green in CI. It still could not
ship, for two reasons that no amount of code could fix. Fly's stock Postgres does
not include pgvector, so the database has to come from somewhere that does
(Neon), which is an account someone has to create. And the Fly trial had ended,
so creating any app at all requires a credit card on file. Both are five-minute
human steps and both are hard walls to an agent.

Takeaway: separate the parts of "deploy" that are engineering from the parts that
are billing and account provisioning, and surface the second kind early. The most
production-ready artifact in the world still waits on a credit card and a managed
database that has the extension you need.

## 13. Connection pooling can hand the next request your tenant context

Row-level security keys on a per-connection setting, and the original code set it
with `set_config('app.current_org', org, false)`. That third argument means
session-scoped, which was harmless while every request opened its own connection
and closed it. Adding a pool turned it into a cross-tenant bug: the setting
survives the commit, rides the connection back into the pool, and the next
request to borrow it starts already scoped to the previous tenant. Any query that
then forgot its own filter would read another org's rows. The fix is one boolean
(`true`, transaction-scoped, reverts at commit), and the regression test borrows
repeatedly until the pool hands back a connection it has already used.

Takeaway: connection-scoped state and connection pooling are a trap, because the
pool's whole job is to make connection reuse invisible. Before adding a pool,
inventory everything your code sets on a connection and ask what happens if the
next request inherits it.

## 14. Deny-by-default was relying on NULL, and a cleared setting is not NULL

Flipping that boolean immediately broke the RLS test, in an instructive way. The
policies compared `org_id = current_setting('app.current_org', true)::uuid`, and
with a fresh connection the setting was unset, so `current_setting` returned NULL
and the comparison matched nothing. That was the deny-by-default. A
transaction-scoped setting does not revert to unset, though; it reverts to the
empty string, and `''::uuid` raises rather than returning NULL. The policy went
from denying quietly to erroring loudly. Not a leak, but a 500 where an empty
result belongs, so the policies now `nullif(setting, '')` before the cast.

Takeaway: "no rows" and "invalid input" are different failures, and a security
rule that leans on NULL semantics deserves an explicit test for every state its
input can be in, including empty, not just set and unset.

## 15. Row-level security quietly forbids COPY

Seeding a benchmark corpus through the app's own least-privilege role failed with
"COPY FROM not supported with row-level security." Postgres refuses bulk loading
into a table with RLS enabled, full stop, because it cannot evaluate the policy
per row the way COPY streams data. The defense-in-depth that protects every
request also removes the fastest write path in the database, so bulk imports have
to run on an admin connection outside the policy, which is a real operational
seam to design rather than discover.

Takeaway: a security control applied at the storage layer constrains everything
that touches storage, including the maintenance paths you were not thinking about
when you enabled it. Check your bulk-load and backfill story before turning on
RLS, not after.

## 16. Retrieved documents are the untrusted input in a RAG system

The assistant read documents that users upload and passed their text straight
into the model prompt, unlabelled. That is the exact shape of an indirect prompt
injection: an attacker does not need to talk to the model, they only need to get
a document into the corpus. The hardening is layered, because no single measure
is reliable: the system prompt states that context is data and never
instructions, retrieved text is wrapped in explicit untrusted-content markers so
the boundary is locatable, and any occurrence of those markers inside a document
is neutralized first so a document cannot forge a closing delimiter and escape
into what looks like instruction space.

Takeaway: in a retrieval system, the documents are attacker-controlled input.
Delimit them, say so in the system prompt, and defend the delimiter itself,
because the first thing a serious injection tries is to close your fence.

## 17. Your security layer can silently cost you your index

Adding a vector index to a project that calls itself scalable felt like a
formality. It turned into the most interesting measurement in the build. With
100k chunks the unindexed search ran p50 569ms and p95 2272ms, scanning every
row. Adding an HNSW index changed the p50 by six milliseconds, because the
planner never used it. The same query, the same data, the same session settings,
run as a role that bypasses row-level security, used the index and returned in
367ms. RLS was quietly costing the index.

The investigation had two false leads worth recording. First I assumed the ACL
filter was the problem, and it partly was: filtering on the joined `documents`
table defeats the index on its own, because the predicate that decides which
rows survive lives on a different relation than the vector. Denormalizing the
ACL onto `chunks` fixed that half. Then I assumed the remaining blocker was
leakproofness, since pgvector's `cosine_distance` is not marked LEAKPROOF and
non-leakproof functions cannot be pushed below a security barrier. Marking it
LEAKPROOF changed nothing, so that tidy explanation was wrong too.

What made the decision easy was the write side. Bulk loading 100k chunks took 28
seconds with no index and 355 seconds to build the index afterward, and every
ongoing insert pays a graph insertion. Under RLS the index was buying nothing on
reads and costing an order of magnitude on writes, so it was added, measured, and
removed again in the same session, with the numbers and the escape hatch written
down.

Takeaway: measure the index, do not assume it. Defense in depth is not free, and
the honest version of "is it scalable" is a table with a row you did not want,
plus a documented decision about which property you are choosing to keep. A
negative result you can reproduce is worth more than a benchmark that flatters
the design.

## 18. Eleven red pull requests were one red commit

Dependabot's first run opened thirteen PRs and eleven were failing, which looks
like a dependency apocalypse and was actually a single cause: they had all
branched from a main whose lint job was broken, because three files with pending
ruff fixes had been committed a few minutes too late. Every one of those PRs
carried the same stale base and therefore the same failure. Rebasing them onto
current main turned seven green immediately. The tell was in the shape of the
failures, not their number: `lint` failed on all eleven, while the additional
failures clustered on exactly the PRs that had a real problem.

Takeaway: when a batch of independent branches fails together, read the failures
as a set before debugging any one of them. Look for the check that fails on all
of them, and suspect the base they share. Same instinct as three unrelated tools
hanging on the same network stack.

## 19. Dependabot splits changes that only work together

Four of the remaining failures were coupled updates that Dependabot had filed as
separate PRs, each unmergeable alone. React and react-dom must move as a set, and
so must their `@types` packages, so the react PR left `@types/react-dom` behind
and died on an unsatisfiable peer range while the react-dom PR failed the mirror
image. The vite plugin tracks the vite major the same way. A fifth, the python
3.14 base image, failed for a subtler version of the same thing: it was blocked
by `voyageai==0.3.7`, which caps at Python 3.13, so the image bump could not land
until a dependency in a different ecosystem moved first.

Landing all four frontend majors as one commit built clean on the first try. The
tool is not wrong to split them, it just cannot see the constraint.

Takeaway: a dependency bot proposes one change per package, but the unit that
actually works is one change per constraint group. When a bot's PR fails alone,
check whether it is half of something before treating it as broken.

## 20. Pinning exactly means every upgrade is a code change waiting to happen

The python group bump carried pgvector 0.3.6 to 0.5.0, which moved `Vector` out
of `pgvector.psycopg` and up to the package root. The import had already moved
once during this build, so this was the second time the same line broke, and the
type checker caught it before the tests did. Nothing about the version numbers
suggested an API change, and nothing in the PR description mentioned it: the
signal was a red CI job on a routine-looking dependency bump.

The write-up matters more than the fix. A one-line import change is trivial; the
thing worth recording is that this dependency relocates its main export between
minor versions, so the next upgrade should be assumed to break it again.

Takeaway: exact pins buy reproducibility and defer breakage rather than
preventing it. Budget for the upgrade being a code change, keep a type checker in
CI so the break surfaces as a compile error rather than a runtime one, and write
down the dependencies that have burned you before, because they will again.

## 21. A bot that opens pull requests needs something that closes them

The thirteen PRs sat open not because anything was wrong with them but because
nothing in the repo was responsible for merging them. Dependabot was configured
in one commit and then left without a counterpart, which is a half-built
automation: it generates work indefinitely and depends on a human noticing. The
fix was a workflow that merges patch and minor updates once CI is green and
leaves majors for review, which matches what actually happened here, where every
major needed a judgment call and none of the minors did.

Worth noting what the obvious implementation gets wrong. `gh pr merge --auto`
only waits for checks when the branch has required status checks, which means
branch protection, which means giving up direct pushes to main. Triggering on the
CI workflow completing instead gets the same gate without changing how the repo
is worked in.

Takeaway: automation that creates work is only half an automation. When you add a
bot, add the thing that resolves its output in the same change, and pick the
trigger that matches your existing workflow instead of reshaping the workflow
around the tool.

## 22. The managed database's default role bypasses your security model

Deploying to Neon, the obvious shortcut was to point the application at the role
the provider hands you, `neondb_owner`, and skip provisioning a second one. I had
even written that option into the runbook as an acceptable simplification, on the
reasoning that FORCE row-level security covers table owners. Checking before
deploying rather than after showed `rolbypassrls = true` on that role. Pointing
the app at it would have disabled RLS in production completely: no error, no
failing test, the local suite still green because local Postgres hands out a role
without that attribute. The entire third layer of tenant isolation would have
been silently absent in the only environment that matters.

The fix was the thing the design already called for, a dedicated `kd_app` role
with only the privileges it needs, and the verification is now part of the deploy:
query `rolbypassrls` for both roles, then connect as the app role with no tenant
context and confirm it reads zero rows from the real production database.

Takeaway: a security control that depends on the connecting role's attributes is
only as true as the role you actually connect with, and managed providers pick
that role for you. Inspect `rolsuper` and `rolbypassrls` on every environment
before trusting RLS, and make the deploy prove the deny rather than assuming it
carried over from your laptop.

## 23. Production is a different interpreter, and it says so

Two problems appeared within minutes of the first deploy, and both came from the
environment rather than the code. The seed script finished its work and then
crashed with `PythonFinalizationError`, because the connection pool's finalizer
tries to join worker threads and Python 3.14 refuses that at interpreter
shutdown; local dev was still on 3.13, where the same code exits quietly. And the
Neon connection string, pasted into an interactive Keychain prompt, was truncated
at exactly 128 characters, which surfaced as a baffling parser error about a
query parameter rather than anything resembling "your input was cut off".

Neither was caught by CI, because CI had been testing on 3.13 while the image
shipped 3.14, a gap that only opened when the base-image bump landed. Aligning
the CI runtime to the image was the actual fix; the pool close was the symptom.

Takeaway: test on the interpreter you deploy on, or you are testing a different
program. And when a value arrives mangled, check its length against a round
number before debugging the parser.

## 24. A restriction the UI enforces is not a restriction

The React admin panel offered exactly two roles when adding a member, `member`
and `admin`, because letting an admin hand out ownership was never the intent.
The API validated the same field against `^(owner|admin|member)$` and stopped
there. So the rule existed, was understood by everyone who read the screen, and
was enforced nowhere a caller with `curl` had to care about.

The exploit is one request and needs no cleverness: an admin creates a member
with `role: owner` and a password of their own choosing, logs in as that account,
and now holds every owner power, including the irreversible `DELETE /org`. An
audit reproduced it end to end, and the tenant deletion returned 204.

What makes this one worth recording is that the neighbouring code was careful.
`set_member_role` already blocked self-demotion and refused to remove the last
owner, both of which are subtler than this. The guard that was missing was the
blunt one: you cannot grant a role you do not hold. `role_at_least` had been
sitting in `auth.py` since Phase 1 and was only ever asked "is the caller at
least an admin", never "is the caller at least what they are handing out".

Takeaway: for every field that names a privilege level, ask what happens when a
caller sends the value your own UI will not offer. And when a permission check
reads `require_role("admin")` before an operation whose parameter is itself a
role, that is the shape of a missing ceiling.

## 25. A default argument reached into a function that had no business paginating

Pagination arrived late, as three list endpoints growing `limit: int = 100` and
an offset. Every caller was updated. The tests passed, the pager worked, the
totals were right.

`export()` was also a caller. It returned
`{"members": self.list_members(), "documents": self.list_documents()}`, and had
done since long before pagination existed. Those bare calls silently acquired a
limit of 100, so `/org/export` started returning the first hundred rows of each
listing, in well-formed JSON, with nothing anywhere to say the rest existed. It
was never noticed because every test tenant had a handful of documents.

The failure mode is what makes it nasty. A truncated export does not error, does
not warn, and looks exactly like a complete one; the only signal is a row count
nobody is checking against a number they do not have. And it is the tenant
data-export path, so the one time it matters is the time it is wrong.

The fix pages explicitly to exhaustion. The more useful change is the reason
it can't come back: `export()` no longer inherits anything from the listing
signatures, so those defaults can move again without dragging the export along.

Takeaway: adding a default argument changes every existing call site that did not
pass one, and the compiler will not tell you which they were. When the default is
a *limit*, grep for callers that wanted everything, because their behaviour just
changed and their tests probably still pass.

## 26. A streamed answer's bill arrives after the last token, so the client decides whether you meter it

Cost attribution worked, was tested, and showed up on the admin dashboard. It
also had a hole big enough to drive through: `finalize_answer` ran when the
provider's usage message arrived, and for a streamed response that message comes
*after* the final token. Disconnect one frame earlier and the answer row stayed
at zero tokens and zero dollars, while Anthropic had already generated the
response and would still charge for it. Aborting every request just before the
end was a way to spend without ever appearing in the ledger.

What made it invisible is that the happy path is the only path the tests walked.
`test_answer_records_usage` consumed the generator to exhaustion, as every test
client does by default. The abort case needed a test that deliberately stops
reading, which is not a thing you write unless you have thought about the client
being adversarial rather than merely absent.

The fix books an estimate from what was streamed, in the `finally` block that
already existed for the tracer, and only when at least one token came out — that
is the evidence the model actually ran. Estimates get their own column, because a
number you inferred sitting in the same field as a number the provider reported
is how a dashboard starts lying quietly.

Takeaway: any metering that happens at the end of a stream is optional from the
client's point of view. Meter incrementally, or make the teardown path book
something, and write at least one test that hangs up early.

## 27. The rate limiter was on the door that already needed a key

There was a token bucket, it worked, and it was wired to `/ask` — an endpoint you
cannot reach without a valid session. The two endpoints an anonymous caller can
reach, `/auth/login` and `/auth/signup`, had nothing. Twenty-five consecutive
password guesses all came back 401, at whatever rate they were sent.

The same route leaked account existence through its own response time, and for
the most ordinary reason: the code returned early when no user row came back, so
a miss skipped bcrypt entirely. About 4ms against roughly 240ms. No error message
differed, no status code differed, and the gap is a clean oracle anyway. The fix
is to verify against a throwaway hash on the miss path, so both branches pay the
same cost — deliberately doing useless work, which reads as a bug until you know
why it is there, hence a comment that outlives the reasoning.

Both were failures of aim rather than of implementation. The control existed and
was pointed at the wrong surface, which is harder to notice than a missing
control, because a search for "is there rate limiting" finds it and stops.

Takeaway: enumerate the endpoints reachable *without* credentials and check each
one separately. That set is small, it is the entire attack surface for anonymous
callers, and a protection applied everywhere else will not show up as absent.

## 28. Per-tenant caps do not bound a bill when tenants are free

The spend controls were per-org and they were thorough: a rolling 24-hour budget,
a monthly question cap, storage and document caps, all enforced before the model
runs and all with tests. The deployment tightened them further because the demo
credentials are published, cutting the daily budget to a dollar.

Every one of those numbers is a bound on one tenant. `/auth/signup` is
unauthenticated and hands out a tenant with a fresh set of all four, so the
worst case was never "a dollar a day"; it was "a dollar a day times however many
times someone is willing to POST to the signup form". The caps were sized against
a threat model where orgs are scarce, in a system where they are free.

Throttling signup raises the cost of that attack, but a throttle is a rate, not a
ceiling, and the question the deployment actually needed answered was "what is
the most this can spend today". That takes a number that is not scoped to a
tenant at all — which turned out to also mean it could not live under row-level
security, because a policy keyed on the current org makes the total unreadable
from the code that has to check it.

Takeaway: for every quota, ask what the unit is and how expensive that unit is to
create. A per-X limit only bounds anything if X is scarce, and if X is created by
an open endpoint, the real limit has to be global.
