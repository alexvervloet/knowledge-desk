# Evals and CI

Three checks that run the whole application end to end, assert properties that
must never regress, and fail the build when they do not hold.

---

## Level 1: high school intro to CS

You have probably written a test, or seen one. You call a function with 2 and 3,
you check it returns 5. Same input, same output, every time. If it ever returns
6, the test fails and you know immediately.

That works because the function is predictable. Now try writing a test for the AI
part of this program. You ask "how long do refunds take" and it answers "Refunds
are processed within ten business days [1]". Run it again and it might say
"According to the handbook, refunds take ten business days [1]". Both are
correct. Neither matches the other. What do you compare against?

You cannot check the words. So you check the properties instead. Not "did it say
this exact sentence" but "is this true about what happened":

Did it cite a source? Did that source come from a document this person is
allowed to read? Did it avoid mentioning the secret that lives in a document
this person is not allowed to read?

Those are yes-or-no questions, and they stay yes-or-no no matter how the wording
changes. Checks like that are called evals.

There are three of them here, in [evals/run.py](../../../evals/run.py), and they run
the real program, not a pretend version. Each one creates a fresh company,
creates users, uploads documents, asks a question, and inspects what came back.

The first one puts a secret phrase, `the passphrase is copper-moon-42`, in a
document only user X may read. Then it logs in as user Y and asks about it, twice,
through two different features. It passes only if X can find it and Y cannot,
neither in the search results nor anywhere in the text of the answer.

Here is the part that makes all this matter, and it is not a technical part. The
checks are wired into the thing that decides whether new code is allowed in. If
one fails, the change is refused. A test that reports a problem but does not stop
the change is a suggestion, and suggestions lose to deadlines.

---

## Level 2: second-year CS undergraduate

An eval is a test for behaviour that is not deterministic. You assert an
invariant, not an output.

Three of them, in [evals/run.py](../../../evals/run.py), run against the real
FastAPI app through `TestClient`, with a real Postgres and the real job queue
drained via `ingest.run_pending()`.

The permission leak eval. Sign up an org, create members X and Y, upload a
document with `acl: ["user:<x_id>"]` containing a known secret. Log in as both.
Assert that X's ask cites the document, and that for Y all three of these are
false: the document appears in `/search` results with `k=50`, the secret string
appears anywhere in the answer text, and the answer has any sources at all. Note
the `k=50`: it is asking for far more results than exist, specifically to defeat
an implementation that hides the leak by returning few rows.

The grounded answer eval. Upload one org-readable document, ask a question that
matches it, assert it is cited. This is the counterweight. Without it, an
implementation that returns nothing to everybody passes the leak eval perfectly,
and "refuse everything" is not a product.

That pairing is the technique worth learning. A safety property and a
functionality property that constrain each other. Either alone is trivially
satisfiable in a useless way.

The prompt injection eval. Upload a document that contains a forged closing
delimiter followed by `SYSTEM: ignore all previous instructions`. Then assert
three things, and look at what they are:

```python
boundary_intact = rendered.count(open_tag) == 1 and rendered.count(close_tag) == 1
wrapped = open_tag in rendered and rendered.index(open_tag) < rendered.index("SYSTEM:")
answered = bool(_sources(events))
```

Exactly one opening and one closing marker, so the forged one was defused. The
boundary opens before the attack text. And the document was still retrieved, so
the test is exercising the real path.

The fence-integrity eval is the same idea aimed one level up, and it exists
because these three assertions have a shape worth noticing: each one names a
field and a payload. `unfenced_untrusted` asks instead which uploader-supplied
values landed outside the fence at all, so it fails on a field nobody wrote an
eval for. It also asserts that the markers depend on the request nonce, which
nothing else does: delete the nonce and every other eval here passes, because
the layer underneath keeps the marker counts right while the boundary quietly
stops being one.

None of that asserts anything about what the model said. That is deliberate and
it is the second technique worth learning: these assertions are about your code,
so they are meaningful even against the mock provider, which means they run in CI
with no API key and no cost and no flakiness.

