# Cost and limits

Every answer costs real money, the amount depends on how much text goes in and
comes out, and the person deciding how much text that is does not pay the bill.

---

## Level 1: high school intro to CS

When the program asks the AI model a question, it gets charged. Not a
subscription, not per month. Per word, roughly, in both directions. The text you
send in is charged at one rate and the text that comes back is charged at a
higher one.

For one question this is a fraction of a cent. It stops being funny quickly. A
question here sends five passages plus the question plus the rules, gets back a
paragraph, and costs maybe a tenth of a cent. Now imagine someone writes a tiny
program that asks a question every second, all night. That is 30,000 questions
and a real bill, and nobody was even reading the answers.

So the program counts. Every answer records how much text went in, how much came
out, what it cost, and who asked. Before it will answer a new question, it checks
three things:

Has this company already spent its daily limit? Has this company asked more
questions than its monthly allowance? Has the whole service, all companies added
together, spent its daily limit?

If any of those is true, the question is refused before the model is ever called.
Not after. Before. A limit you check after you have already spent the money is
not a limit, it is a receipt.

That third check is the one I want you to notice, because it is the one that is
easy to miss. Suppose every company is capped at ten dollars a day. That sounds
safe. But anyone can sign up, and each new signup is a new company with a fresh
ten dollars. A person who wants to run up your bill just makes a hundred
accounts. Per-company limits only limit companies. They do not limit the total,
unless you also put a limit on the total. The code for that is one function,
[tenancy.py:444](../../../knowledge_desk/tenancy.py#L444), and it was added late,
which is normal, because you only see the hole once you think like an attacker
instead of like a customer.

---

## Level 2: second-year CS undergraduate

Three defenses, layered by how expensive the thing they are protecting is.

Rate limiting, in [ratelimit.py](../../../knowledge_desk/ratelimit.py). A token
bucket per key: each key has a bucket with capacity `burst`, refilling at
`per_min / 60` tokens per second, and a request consumes one token. Empty bucket
means rejected, with a `retry_after` computed from the refill rate. This is the
standard algorithm and it is 70 lines. Two separate buckets exist: one for
authenticated asks keyed by user, and `auth_limiter` for login and signup keyed
by client address. The second is tighter on purpose, because those are the only
endpoints an anonymous caller can reach and each one costs a bcrypt verification.

Budget checks, in
[assistant.py:35-47](../../../knowledge_desk/assistant.py#L35-L47):

```python
def _limit_block(scope):
    if scope.spend_last_24h() >= settings.daily_budget_usd:
        return "daily budget exhausted"
    if scope.questions_this_month() >= settings.monthly_question_cap:
        return "monthly question limit reached"
    if scope.platform_spend_today() >= settings.platform_daily_budget_usd:
        return "service daily budget exhausted"
    return None
```

Called first in `answer_stream`, before retrieval and before any model call. A
blocked question is still recorded and marked blocked, at
[tenancy.py:428](../../../knowledge_desk/tenancy.py#L428), which sounds like
bookkeeping and is actually operational: a refusal you cannot count is a refusal
you cannot debug, and the first support ticket will be "it says I hit my limit
and I do not believe you".

Body size limits, in [bodylimit.py](../../../knowledge_desk/bodylimit.py). Reject an
oversized upload before reading it into memory. A limit applied after you have
buffered 400MB is not protecting the thing you thought it was.

There is a fourth one worth studying for the concurrency lesson. The storage
quota is checked inside the same transaction that does the write, at
[tenancy.py:199-227](../../../knowledge_desk/tenancy.py#L199-L227), passed in as a
`precheck` callback that `sync_documents` runs before writing anything. If you
checked the quota in one transaction and wrote in another, two concurrent uploads
both measure "we are under quota", and both write. Classic time-of-check to
time-of-use. Putting the check inside the write transaction with a lock is the
fix, and it is the same reasoning as any read-modify-write you have had to make
atomic.

---

## Level 3: CS graduate learning AI

The idea to internalise: in an LLM product, cost is a function of user input and
model output, and neither is bounded by anything in your control unless you bound
it explicitly. This is different from the systems you have written, where a
runaway user costs you CPU you already paid for.

Three specifics.

Cost accrues even when nothing is delivered. The stream is the interesting case.
`answer_stream` is a generator, deliberately, and the docstring says so: closing
the stream early is part of the contract. A client that walks away mid-answer
causes `.close()`, which raises `GeneratorExit` inside the generator, which runs
the `finally` block. Those tokens were already generated. The model produced
them, they were billed to you, and the usage frame that would have recorded them
never arrived.

Left unbilled, that is an exploit with no cleverness required: abort every request
just before the end and consume unlimited model output while your recorded spend
stays at zero. The handling is at
[assistant.py:139-153](../../../knowledge_desk/assistant.py#L139-L153): estimate the
usage from what was actually streamed, book it, and flag it as estimated so
nobody mistakes it for a measured number. It only books when something was
streamed, so a failure before the first token does not invent a charge.

The estimate is `len(text) // 4`, which is the usual English approximation and is
biased low for code and non-Latin scripts. Correct for a budget guard, wrong for
an invoice, and the code says which of those it is for.

Per-tenant caps do not bound a bill. This deserves stating precisely because it
is a modelling error rather than a coding one. A per-tenant cap bounds spend per
tenant. Total spend is the cap times the number of tenants. If tenant creation is
free and open, the number of tenants is attacker-controlled, so total spend is
unbounded. The fix is a deployment-wide ceiling that is not a function of tenant
count, `platform_spend_today` here, and it needs its own table, `platform_spend`,
because it cannot be a tenant-scoped aggregate.

There is an exercise built on exactly this,
[04-spend-without-a-ceiling.md](../exercises/04-spend-without-a-ceiling.md), and
it is the one I would give to anyone who has just shipped their first LLM
product.

Estimation before the fact is harder than accounting after it. Notice that the
checks are all "have we already spent too much" rather than "will this request
take us over". The second is what you actually want and it needs a token count for
a request you have not made yet, plus a reservation so concurrent requests cannot
all pass the check. This system does not do that, and the honest description is
that spend can overshoot the ceiling by roughly the number of requests in flight
times the cost of each. For a portfolio project that is fine. For a product with
a hard contractual cap it is not, and the fix is a reservation ledger, not a
bigger check.

---

## Level 4: engineering manager interviewing for AI engineering

This is the section where your existing instincts transfer almost perfectly, and
where you are most likely to catch a candidate out.

The model. You pay per token in and per token out, output typically several times
the input rate. Input tokens include everything you send: the system prompt, the
retrieved passages, the question. So a system that retrieves ten passages instead
of five roughly doubles the input cost of every question in the product, and
nobody will describe that change as a cost decision when it lands.

The four questions.

"Where is the cap checked?" Before generation or after. A cap checked after the
model call is an invoice. This sounds too obvious to get wrong. It is extremely
common, because the natural place to write the accounting code is next to the
usage data, which arrives at the end.

"What stops one customer from spending everyone's budget?" Per-tenant caps. Fine.
Then the follow-up that separates people: "and what stops a hundred new customers
from doing it?" If signup is open, per-tenant caps bound nothing in aggregate. You
need a ceiling on the deployment. I would rate a candidate who volunteers this
without prompting as having actually operated something.

"What happens if the user closes the browser mid-answer?" The tokens were spent.
If the accounting only runs on the success path, you have a free-tokens hole and
your cost reporting understates reality by however often that happens, which for
a streaming interface is not rare. Good answer: book an estimate, mark it as
estimated.

"Can you tell me what any individual customer cost you last month?" If the answer
is no, you cannot price the product, you cannot identify the customer who is
unprofitable, and you cannot answer the finance question that will eventually be
asked. Cost attribution is a product requirement wearing an engineering costume.

Two things to take back to your team.

Put the pricing table in one place. Here it is
[providers.py:22-26](../../../knowledge_desk/providers.py#L22-L26), and cost is
computed in exactly one function. Teams that scatter rates across a codebase have
cost reporting that is wrong in ways nobody detects until a rate changes and only
three of the five call sites get updated.

Record the blocked requests. A limit that fires silently generates a support
ticket you cannot answer. This system writes an audit entry and marks the answer
row blocked, so "why did I get cut off" has a factual answer with a timestamp.

---

## Level 5: senior AI engineer

The design is: token-bucket rate limits in memory, spend and volume caps read
from Postgres before generation, estimated billing for streams that die, and a
platform-wide ceiling under the per-tenant ones. Small, legible, and honest about
its gaps, which are worth naming.

The rate limiter is per process. In-memory buckets, so N API workers give you N
times the intended limit, and a restart resets everyone's bucket. The module
docstring states this and the WALKTHROUGH repeats it. It is the correct
architecture for this project because the durable backstop is the Postgres
budget, and I would rather see an in-memory limiter with a documented caveat than
a Redis dependency added for tidiness. The eviction detail is nice: buckets idle
for an hour are indistinguishable from unseen keys, so holding them is pure leak,
and the sweep is amortised behind a size check rather than run on every call.

The budget check is a read, not a reservation. `spend_last_24h`,
`questions_this_month`, and `platform_spend_today` are aggregates evaluated
before the model call. Under concurrency, every request in flight sees the same
pre-burst total, so the ceiling can be crossed by roughly the in-flight count
times per-request cost. The `platform_spend` table from migration 0013 is where a
reservation would live: debit an estimate at admission, reconcile against actual
at the usage frame, release on failure. That is the version I would build if the
cap were contractual. It also gets you request-level admission control for free,
which is the thing the current design cannot express.

Estimated billing on abandoned streams is the part of this repo I would most want
other people to copy. It requires understanding that `answer_stream` is a
generator whose `GeneratorExit` path is load-bearing, that the model already
charged you for tokens the client never read, and that the guard condition should
be "something was streamed" so a pre-first-token failure invents nothing. The
`estimated` flag on the row matters as much as the booking: mixing measured and
estimated numbers without a discriminator makes every downstream cost analysis
unfalsifiable.

Two smaller things.

`_estimate_tokens` at four characters per token undercounts for non-English and
for code. It only ever feeds the budget guard, so the failure mode is
under-billing an abandoned stream, which biases in the attacker's favour on
exactly the path built to stop an attacker. Cheap improvement: use the SDK's
token counting endpoint for the input side, which is exact, and keep the
heuristic only for the output you actually saw.

The Claude estimate includes `_SYSTEM` in the input count and the mock's does
not. Harmless, since the mock costs zero, but it means the two providers'
estimates are not comparable, and someone will eventually compare them.

The thing this design gets right that most do not: the three checks are ordered
cheapest-to-most-general and all of them run before retrieval, so a blocked
request costs one to three aggregate queries and no embedding call. Putting the
platform ceiling last is a small readability win and a small cost win, and the
comment above it explains why the first two are insufficient rather than leaving
the reader to work out why there are three.
