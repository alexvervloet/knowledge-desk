# WALKTHROUGH

A narrated trip through Knowledge Desk end to end: what happens at each step, the
decision points where the system could go a different way, the places it will
surprise you, and the shapes of problem it is and is not good at.

This is not the setup guide (see [README.md](README.md)) and not the build log
(see [PLAN.md](PLAN.md) and [LESSONS.md](LESSONS.md)). Every output quoted here
was captured from an actual run against the seeded demo data.

---

## The one-paragraph version

An organization signs up, uploads documents, and a background worker turns them
into embedded chunks. Members ask questions in natural language. Retrieval finds
the nearest chunks **that the asking user is allowed to see**, and the model
answers using only those passages, with citations. Every question is metered
against the org's budget and quotas, written to an audit log, and traced. No
answer can ever be built from a document the asker lacks permission to read, and
that is enforced in three independent layers rather than trusted to one.

---

## Step 1: signing up creates a tenant, not just a user

`POST /auth/signup` with an org slug, org name, email, and password creates three
things in one transaction: the org, the user, and a membership binding them with
the role `owner`. There is no "user without an org" state. The response carries a
bearer token whose session is scoped to one org.

**Branch point: one identity, many orgs.** A user is a global identity, and
membership binds them to an org. If your email belongs to exactly one org, login
can omit the org slug. If it belongs to several, login without a slug is a 401
telling you to pick one, because a session is always scoped to a single tenant.
There is no cross-org "switch workspace" token; you log in again for the other
org. That is a deliberate simplification, and the place to change if you ever
want a workspace switcher.

**Gotcha: the org slug has a minimum length.** Slugs must match
`^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$`, so a two-character slug like `ci` is a 422.
This cost a CI run once. If you are scripting signups, use something like
`ci-demo`.

## Step 2: uploading documents is a reconcile, not an append

`POST /sources/folder` takes a list of documents (path, content, optional ACL)
and treats them as the **complete desired state** of that source for that org. It
compares each incoming file's SHA-256 against what is stored and returns a
summary like `{"enqueued": 1, "unchanged": 1, "deleted": 1}`:

- new or changed content is written and an embed job is enqueued
- byte-identical content is skipped entirely, no embedding, no cost
- a path that was previously known and is **absent from this upload** is marked
  deleted and its chunks are removed

That third behavior is the one that surprises people. This endpoint is a sync,
not an "add these files". Uploading a single file to an org that already has
fifty will delete the other forty-nine. If you want incremental adds, send the
full set each time (which is cheap, because unchanged files cost nothing).

**Why it is built this way:** it makes re-syncing a folder idempotent and nearly
free. An unchanged 25-file corpus reconciles in about 12ms versus about 1377ms
for the first ingest, because the hash check skips chunking and embedding
entirely. With a real embedding provider the gap is far larger, since what is
skipped is network calls.

The request returns `202 Accepted` immediately. Embedding happens in the worker.

**Gotcha: nothing is searchable until the worker runs.** In development that
means a second shell running `python -m knowledge_desk.worker`. If documents sit
at status `pending` forever, the worker is not running. In the container the
worker is its own service and this is handled for you.

**Branch point: caps are checked before the queue.** Per-org document and storage
caps are enforced at upload, returning `413` before any embedding work is
enqueued. The check is deliberately conservative: an update counts toward the
incoming total, so it can refuse slightly early, never slightly late.

## Step 3: the worker embeds, and failures are contained

The worker claims jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, so you can run
several workers safely. Each job chunks the document (1000 characters, 150
overlap), embeds the chunks, and replaces that document's chunk set.

Failure handling is per document, not per batch. A document the embedding
provider rejects fails its own job, retries with exponential backoff, and after
three attempts dead-letters to status `failed`. The other documents in the same
upload ingest normally. A poison file cannot wedge the queue.

**Gotcha: binary files are not handled at the connector yet.** Postgres `text`
columns cannot store a NUL byte at all, so a genuinely binary file fails at
insert time with a database error rather than being gracefully skipped. Filter to
text formats before uploading. This is a known gap, recorded in the plan.

## Step 4: asking a question, and the permission boundary

`POST /ask` opens a server-sent-event stream. The frames arrive in a fixed order:

```
meta      answer_id and provider
sources   the chunks that will be cited (omitted if there is nothing to cite)
token     many, the answer streaming word by word
done      token usage and cost estimate
```

An `error` frame can replace `done` at any point. Before any of this, retrieval
runs against the caller's **principal set**: `public-to-org`, plus `user:<their
id>`, plus one `group:<id>` for each group they belong to. The ACL filter lives
inside the SQL that fetches candidates, so a forbidden chunk is never scored,
never ranked, and never available to be returned by accident.

Here is the isolation property, from a real run. Both orgs are asked the same
question, and each only ever sees its own documents:

```
acme   "how long do refunds take?"  ->  sources: handbook.md, security.md
globex "how long do refunds take?"  ->  sources: products.md, onboarding.md
```

**Gotcha, and the most important one in this document: being an org owner does
not grant document access.** Roles govern administration (who may invite members,
upload, edit ACLs), not readership. A document ACL'd to `user:X` is invisible to
the org owner in retrieval. From a real run, with three chunks in the org and one
of them scoped to member X:

```
member X   org_chunks: 3   allowed_chunks: 3
org owner  org_chunks: 3   allowed_chunks: 2
```

If you want owners to see everything, that is a policy you must add. The system
deliberately does not assume it, because "the admin can read every private
document" is a decision that should be explicit.

**Branch point: revocation is immediate.** Principals are recomputed from the
database on every single query, so removing someone from a group revokes their
retrieval access on their next question. There is no cache and no staleness
window. The cost is two extra cheap queries per ask.

## Step 5: the answer, and what it will not do

The model is instructed to answer only from the provided passages and to cite
them by number. When the permitted candidate set is **empty**, the assistant
refuses rather than answering from the model's own knowledge:

```
I don't have anything I'm allowed to cite for that. Nothing in the documents
you can access matches this question.
```

That refusal is the permission boundary reaching all the way through to the
generated text. A user cannot learn the contents of a forbidden document, and
cannot get a plausible-sounding answer assembled from general knowledge either.

**Gotcha: there is no relevance threshold.** Retrieval returns the nearest `k`
chunks, and "nearest" does not mean "relevant". Asked a question its corpus
cannot answer, an org with documents still gets sources back. From a real run:

```
globex: "what is the airspeed velocity of an unladen swallow?"
  -> sources: products.md, onboarding.md   (cosine distance 1.019, 1.045)
