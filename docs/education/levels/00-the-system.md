# The system

Knowledge Desk is a question-answering assistant over a company's own documents,
built for several companies at once, where the whole difficulty is making sure
nobody ever sees a sentence they were not allowed to see.

---

## Level 1: high school intro to CS

You have used Ctrl-F. You type a word, the computer finds that exact word on the
page. It is fast and it is stupid. If the handbook says "reimbursement is
processed within ten business days" and you search for "how long do refunds
take", Ctrl-F finds nothing, because not one of those letters matches.

Knowledge Desk fixes that in two steps.

First, it chops every document into small pieces, about a paragraph each. The
code that does this is 33 lines long, in
[chunking.py](../../../knowledge_desk/chunking.py). Each piece is called a chunk.

Second, it turns each chunk into a long list of numbers. Not the letters, the
meaning. Picture a map where every sentence is a pin. Sentences about refunds
land near each other, sentences about parking land somewhere else entirely, and
the pin for "reimbursement is processed within ten business days" sits right next
to the pin for "how long do refunds take", even though they share almost no
words. The list of numbers is the pin's coordinates. Here each pin has 1,024
coordinates instead of 2, which you cannot picture, and that is fine, because the
computer only ever does one thing with them: it measures which pins are close
together.

So when you ask a question, the system turns your question into a pin too, grabs
the five nearest chunks, hands them to an AI model, and says "answer using only
these, and tell me which one you used". You get a sentence back with a citation,
like a school essay.

Now the part that is actually hard.

Imagine your school and the school across town both use this. Their handbooks
are in the same computer. Nothing in the idea above stops a student at your
school from asking a question and getting back a chunk of the other school's
handbook. The map does not know about schools. It only knows about closeness.

Most of this project is the answer to that problem. There are 3,186 lines of
Python here. The part that does the chopping, the pins, and the closeness search
is 129 of them. Everything else exists so that the wrong person never gets the
right answer.

The one thing I would take away: the clever bit was the easy bit.

---

## Level 2: second-year CS undergraduate

