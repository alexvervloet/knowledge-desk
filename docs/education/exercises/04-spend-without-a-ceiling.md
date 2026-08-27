# Exercise 4: spend without a ceiling

**Property:** a day's spend is bounded, whatever anyone does.
**Layer under test:** the limit check that runs before the model call.
**Time:** 20 minutes.

## The idea

Ordinary web endpoints are effectively free per call. You rate-limit them to stop
abuse and move on. An LLM endpoint is different in three ways at once:

- each call costs real money,
- the amount is denominated in tokens you cannot count until generation has
  finished,
- and the caller can disappear before you ever learn the total.

That combination produces a category of bug that does not exist elsewhere: code
that is correct, secure, well tested, and quietly capable of spending your entire
budget in an afternoon.

## Part A: watch the cap work

The limits are checked in
[`_limit_block`](../../knowledge_desk/assistant.py#L35-L47) — three of them, in
order: this org's rolling 24-hour spend, this org's questions this calendar
month, and the whole deployment's spend today.

Force the first one to bite:

```bash
python -m pytest tests/test_ops.py -q -k 'budget or blocked or abandoned'
```

```
5 passed, 16 deselected, 1 warning in 1.65s
```

Those five tests are worth reading before you change anything
([tests/test_ops.py:70-150](../../tests/test_ops.py#L70-L150)). To see what a
blocked question looks like on the wire, the SSE frames a caller receives when
their org is over budget are:

```
{'type': 'meta',  'answer_id': '68cd0a67-...', 'provider': 'mock'}
{'type': 'error', 'message': '[LIMIT] request blocked: daily budget exhausted. No answer was generated.'}
```

Compare that with a normal answer, which continues past `meta` into `sources`,
then a stream of `token` frames, then:

```
{'type': 'done', 'usage': {'input_tokens': 30, 'output_tokens': 36}, 'cost_usd': 0.0}
```

Three things about the blocked case are deliberate, and all three are decisions
you will have to make in your own system:

1. **No `sources` frame.** Retrieval never ran. The block happens before any
   work, not just before the model.
2. **The message is specific.** Unlike the error path
   ([assistant.py:123-138](../../knowledge_desk/assistant.py#L123-L138)), which
   deliberately hands the caller an opaque reference because exception text
   leaks database hosts and role names, this message is one *we* wrote and is
   safe to show. "You are over budget" is actionable; "something went wrong" is
   a support ticket.
3. **The refusal is still recorded.** The answer row is written and marked
   blocked ([assistant.py:70-73](../../knowledge_desk/assistant.py#L70-L73)),
   and an audit entry is logged. A refusal you cannot count is a refusal you
   cannot debug — when someone reports "it stopped answering", you need the
   number of blocks and their reason, not silence.

## Part B: why per-org caps do not bound your bill

Here is the part that catches people. This system has a per-org daily budget of
$5 and a per-org monthly question cap. Reason about it for a moment: if every
tenant can spend at most $5 a day, is the deployment's daily spend bounded?

Only if the number of tenants is. **Signup is open.** A fresh org arrives with a
fresh $5 allowance, so the per-org cap bounds one tenant's spend and says nothing
whatever about the bill. That is why a third check exists
([assistant.py:45-46](../../knowledge_desk/assistant.py#L45-L46)), and it is the
only one of the three that actually caps what the deployment can spend in a day.

Delete it and see what notices. In
[`_limit_block`](../../knowledge_desk/assistant.py#L35-L47), remove:

```python
    if scope.platform_spend_today() >= settings.platform_daily_budget_usd:
        return "service daily budget exhausted"
```

Then:

```bash
python -m pytest tests/test_ops.py -q -k 'budget or blocked or abandoned'
```

```
FAILED tests/test_ops.py::test_platform_budget_blocks_an_org_that_is_under_its_own
1 failed, 4 passed, 16 deselected, 1 warning in 1.70s
```

The other four still pass — the per-org caps are untouched and working
perfectly. The failing test
([tests/test_ops.py:129-145](../../tests/test_ops.py#L129-L145)) is the only one
that encodes the reasoning above: it spends past the platform ceiling as one org,
then asks as a **brand new org that has not spent a cent of its own allowance**,
and demands a refusal:

```
        fresh = signup("globex", "o@globex.test")
        assert _scope_for(fresh).spend_last_24h() == 0.0
        events = ask_events(fresh, "anything at all")
>       error = next(e for e in events if e["type"] == "error")
E       StopIteration
```

`StopIteration` because there was no error frame at all. The new org was happily
answered, as would the next thousand.

Restore before continuing:

```bash
git checkout knowledge_desk/assistant.py
```

## Part C: the bill that arrives after the client leaves

The last trap needs no edit — just read
[assistant.py:139-153](../../knowledge_desk/assistant.py#L139-L153) and work out
why the `finally` block is there.

Billing happens when the provider emits its `usage` frame, which is the last
thing in the stream. If the client disconnects halfway through, that frame never
arrives. The obvious behaviour — no usage, no charge — means the model already
generated those tokens and you already owe for them, and **aborting every request
just before the end is free inference**. Not a theoretical attack: a flaky mobile
connection produces the same pattern by accident, and your budget never moves.

The fix books an estimate from what was actually streamed, flagged
`usage_estimated = true` so the number is never confused with a reported one.
The guard is `and streamed` — evidence the model ran at all — so a failure
*before* the first token cannot invent a charge.

Two general points hide in that block:

- **Meter what you consumed, not what you successfully delivered.** They are
  different quantities, and only one of them is what the provider invoices.
- **Mark inferred data as inferred.** An estimate in the same column as a
  measurement, indistinguishable, is how a cost dashboard becomes untrustworthy.

Covered by
[test_abandoned_stream_counts_toward_the_org_budget](../../tests/test_ops.py#L342).

## Restore

```bash
git checkout knowledge_desk/assistant.py
git status --short        # nothing
python -m evals.run       # all evals passed
```

## The takeaway

- **Check the cap before the model runs.** A limit enforced afterwards is not a
  limit, it is an invoice.
- **Work out what your per-tenant limits actually bound.** If tenants are free to
  create, the answer is "one tenant" and you need a global ceiling too.
- **Record refusals.** The block is a product event, not an error.
- **Bill for generation, not for delivery.**

## Where to go next

You have now broken all four properties from
[01-thesis.md](../01-thesis.md): isolation twice, injection resistance, and
bounded cost. Next: [03-evals.md](../03-evals.md) for why these gates are built
the way they are, or
[05-reading-the-history.md](../05-reading-the-history.md) to see how most of them
got here — several were bugs first.