```

A distance around 1.0 is essentially unrelated, and the system cited it anyway.
The refusal path only triggers on an empty permitted set (a brand new org, or a
user with access to nothing), not on a weak match. In mock mode this is very
visible because the mock always names source [1]. With a real model the system
prompt does most of the saving, since the model is told to say it has nothing to
cite when the context does not contain the answer, but the passages are still put
in front of it. **If you productionize this, add a distance cutoff.** It is a
one-line change in the retrieval query and the highest-value improvement
available.

## Step 6: metering, and the two ways you get told no

Every question writes an `answers` row with its token counts and cost estimate,
which is what the budget sums over. Two different guardrails, and they announce
themselves in deliberately different ways:

**Per-user rate limit** (5 burst, 30 per minute) is an HTTP `429` with a
`Retry-After` header, before the stream ever opens:

```
statuses: [200, 200, 200, 200, 200, 429, 429, 429]
Retry-After: 2
```

**Per-org budget and quota** (default $5 per rolling 24 hours, 1000 questions per
month) is checked inside the stream, because by then the client is already
reading a stream and deserves the news in the channel it is listening on. The
HTTP status is still 200 and the frames are `meta` then `error`:

```
[LIMIT] request blocked: daily budget exhausted. No answer was generated.
```

The blocked question is still recorded, flagged `blocked`, and audited, so a user
hitting their cap appears in the dashboard rather than vanishing. Note that the
model is never called, so a blocked question costs nothing.

**Gotcha: the cost figure is an estimate with a known direction.** It is derived
from token counts times list price and it undercounts, because thinking tokens
bill without appearing in the text stream. That is fine for a budget cap, which
needs the right order of magnitude and a known bias, and not fine as a billing
system. Set the cap with headroom.

## Step 7: what an admin sees afterwards

The Members tab manages roles, membership, and groups. The Sources tab lists
documents with status, chunk count, PII flags, and each document's ACL, editable
in place. The Usage tab shows questions, spend, and storage against their caps,
the month's top questions, and the recent audit events.

PII detection runs at ingest and flags obvious formats (email, phone, SSN, card
shapes) on the document row. It is a **visibility signal, not a gate**: a
document with PII still ingests and is still retrievable. PII is redacted out of
audit log detail, not out of documents.

With Langfuse keys set, each question also emits a trace tagged by org and user,
whose retriever span carries `org_chunks` versus `allowed_chunks`. That makes the
permission filter visible per request: you can see that a user was shown 2 of the
org's 5 chunks, which is the kind of thing that is otherwise invisible until it
is a security incident.

---

## Where this design shines

- **Mixed-sensitivity corpora.** The whole point. An org where some documents are
  company-wide, some are team-scoped, and some are individual is exactly what the
  ACL-in-the-candidate-fetch design is for. Most RAG demos ignore this entirely.
- **Multi-tenant SaaS, where a leak is existential.** Three independent isolation
  layers (application filter, ACL fetch, Postgres row-level security) mean no
  single bug leaks data across tenants, and there is a test that proves a raw
  query with no tenant context returns nothing.
- **Auditable, cost-controlled deployments.** Every question is attributable to a
  user and an org, capped, logged, and traced. If someone asks "who asked what,
  and what did it cost us", there is an answer.
- **Text knowledge bases of modest size.** Handbooks, policies, runbooks, wikis.
  Documents that are mostly prose and mostly stable.

## Where it will disappoint you

- **As a search engine for questions your corpus cannot answer.** No relevance
  threshold means confident-looking citations of unrelated documents. Fix this
  before showing it to anyone if that matters.
- **At very large scale, as configured.** Retrieval is an exact scan with an ACL
  filter, which is correct and fast for thousands to low tens of thousands of
  chunks. There is no ANN index yet, so at millions of chunks you would want
  HNSW, and combining an ANN index with a per-user ACL filter is genuinely
  tricky: the index does not know about permissions, so you either over-fetch and
  filter, or partition. Plan for that before you promise it.
- **On non-text documents.** No PDF, DOCX, image, or OCR handling. Everything is
  UTF-8 text, and binary files fail at the database rather than being skipped.
- **For fast-changing documents.** Resync is cheap for unchanged files, but a
  changed file re-embeds entirely. There is no partial or streaming update.
- **For nested or inherited permissions.** Groups are flat. A group cannot contain
  a group, and documents do not inherit an ACL from a folder. If your real
  permission model is a tree, this is a rewrite of the principal set, not a
  tweak.
- **As a general chatbot.** It has no conversation memory. Every question is
  independent, so follow-ups like "what about the second one?" have no referent.

## If you extend one thing

Add a distance cutoff to retrieval so weak matches produce the refusal instead of
irrelevant citations. It is small, it is contained in one query, and it converts
the most common failure mode from "confidently wrong" into "honestly empty",
which is the behavior the rest of the system is already built around.