Two tables, roughly. `documents` holds uploaded text. `chunks` holds the pieces,
each with a `text` column and an `embedding` column, where the embedding is a
1,024-dimensional float vector stored in Postgres by the pgvector extension.
Retrieval is a nearest-neighbour query under cosine distance, written in SQL with
the `<=>` operator, in
[tenancy.py:391-412](../../../knowledge_desk/tenancy.py#L391-L412 "TenantScope.search").

You already know what makes that expensive. Exact nearest neighbour over N rows
is O(N) distance computations per query, and each one touches 1,024 floats. So
there is an approximate index (HNSW, a navigable small-world graph) that trades
exactness for something close to logarithmic lookups. The trade matters later,
because an index that returns "about the right ten rows" behaves badly when you
then throw eight of them away for permission reasons.

The architecture is the shape you would sketch on a whiteboard for any web app:

- A FastAPI service handles HTTP. Login, upload, ask.
- Postgres holds everything, including the job queue. No Redis, no broker.
- A second process, [worker.py](../../../knowledge_desk/worker.py), 52 lines, drains
  that queue and does the slow work.
- A React single-page app talks to the API.

The one non-obvious split: uploading a document does not embed it. Embedding is a
network call to an external service and it is slow, so the upload writes rows and
enqueues a job, then returns. The worker picks the job up. Asking a question, by
contrast, is synchronous, and streams its answer back token by token over Server
Sent Events so the user sees text appear rather than watching a spinner for four
seconds.

Now the part that is worth your attention as a student, because it is the part
your coursework will not cover.

Every query in this system carries an organisation id. Not by convention, not
because each handler remembers. Every org-scoped read and write goes through one
class, `TenantScope`, and that class stamps the id on. The reason is stated in
the module docstring at the top of
[tenancy.py](../../../knowledge_desk/tenancy.py): if a query touches org data and is
not a method on that class, that is the bug. It turns "did we leak data" from a
question about the whole codebase into a question about one file.

That is a design pattern, and it is a better one than it looks. You have written
code where the same check appears in fifteen handlers. Fourteen of them are
correct. The fifteenth is the incident.

---

## Level 3: CS graduate learning AI

You know the retrieval-augmented generation loop: embed the corpus, embed the
query, take top-k by cosine similarity, stuff the passages into a prompt, ask the
model to answer from them. You have probably built it. It works on a laptop in an
afternoon.

Here is the ratio that reframes the job. That loop is
[chunking.py](../../../knowledge_desk/chunking.py) (33 lines),
[embeddings.py](../../../knowledge_desk/embeddings.py) (79), and
[retrieval.py](../../../knowledge_desk/retrieval.py) (17). One hundred and
twenty-nine lines out of 3,186. Four percent. And it is the four percent with the
fewest interesting failure modes: the worst thing that happens is a network
timeout, and you could swap the whole thing for a different library between
lunch and dinner.

The other 96% is where the graduate-to-engineer gap lives. Four things in
particular do not show up in the coursework version.

The input is attacker-controlled and it arrives shaped like instructions. A
knowledge assistant reads documents that users uploaded. In an ordinary
application, user data goes into a database and comes back out as data. Here it
goes into a prompt, where the line between "content" and "command" is a
convention the model chooses to honour rather than a rule a parser enforces. A
document containing `SYSTEM: ignore all previous instructions` is an injection
attack whose interpreter is a neural network and whose escaping rules are
probabilistic. The defense is in
[providers.py:61-153](../../../knowledge_desk/providers.py#L61-L153) and it is worth
reading because it is so unsatisfying compared to parameterised SQL.

Wrong output is not an error. Your training says a bug throws, or returns a
wrong value you can assert against. A model that answers a question about
Acme's refund policy using a passage it should never have retrieved returns a
fluent, well-cited, entirely plausible paragraph. Nothing raises. Nothing logs.
The only way you find out is if you asserted the property in advance, which is
what [evals/](../../../evals/) is for.

Permission and ranking interact. This is the one I would put money on you not
having thought about. If you retrieve top-5 and then filter out the ones the
caller cannot see, you do not get "the permitted top-5". You get "however many
of the global top-5 happened to be permitted", which is often zero, and the user
sees an unexplained refusal while the answer sits in row six. Filtering has to
happen inside the ranking query. See
[01-retrieval-and-acl.md](01-retrieval-and-acl.md), which is entirely about this.

The output costs money per token, and the caller controls the token count. A user
can hold a stream open, abandon it, and repeat. Somebody has to have decided in
advance what happens then.

None of that is machine learning. All of it is the job.

---

## Level 4: engineering manager interviewing for AI engineering

Here is the shape of the thing you are being interviewed about, in the terms you
already think in.

An LLM product is a normal distributed system with one unusual dependency: a
component that is non-deterministic, priced per unit of output, and capable of
being socially engineered by your own input data. Everything else, the queue, the
database, the auth, the rate limits, the tracing, is engineering you have already
managed. The AI-specific part is smaller than the discourse suggests. In this
codebase it is measurably 4% of the lines.

That number is your best interview instrument. Ask a candidate "roughly what
fraction of an LLM product is model code", and listen for whether they have ever
shipped one. Somebody who has says "almost none of it" and then tells you what
the rest was. Somebody who has not talks about the model.

Four vocabulary items you will be expected to hold, with the reason each one
matters to a budget or an incident review:

Grounding and refusal. The model answers only from retrieved passages, and says
"I have nothing I am allowed to cite" when there are none. The refusal is not
politeness. It is the access-control boundary reaching the generated text. The
live demo of this project is built entirely around one screenshot of two
customers asking the same question, where one gets an answer and the other gets
a refusal. If a candidate cannot explain why the refusal is the interesting half,
they have been building demos.

Retrieval. Finding the relevant passages. The failure mode that costs you a
customer is not "it found nothing useful", it is "it found something belonging to
somebody else".

Evals. Tests for behaviour that is not deterministic. In this repo three of them
run in CI and block the merge. That last clause is the whole value. An eval suite
nobody blocked a merge on is a dashboard.

Tokens and cost attribution. You are billed per token in and per token out. If
you cannot say which customer caused which tokens, you cannot price the product,
and you will find out during the month you get a surprise invoice.

Questions I would actually ask, and what the answers tell you:

"Where do you enforce that a user cannot retrieve a document they lack permission
for?" A weak answer filters the results. A strong answer puts the predicate in
the same query that does the ranking, and can explain why the weak version
silently returns short result sets rather than failing.

"What stops one customer's spend from being unbounded?" A weak answer describes a
per-customer cap. A strong answer notices that per-customer caps bound one
customer, and that with open signup they do not bound the bill at all, so there
has to be a deployment-wide ceiling underneath. That is
[tenancy.py:468](../../../knowledge_desk/tenancy.py#L468) here, and it was added
late, which is normal.

"How do you know your last deploy did not reintroduce a data leak?" If the answer
is "we tested it", ask what the test asserts. The interesting version asserts a
property, in CI, that fails the build.

"Tell me about a security layer you could delete without any test failing." This
is a trap and a good one. Defense in depth means some layers are invisible from
behaviour. This project has an exercise built on exactly that, because removing
its third isolation layer leaves every eval green. A candidate who has felt that
will light up.

---

## Level 5: senior AI engineer

The claim this repo is arguing, and the reason it exists as a portfolio piece, is
that the interesting engineering in an LLM product is the operational layer, and
that the RAG core is a component you should deliberately keep boring so it can be
replaced. Chunking is fixed-width character windows with overlap. No token
awareness, no semantic splitting, no reranker, no hybrid BM25, no query rewriting.
[04-rag-core.md](../04-rag-core.md) is the honest inventory of what that gives up.

What is worth your time here, in rough order:

The ACL predicate lives on `chunks`, not `documents`, and that is a measured
decision rather than a modelling preference. With `d.acl ?| principals` on the
joined table, the planner cannot stream ordered rows out of the HNSW index,
because the predicate deciding survival lives on a different relation. It falls
back to a full scan and sort. Isolating the predicates showed the join, the
`org_id` filter, and the `status` filter all keep the index, and only the
cross-table ACL check breaks it. The write-up is in
[migrations/0009_chunk_acl.sql](../../../migrations/0009_chunk_acl.sql). The cost is
a denormalised copy that `update_document_acl` has to keep in sync, which is a
real liability and the kind of thing that rots.

`hnsw.iterative_scan = relaxed_order`, set per physical connection in
[db.py](../../../knowledge_desk/db.py). Without it a filtered HNSW search returns k
candidates, the ACL predicate removes most of them, and the caller silently gets
three results when they asked for five. This is the single most under-discussed
failure mode in filtered vector search and it degrades quality without ever
raising anything.

The tenant GUC is transaction-scoped, and that word is load-bearing under a
connection pool. `set_config(..., true)` reverts at commit, so a recycled
connection starts with no tenant and the RLS policies deny by default. Session
scope would ride the connection back into the pool and the next request inherits
the previous tenant's org. The corresponding session-scoped setting on the same
connection, the iterative scan above, is deliberately session-scoped because it
is identical for every tenant. Both decisions are in the same file, next to each
other, which I think is the right way to write that down.

RLS is a property of the role you connect as. Migrations run as owner, the app
connects as `kd_app`, and `force row level security` exists because a table owner
bypasses RLS otherwise. On a managed database the default role may bypass it
anyway, which is recorded in LESSONS §8 and §22 and is the sort of thing that
turns a defense-in-depth layer into a comment.

Billing an abandoned stream. `answer_stream` is a generator on purpose, so a
client that walks away mid-answer raises `GeneratorExit` inside it, and the
`finally` block books an estimated charge for the tokens already generated. Left
unbilled, aborting each request just before the usage frame is a free-tokens
exploit. It only books when something was actually streamed, so a pre-first-token
failure does not invent a charge. See
[assistant.py:157-171](../../../knowledge_desk/assistant.py#L157-L171).

Where I think it is still weak: the rate limiter is in-process, so it is per
worker rather than per deployment, and the platform spend ceiling is a read of
aggregate spend rather than a reservation, so a burst of concurrent asks can
cross it before any of them finish. Both are stated rather than hidden, which is
the least you can do.

The exercise I would point you at is
[03-remove-the-invisible-layer.md](../exercises/03-remove-the-invisible-layer.md).
Delete row-level security and every eval still passes. That is the whole argument
for defense in depth compressed into one command, and it is uncomfortable in a
useful way.
