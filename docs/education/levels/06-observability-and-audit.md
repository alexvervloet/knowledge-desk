# Observability and audit

Knowing what the system did, in enough detail to debug a bad answer, without the
record itself becoming the leak.

---

## Level 1: high school intro to CS

When a program does something wrong, you need to find out what happened. With
ordinary code you can add print statements and run it again. With this system you
often cannot, because the thing that went wrong happened once, to a stranger,
four hours ago, and it involved an AI model that will not give you the same
answer twice.

So the system writes down what it did while it was doing it. Three separate
records, for three different readers.

Logs, for the programmer. If something crashes, the full details go here.

Traces, for the programmer who wants to see the shape of one question. A trace is
like a receipt for a single request, with a line for each step: how long the
search took, which passages it found, what the model was asked, what it said, how
many words that was, and what it cost. When someone says "the answer was
rubbish", the trace tells you whether the search failed or the model did. Those
have completely different fixes and they look identical from the outside.

An audit log, for the company using the product. This one is not for engineers.
It records that a person did a thing: uploaded a document, added a member, asked
a question, changed someone's permissions. If an admin at that company ever needs
to ask "who deleted the handbook", this answers it.

Now the twist, and it is a good one. The record can leak.

If someone types their credit card number into a question, and the program writes
the question into the audit log, the card number is now in the log, forever, and
the log is a thing lots of people can read. So before writing an audit entry, the
program scans the text for things that look like emails, phone numbers, social
security numbers, and card numbers, and replaces them with tags like
`[REDACTED-EMAIL]`. That is [pii.py](../../../knowledge_desk/pii.py), 36 lines of
patterns.

And one more, which is my favourite detail in this whole project. When something
goes wrong, the user does not get told what went wrong. They get a short random
code like `a3f9c1d0`. The real details go in the log next to that same code. The
reason is that error messages love to include useful information, and "useful to
the programmer" and "useful to an attacker" are frequently the same sentence.
Before this change, a database failure handed the user the database's address and
the username the program was logging in with.

---

## Level 2: second-year CS undergraduate

Three mechanisms, three audiences, three different rules.

Tracing, in [tracing.py](../../../knowledge_desk/tracing.py). One trace per
question, sent to Langfuse. The structure is a root span called `ask`, containing
a `retrieval` span and a `generation` span. The retrieval span records the
sources chosen. The generation span records the model, the input, the streamed
output, token counts, and cost. Tags carry the org id, the user, and the
provider.

If you have used distributed tracing before, this is the same idea: nested spans
with timing and attributes. The LLM-specific part is that spans carry token usage
and cost as first-class fields, because those are the numbers you will be asked
about.

Two properties of this file are worth copying into your own code. Every method is
wrapped in try/except and swallows its exceptions, and everything is a no-op when
no key is configured. Observability that can break the request it is observing is
worse than no observability, because it converts a monitoring outage into a
customer outage. The class checks `self._root is None` at the top of each method
and returns, so the whole feature switches off cleanly.

Audit, in [audit.py](../../../knowledge_desk/audit.py). An append-only table of
`(org_id, actor_user_id, action, detail, timestamp)`. Best-effort by design: a
failure prints a warning and does not propagate, with the trade-off stated in the
docstring. A dropped write is a gap in the log, not a failed request. That is a
judgement call and the opposite choice is defensible for a compliance product;
what matters is that somebody made it deliberately.

PII redaction, in [pii.py](../../../knowledge_desk/pii.py). Four regexes: email,
SSN, credit-card-shaped digits, US phone. `redact_detail` walks one level of a
dict and redacts string values before the audit write. Note the comment on
pattern order: SSN is checked before phone so `NNN-NN-NNNN` is not mislabelled.
Ordering-dependent regex sets are exactly the sort of thing that quietly breaks
when someone appends a new pattern, and the comment is what stops that.