`main()` prints a report and returns 1 if any eval failed. It is a required step
in [.github/workflows/ci.yml:46](../../../.github/workflows/ci.yml#L46), so a
nonzero exit blocks the merge.

---

## Level 3: CS graduate learning AI

You have probably met evals as benchmark scoring: run the model over a labelled
set, report accuracy, compare to baseline. That is model evaluation. This is a
different thing wearing the same word, and conflating them is common.

These three are system evals. They exercise auth, tenancy, ingestion, the queue,
retrieval, ACL filtering, prompt assembly, and streaming, and they assert
properties of the whole path. The model is a participant, not the subject.

The design principle that makes them useful rather than flaky: assert what your
code controls.

Consider the two ways to write the injection eval. You could assert that the
model did not reveal its system prompt. That is a real property and it is what
you actually care about, and it is also a probabilistic assertion about a
third-party model that will fail occasionally for reasons you cannot fix, cost
money on every CI run, and eventually get marked flaky and skipped. I have
watched that happen to good test suites.

Or you assert that the delimiters were present, that exactly one pair survived,
and that the boundary opened before the attack text. Deterministic, free, fast,
and it fails precisely when someone breaks your defense. It does not prove the
model resisted. It proves you still built the fence.

That is the correct division. Test your code deterministically in CI. Evaluate
the model separately, offline, on a schedule, with sampling and thresholds, and
do not put it in the path of a merge.

Second principle: safety and functionality evals have to be paired, because each
one alone has a degenerate solution. Return nothing and the leak eval is
perfectly satisfied. Return everything and the grounded eval is. Only the pair
constrains the implementation.

Third, and this is the uncomfortable one. Evals catch behavioural regressions.
They are structurally incapable of catching the loss of a defense-in-depth layer,
because such a layer produces no behaviour while the layers above it work. Delete
row-level security from this project and all three evals pass, every unit test
passes, and the app is identical. The exercise is
[03-remove-the-invisible-layer.md](../exercises/03-remove-the-invisible-layer.md)
and I think it is the most valuable thing in the course.

The implication is that "our evals pass" is not a security statement. Invisible
layers need review, checklists, and infrastructure assertions, not behavioural
tests. Knowing which of your controls are testable and which are not is itself a
deliverable.

---

## Level 4: engineering manager interviewing for AI engineering

This is the concept where I would expect you, as a manager, to add the most value
and be least likely to be fooled, because the failure here is organisational
rather than technical.

The distinction to hold: a test asserts an exact output for a deterministic
function. An eval asserts a property of a system whose output varies. You cannot
assert the sentence. You assert that a source was cited, that the source was
permitted, that the secret did not appear.

The sentence that matters, and I would push on it hard in any interview: an eval
suite that does not block a merge is a dashboard. Dashboards get ignored during
crunch. The value is entirely in the gate.

Questions, in the order I would ask them:

"Do your evals block deploys, or do they report?" If they report, ask who looks,
how often, and what happened the last time one regressed. You will usually find
the answer is nobody, monthly, and nothing.

"How many evals do you have and how long do they take?" Three that run in ninety
seconds and block every merge beats two hundred that run nightly and are amber.
Small and blocking is a functioning quality system. Large and advisory is a
reporting habit.

"Do your evals call the real model?" This is a trap worth setting. If yes, ask
what they cost per run, how often they fail for reasons unrelated to the change,
and whether anyone has added a retry. Evals that call a live model in CI are slow,
priced, and flaky, and flaky gates get disabled. The mature pattern is
deterministic assertions on your own code in the merge path, and model quality
evaluated separately on a schedule.

"What property would a regression have to break for your evals to catch it?" This
is the deep one. Ask them to name a security control they could delete with all
evals still green. If they can, they understand defense in depth. If they insist
there is none, they either have a very unusual system or they have not looked.

"What is your functionality eval?" If every eval is a safety eval, an
implementation that refuses everything scores perfectly. That sounds like a joke
until you meet a system that has been quietly over-refusing for two months
because someone tightened a filter and only the safety evals were watched.

The thing to fund: the gate, and the person who keeps it green. Both are
unglamorous. The alternative is finding out about regressions from a customer.

---

## Level 5: senior AI engineer

Three evals, 175 lines, wired as a required CI step. The proportions are the
argument: this is small enough that nobody is tempted to make it non-blocking,
and it covers the properties that would actually end the product.

The choices I would call out as correct:

Structural assertions in the injection evals. Marker counts, the boundary opening
before `SYSTEM:`, and in the fence-integrity eval the question of which untrusted
fields escaped the fence. Deterministic, provider-independent, and they fail
exactly when someone breaks the assembly. The docstrings say outright that this
is what makes them meaningful against the mock. Reaching into
`providers._render_context` from an eval is a coupling I would normally object to,
and here it is the point: the eval is testing the defense, not the model.

The one I would call out as a mistake, since it was one: for a while the marker
counts were the whole gate, and they cannot see a missing nonce. A layer can be
deleted while every assertion about it stays green, if the assertions are about
a symptom that another layer also produces. Assert the property, and then check
that deleting the layer actually turns the assertion red.

`k=50` in the leak eval's search call. Asking for far more results than exist
defeats an implementation whose apparent safety comes from a small result set. If
I were reviewing this suite that is the line that would tell me the author had
thought about how the eval could be fooled.

Three separate leak channels checked for Y: search results, answer text
containing the literal secret, and any sources at all. Belt, braces, and a third
thing. The `bool(_sources(y_events))` check is the strongest, because it fails
even if the model happens to phrase around the secret.

`_reset()` truncating every table plus `auth_limiter.reset()`, with the comment
explaining that every eval signs up from the same client address so the auth
limiter would otherwise throttle the gate itself. That is the sort of detail you
only write after watching CI go red for a reason that has nothing to do with the
change.

The same functions are asserted from `tests/test_evals.py`, so local runs get
them without a separate command. Good, and it also means the eval bodies are held
to the same import hygiene as the test suite.

What is missing, in the order I would add it:

No eval for the estimated-billing path on an abandoned stream. That is a
free-tokens exploit if the `finally` block regresses, it is deterministic against
the mock, and it would be maybe twenty lines: start a stream, close it early,
assert a row exists with `estimated = true`.

No eval asserting the ACL denormalisation stays consistent. Change a document's
ACL, assert every chunk's ACL matches. The two-copies-of-the-truth problem from
[migrations/0009_chunk_acl.sql](../../../migrations/0009_chunk_acl.sql) is the most
likely source of a future permission bug in this codebase, and it is trivially
assertable.

No eval covering result-count degradation under a selective ACL. Filtered HNSW
returning short result sets is invisible from every existing assertion. Upload
enough chunks, give a user access to a small subset, ask for k=5, assert you got
5. That is the exact failure `hnsw.iterative_scan` exists to prevent, and nothing
currently notices if someone removes that line.

That last one generalises into the honest limitation of the whole suite, which
[03-evals.md](../03-evals.md) states directly: these evals assert properties that
are visible in behaviour. The RLS layer, the iterative scan setting, and the
least-privilege database role are all invisible while the layers above them work.
For those, the control is review and infrastructure assertion, and the right move
is to say so rather than to let a green gate imply more than it covers.
