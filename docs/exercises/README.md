# Exercises

Four exercises that break a safety property on purpose and show you what catches
it — or, in one case, what does not.

Reading about defense in depth is cheap. Deleting a `where` clause and watching a
secret appear in another user's answer is not something you forget.

## Setup

You need the stack running locally. From the repo root:

```bash
docker compose up -d db                   # Postgres + pgvector on :5436
python -m knowledge_desk.migrate          # schema, RLS policies, and the app role
python check_setup.py                     # should print "all checks passed"
```

No API keys are needed. The project falls back to a loud mock provider, and
every exercise here works against it — including the prompt-injection one, which
asserts structural properties rather than model behaviour.

Confirm you are starting green:

```bash
python -m evals.run
```

```
eval gate
  PASS  permission-leak      x_can_read=True y_leaked=False
  PASS  grounded-answer      cited_policy_doc=True
  PASS  prompt-injection     boundary_intact=True wrapped=True retrieved=True

all evals passed
```

If that is not what you see, fix it before continuing — the exercises are
differences from this baseline.

## The exercises

Do them in order. The third is the one worth your time even if you skip the rest.

| # | Exercise | Property broken | What you learn |
|---|---|---|---|
| 1 | [Break the ACL filter](01-break-the-acl-filter.md) | Isolation, layer 2 | Why the permission check belongs inside the ranking query, not after it |
| 2 | [Forge the delimiters](02-forge-the-delimiters.md) | Injection resistance | Why a document can attack your prompt, and what a real boundary costs |
| 3 | [Remove the invisible layer](03-remove-the-invisible-layer.md) | Isolation, layer 3 | That losing a redundant layer is *unobservable from behaviour* — the argument for testing layers, not just outcomes |
| 4 | [Spend without a ceiling](04-spend-without-a-ceiling.md) | Bounded cost | Why the cap goes before the model call, and what "blocked" has to record |

## How each one works

Every exercise has the same five parts:

1. **The edit** — a small, specific change, given as exact before/after text.
2. **Run it** — one command.
3. **What you should see** — output captured from an actual run of that exact
   edit, not predicted.
4. **What happened** — the explanation.
5. **Restore** — an explicit `git checkout`, so you never have to guess how to
   get back.

## A rule for the whole set

Restore after each one. Several of these break things in ways that make the next
exercise's output confusing, and exercise 3 in particular leaves the system
*looking* completely healthy while a security layer is gone — which is exactly
its point.

If you want to be certain you are clean at any moment:

```bash
git status --short        # should print nothing
python -m evals.run       # should print all evals passed
```