Error handling, in
[assistant.py:123-138](../../../knowledge_desk/assistant.py#L123-L138):

```python
reference = secrets.token_hex(4)
log.exception("answer generation failed [ref=%s] ...", reference, ...)
yield {"type": "error", "message": f"Answer generation failed. Quote reference {reference} ..."}
```

The user gets a correlation id. The log gets the exception. This is the standard
pattern and the commit history says it was written after `str(exc)` went straight
into the browser and handed out database host names.

---

## Level 3: CS graduate learning AI

The observability problem for LLM systems is different in one specific way: you
cannot reproduce the failure. A bad answer is a function of the retrieved
passages, the prompt, the model version, and sampling. Rerunning gives you a
different answer. So the trace is not a debugging convenience, it is the only
evidence that will ever exist for that request.

That reframes what belongs in it. You need enough to reconstruct the input, not
just measure the output.

The detail I would point at as the good idea here is
[`retrieval_stats()`](../../../knowledge_desk/tenancy.py#L341) landing on the
retriever span. It records two numbers: how many ingested chunks the org has, and
how many of them this caller was permitted to see. The gap between those is the
ACL filter made observable.

Why that matters. When a user reports "it says it can't find anything", there are
three possible causes and they are indistinguishable from the answer text:
nothing was ingested, retrieval found nothing similar, or this person can see
eleven chunks out of nine thousand because nobody added them to the right group.
The third is a permissions configuration problem, not a retrieval problem, and
without that number on the span you will spend an afternoon tuning chunk sizes to
fix a group membership.

Generalise it: instrument the boundary conditions of your safety machinery, not
just its outcomes. Every filter you add is a place where correct behaviour and
broken configuration look identical.

Second thing at this level: the asymmetry between the audit log and stored
questions. Audit detail is PII-redacted. The question text stored in `answers` is
not, and the reasoning is written out at
[tenancy.py:384-396](../../../knowledge_desk/tenancy.py#L384-L396). An audit entry is
metadata about an action, where a stray email address is incidental and redacting
it costs nothing. A question is the content. Redact it and `top_queries` shows
`[REDACTED-EMAIL]` and an answer can no longer be traced back to what was asked.
Anyone who can read these is already an admin of the asker's org.

I think that is the right call, and I also think the important part is the last
line of that docstring: it is worth knowing that this is where user-typed text
accumulates in the clear. A deliberate decision that is written down is a
decision. The same decision undocumented is a finding waiting for an auditor.

Third: the trace itself carries the question and the answer, and it goes to a
third-party service. That is a data-residency and processing question the moment
your customers care about one, and it is invisible in the code because it looks
like a monitoring integration rather than a data export.

---

## Level 4: engineering manager interviewing for AI engineering

The reason observability is disproportionately important here: you cannot
reproduce a failure. In your current systems, a bug report gives you a request
you can replay. In an LLM system, replaying gives you a different answer. Whatever
you captured at the time is all you will ever have.

That has a budget consequence. Tracing in LLM products is not a nice-to-have you
add in the second year. It is the debugger. A team that has not instrumented is a
team that cannot investigate quality complaints, and quality complaints are the
main kind you will get.

Three layers to expect, and they answer different questions:

Traces answer "what happened in this one request": what was retrieved, what the
prompt was, what came back, how many tokens, what it cost, how long each step
took. Langfuse here, and there are several comparable tools.

Audit logs answer "who did what in my company", and that is a customer-facing
feature, not an engineering one. Enterprise buyers ask for it in procurement.

Application logs answer "what broke".

Questions worth asking:

"When a user says the answer was wrong, what do you look at?" A good answer names
the retrieved passages first, because the most common cause of a bad answer is
bad retrieval, not a bad model. A candidate who jumps straight to prompt tweaks
has not debugged many of these.

"What is in your trace that is not in a normal APM trace?" Token counts, cost,
the prompt, the retrieved documents, the model version. If they say "we use
Datadog" and stop, they are measuring latency and missing the entire content
layer.

"What personal data ends up in your logs and traces?" This one is uncomfortable
and it should be. Questions contain whatever users type. Retrieved passages
contain whatever is in the documents. Traces often go to a third-party service.
A team that has thought about it can tell you what is redacted, what is not, and
why. A team that has not will tell you about their retention policy, which is a
different question.

"What does a user see when something fails?" You want a correlation id and a
generic message. Raw exception text reaching a browser is a real and common leak,
and this codebase has the commit where it was fixed.

One thing to push for on your own team: put the permission-filter counts in the
trace. Knowing that a user could see 11 of 9,000 chunks turns a mystifying
quality complaint into a five-minute fix, and it costs two count queries.

---

## Level 5: senior AI engineer

[tracing.py](../../../knowledge_desk/tracing.py) is 129 lines and the discipline in
it is the interesting part. `AskTracer` holds `_root`, `_retrieval`, `_gen`, and
every public method guards on `_root is None` and wraps its body in try/except
with `log.exception`. Init failure sets `_root = None` and the whole feature
becomes inert. That is the correct shape for any telemetry integration and it is
rarer than it should be.

`propagate_attributes(trace_name="ask", user_id=user_id)` wraps only the
synchronous span creation, with a comment noting that the context manager
therefore cannot leak across interleaved requests. That is a real hazard with
context-var-based SDKs inside a streaming generator, and the fix is exactly this:
never hold the context manager across a yield.

Span typing is right: `retriever` for retrieval, `generation` for the model call,
with `usage_details` and `cost_details` populated from the provider's usage frame
rather than estimated. `finish(error=...)` sets trace level ERROR with a status
message, and the caller distinguishes a chosen limit message from an unexpected
exception, so a rate-limited request shows up as a deliberate refusal rather than
as noise in your error rate.

Things I would change:

`token()` appends to `self._answer` unconditionally, even when tracing is
inactive. That is an unbounded list per request accumulating the full answer for
nothing when Langfuse is off. Small, but it is on the hot path of every streamed
token in the default keyless configuration.

`retrieval_stats()` costs two `count(*)` queries over `chunks` per traced ask,
and calls `principals()` a second time. It is gated on `tracer.active`, which is
the right gate, and at 10M chunks those counts stop being cheap. An approximate
count or a cached per-org total with an exact permitted count would keep the
diagnostic without the scan.

The audit log's best-effort write is defensible for this product and I would want
it configurable. For anything sold into a regulated buyer, "the action succeeded
but we have no record of it" is the wrong default, and the two behaviours differ
by one `raise`.

PII redaction is regex over four formats, one dict level deep, applied only to
audit detail. The docstring is honest that it catches the obvious formats rather
than being a classifier. The gap I would flag is that document content is
PII-scanned at ingest to set `pii_types` for admin visibility, but retrieved
passages flow into the trace unredacted, which means your trace vendor holds
customer document content. That is a defensible product decision and an
indefensible surprise, so it belongs in the docs rather than only in the code.

The best single line in this area is the reasoning at
[tenancy.py:384-396](../../../knowledge_desk/tenancy.py#L384-L396) for why questions
are stored unredacted while audit detail is not. It states the asymmetry, gives
the mechanism that would break under the alternative, names who can read the
data, and then says plainly that this is where user-typed text accumulates in the
clear. That last sentence is what separates a decision from an oversight, and
most codebases skip it.
