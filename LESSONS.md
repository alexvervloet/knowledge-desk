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
