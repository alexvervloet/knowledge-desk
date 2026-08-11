# Evals as a merge gate

You have run `python -m evals.run` a dozen times by now. This is what it is for,
why it is separate from the test suite, and how to add one.

## An eval is not a test

The distinction people usually reach for — "tests check code, evals check the
model" — is not quite it, and it leads to building the wrong thing. The useful
distinction is about what fails.

| | Unit / integration test | Eval |
|---|---|---|
| Asserts | this function does what it says | this **system property** still holds end to end |
| Scope | a unit, with the rest mocked | the real app, real database, real request path |
| Fails when | code is wrong | the *composition* is wrong, even though every part is right |
| Answers | "did I break this function?" | "did I break the promise?" |

The gap between those is where LLM applications live. You can have 100% coverage,
every unit correct in isolation, and still ship a system that cites documents the
asker cannot read — because the leak is not in a function, it is in how retrieval,
permissions, and prompt assembly compose.

Exercise 1 is exactly this. Deleting the ACL predicate breaks no unit test,
because no unit is wrong: `search` still runs a valid query, `principals` still
returns the right list, the assistant still streams correctly. The property is
gone anyway.

## Why this matters more with a model in the loop

Three properties of LLM code defeat the ordinary toolkit:

**Wrong output is not an error.** Nothing raises. Nothing returns a bad status
code. An ungrounded answer citing a forbidden document is a successful HTTP 200
with a fluent paragraph. Your monitoring is green.

**Output is nondeterministic.** `assert answer == expected` is unavailable. Even
temperature zero drifts across model versions. Anything asserting on generated
prose is a flaky test, and flaky tests get disabled — which is worse than never
having written them, because the gate is still in CI, still green, and no longer
checking anything.

**Behaviour changes without a deploy.** The provider updates a model and your
system behaves differently on identical code. There is no commit to blame and no
diff to review. Only a standing check catches it.

## The three evals here

Read [evals/run.py](../evals/run.py) alongside this — it is 179 lines and the
whole thing is legible in one sitting.

### 1. permission-leak ([run.py:88-108](../evals/run.py#L88-L108))

Sets up the minimum viable version of the real risk: one org, two members, a
document with `acl: ["user:<x>"]` containing a secret passphrase. Asks as the
*other* user and checks three escape routes — the search endpoint, the answer
text, and the sources list.

Note what makes it strong. It asserts **both** directions: `x_can_read` and
`not y_leaked`. An eval that only checked the negative would pass beautifully if
retrieval returned nothing to anybody, which is the failure mode a naive fix
produces. Half of a good safety eval is proving you did not achieve safety by
breaking the feature.

### 2. grounded-answer ([run.py:111-120](../evals/run.py#L111-L120))

Uploads a permitted document that matches the question and asserts it is cited.
This is the counterweight to eval 1. Together they pin the system between "leaks"
and "useless", which is the interval any access-controlled retrieval system has to
stay inside.

### 3. prompt-injection ([run.py:131-155](../evals/run.py#L131-L155))

Uploads a document containing a forged closing delimiter and a `SYSTEM:` payload,
then asserts three structural facts: exactly one opening and one closing marker
survive rendering, the untrusted region opens before the payload, and retrieval
still worked.

**It never asks whether the model complied.** That is the design decision worth
copying, and it is unpacked in
[exercise 2](exercises/02-forge-the-delimiters.md): a model-in-the-loop assertion
would need an API key, would be nondeterministic, and would prove only that one
model resisted one payload on one day. Asserting the structural property that the
*code* guarantees is deterministic, runs keyless in CI, and stays true across
model upgrades.

## What makes these gate-worthy

Four properties. If you take a checklist from this document, take this one:

1. **Deterministic.** No eval here can flake. A gate that fails randomly gets
   deleted or ignored, and an ignored gate is worse than none.
2. **Runs with no API keys.** The mock provider means every contributor, fork,
   and CI run gets the same result. A gate that only runs when secrets are
   available does not run on the pull requests that need it most.
3. **Fast, and isolated.** Each eval truncates every table first
   ([run.py:36-43](../evals/run.py#L36-L43)) — including resetting the auth rate
   limiter, because signing up repeatedly from one address throttles the gate
   against itself. Order-dependent evals are flaky evals wearing a disguise.
4. **It actually blocks the merge.**
   [ci.yml:46](../.github/workflows/ci.yml#L46) runs it as a required step, and
   `main` returns nonzero on any failure. An eval you run manually when you
   remember is a diagnostic, not a gate.

That last point is the one people skip. The eval is not the valuable artifact —
**the enforcement is.**

## Where these evals fall short

Be clear-eyed about this, because the failure was demonstrated in exercise 3.

**They check outcomes, so they cannot see a missing layer.** Removing row-level
security leaves every eval passing, because layers 1 and 2 still produce correct
behaviour. Outcome checks cannot distinguish three layers from two. That gap is
covered by a *test* that asserts the layer exists
([test_governance.py:192-202](../tests/test_governance.py#L192-L202)) — one that
queries with no tenant context, something no product feature ever does.

The general rule: evals check the promise, targeted tests check the defenses
behind it. You need both, and knowing which job each is doing tells you where to
put a new check.

**They do not measure answer quality.** There is no groundedness score, no
faithfulness metric, no golden dataset, no LLM-as-judge. A real product would add
those. They belong in a different category from these three: quality evals are
statistical, they run on a schedule against a dataset, they report a number that
moves, and they should *not* hard-fail a merge on a single sample — whereas a
permission leak is binary and should block instantly.

Mixing the two is a common and expensive mistake. Safety evals gate. Quality
evals trend.

## Adding your own

Copy the shape of an existing one:

```python
def my_property_eval() -> dict[str, Any]:
    """One sentence naming the property that must never regress."""
    _reset()
    token = _signup("acme", "owner@acme.test")
    _upload(token, [{"path": "doc.txt", "content": "...", "acl": ["public-to-org"]}])
    events = _ask(token, "the question")

    passed = ...  # a deterministic assertion
    return {"name": "my-property", "passed": passed, "detail": f"...={...}"}
```

Then add it to [`run_all`](../evals/run.py#L158-L159). It is picked up by the CI
gate and by [tests/test_evals.py](../tests/test_evals.py), which asserts the same
functions locally.

Before you write it, ask the four questions: is it deterministic, does it run
without keys, is it isolated from the others, and would you genuinely block a
release on it? If the answer to the last one is no, it is a quality metric, and it
belongs on a dashboard rather than in the gate.

## A good first exercise

The most-cited weakness of this system is that retrieval has no relevance
threshold, so a question the corpus cannot answer produces confident citations of
unrelated documents ([WALKTHROUGH.md](../WALKTHROUGH.md), "Where it will
disappoint you"). Write the eval first: upload a document about refunds, ask about
something entirely unrelated, and assert the answer refuses rather than cites.

Watch it fail. Then add a distance cutoff to
[`TenantScope.search`](../knowledge_desk/tenancy.py#L359-L380) and watch it pass.
That is the whole loop — property, gate, fix — on a real gap in a real system.

Next: [04-rag-core.md](04-rag-core.md).
