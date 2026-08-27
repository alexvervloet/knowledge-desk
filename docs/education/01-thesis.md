# The hard part is not the RAG

Read this before the code, or the code will look over-engineered.

## The number that makes the argument

Knowledge Desk is 3,169 lines of application Python. The part that a tutorial
would call "the RAG" — turning text into chunks, chunks into vectors, and a
question into a nearest-neighbour lookup — is 126 lines across three files:

| File | Lines | Job |
|---|---|---|
| [chunking.py](../knowledge_desk/chunking.py) | 35 | split text into overlapping windows |
| [embeddings.py](../knowledge_desk/embeddings.py) | 76 | text → vector, with a mock fallback |
| [retrieval.py](../knowledge_desk/retrieval.py) | 17 | embed the query, hand it to the data layer |

Four percent of the code. And it is the *easy* four percent: it has no failure
mode more interesting than a network timeout, and you could swap the whole thing
for a different library in an afternoon.

The other 96% is what makes it a product instead of a demo: who is allowed to
see which chunk, what happens when the worker dies mid-embed, what stops one
customer reading another's handbook, what the answer costs and who pays, and how
you know the deploy you just shipped did not undo any of it.

That ratio is the thesis. **AI engineering, as a job, is mostly not model work.**

## Why the demo → product gap is unusually wide here

Every kind of software has a gap between the demo and the shippable version. LLM
applications have a wider one than most, for four reasons that all show up in
this repo.

**1. The input is attacker-controlled, and it arrives shaped like instructions.**

A knowledge assistant reads documents that users uploaded. In a normal
application, user data flows into a database and comes back out as data. Here it
flows into a *prompt*, where the boundary between "content" and "command" is a
convention the model chooses to honour, not a parser rule. A document that says
`SYSTEM: ignore all previous instructions` is a SQL injection whose interpreter
is a neural network and whose escaping rules are probabilistic.

The defense is in [providers.py:47-77](../knowledge_desk/providers.py#L47-L77):
explicit delimiters around retrieved text, forged delimiters neutralised before
the prompt is assembled, and a system prompt that names the boundary so the model
can locate it. You will break this yourself in
[exercise 02](exercises/02-forge-the-delimiters.md).

**2. Wrong output is not an error.**

A type checker cannot tell you the answer was ungrounded. A test cannot assert
`answer == expected` when the model phrases it differently every time. Nothing
crashes when your assistant confidently cites a document the asker was never
allowed to read — it returns HTTP 200 with a plausible paragraph.

This is why the project has an [eval gate](03-evals.md) as a required CI step
alongside the unit tests. Tests check that the code does what it says; evals
check that the *system* still has the properties you promised, end to end,
through the model.

**3. Every request costs money, and the cost is only known afterwards.**

Ordinary endpoints are effectively free per call, so you rate-limit for abuse and
move on. Here a single question can cost real cents, the bill is denominated in
tokens you cannot count until generation finishes, and the client can hang up
before you ever see the total.

That last one is a genuine trap, and this repo walked into it: a stream the user
abandons still consumed tokens, because the model generated them before anyone
stopped reading. Left unbilled, aborting every request just before the end is
free inference. The fix is in
[assistant.py:139-153](../knowledge_desk/assistant.py#L139-L153) — book an
estimate, flagged as estimated. Spend is capped *before* the model runs
([assistant.py:35-47](../knowledge_desk/assistant.py#L35-L47)), because a cap you
check afterwards is an invoice.

**4. Retrieval turns an access-control question into a ranking question.**

This is the one people miss. The moment your answer is built from retrieved
documents, "can this user read this file" stops being a permission check on an
endpoint and becomes a property of a similarity search. Get it wrong and the
permission system is intact, every endpoint is correctly guarded, and the model
cheerfully summarises a document the asker cannot open.

The rule this project follows: **filter inside the candidate fetch, never
after.** The ACL predicate sits in the same SQL that ranks
([tenancy.py:359-380](../knowledge_desk/tenancy.py#L359-L380)), so a forbidden
chunk is never scored, never ranked, and cannot survive a forgotten post-filter.
A post-filter also silently returns fewer than `k` results, which looks like bad
retrieval rather than a security design.

## The four properties this system defends

Everything in the 96% exists to hold one of these:

1. **Isolation** — an answer can never be built from a document the asker cannot
   read. Enforced three independent times (org filter, ACL-in-fetch, row-level
   security), so no single bug is a breach.
2. **Groundedness** — if retrieval returns nothing permitted, the assistant
   refuses instead of answering from the model's own knowledge
   ([assistant.py:90-96](../knowledge_desk/assistant.py#L90-L96)). The refusal is
   the feature.
3. **Bounded cost** — per-org budget, per-org monthly cap, and a
   deployment-wide daily ceiling, all checked before generation.
4. **Attribution** — every question is tied to a user, an org, a cost, an audit
   entry, and a trace.

Pick any file in the repo and it is almost certainly serving one of those four.

## What "defense in depth" actually buys you

The isolation property is enforced three times, which sounds like belt and
braces until you notice the uncomfortable consequence: **removing one layer
changes nothing observable.** The system behaves identically. Every eval passes.
The demo works.

That is not an argument against redundancy — it is the argument for testing the
existence of each layer rather than only the outcome. You will see this yourself
in [exercise 03](exercises/03-remove-the-invisible-layer.md), which is the one
worth doing even if you skip the others.

## If you take one habit from this project

Decide which properties must survive every future change, then build something
automated that fails loudly when one stops holding — before you build the
feature. Here that is three evals and a permission test, wired into CI as
required steps. It is much easier to add on day one than after the first
incident, and it is the difference between a demo that works and a system you
can let strangers upload documents to.

Next: [02-concept-index.md](02-concept-index.md) to find any of this in the code,
or jump straight to the [exercises](exercises/).
