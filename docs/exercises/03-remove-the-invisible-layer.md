# Exercise 3: remove the invisible layer

**Property:** an answer can never be built from a document the asker cannot read.
**Layer under test:** row-level security, the third and deepest one.
**Time:** 15 minutes.

This is the exercise that surprises experienced engineers. Do this one even if
you skip the others.

## The idea

Knowledge Desk enforces tenant isolation three separate times:

1. The data layer stamps `org_id` onto every query
   ([tenancy.py](../../knowledge_desk/tenancy.py)).
2. Retrieval filters by the caller's ACL inside the ranking query (exercise 1).
3. Postgres row-level security denies by default underneath both
   ([migrations/0007_rls.sql](../../migrations/0007_rls.sql)).

Layer 3 is the one you cannot bypass by writing a new query, because it is
enforced by the database rather than by remembering to add a `where` clause. It
is also the one with a property nobody expects.

## How layer 3 actually works

Two pieces, and the second is where this exercise lives.

**The policies.** Every org-scoped table gets `force row level security` and a
policy that compares the row's `org_id` against a session variable
([migrations/0007_rls.sql:32-34](../../migrations/0007_rls.sql#L32-L34)). The
application sets that variable per transaction
([db.py:1-16](../../knowledge_desk/db.py#L1-L16)). No variable set means no rows
match, so the default is deny.

**The role.** Postgres does not apply RLS policies to a superuser, to a table's
owner without `FORCE`, or to any role carrying `rolbypassrls`. So the project
connects with two different credentials, and the distinction is the whole ball
game:

| Setting | Role | Used for |
|---|---|---|
| `DATABASE_URL` | `kd` — owner | migrations, DDL, preflight |
| `APP_DATABASE_URL` | `kd_app` — least privilege | every runtime query |

**Row-level security is a property of the role you connect as, not of your
tables.** Point the running application at the owner credential and the policies
are still there, still correct, and no longer doing anything.

## The edit

No code change. Run the application against the owner role instead of the app
role — exactly the mistake you make when a managed database hands you one
connection string and you paste it into your config.

```bash
APP_DATABASE_URL=postgresql://kd:kd@localhost:5436/knowledge_desk python -m evals.run
```

## What you should see

```
eval gate
  PASS  permission-leak      x_can_read=True y_leaked=False
  PASS  grounded-answer      cited_policy_doc=True
  PASS  prompt-injection     boundary_intact=True wrapped=True retrieved=True

all evals passed
```

**Everything passes.** Read that again. You have just removed one of the three
layers protecting your most important guarantee, and the merge gate — the thing
built specifically to catch permission regressions — reports the system healthy.

## Why nothing failed

Because the system's *behaviour* is identical. Layers 1 and 2 are untouched, so
every query still carries its `org_id` and every retrieval still filters by ACL.
No user can reach another tenant's data. The evals check outcomes, and the
outcomes are correct.

This is the uncomfortable arithmetic of defense in depth: **a redundant layer, by
definition, changes nothing observable while the other layers work.** Its entire
value is what happens on the day one of them has a bug. Which means you cannot
detect its absence by testing behaviour — not with evals, not with integration
tests, not by clicking through the app, not in staging. It is invisible until the
day you needed it, and on that day it is not there.

## Now catch it properly

```bash
APP_DATABASE_URL=postgresql://kd:kd@localhost:5436/knowledge_desk \
  python -m pytest tests/test_governance.py -q -k 'rls or pooled'
```

```
        with connect() as conn:
>           assert conn.execute("select count(*) as n from documents").fetchone()["n"] == 0
E           assert 1 == 0

tests/test_governance.py:200: AssertionError
...
FAILED tests/test_governance.py::test_pooled_connection_does_not_inherit_previous_tenant
FAILED tests/test_governance.py::test_rls_blocks_query_without_org_context
2 failed, 9 deselected, 1 warning in 0.66s
```

Look at what
[that test](../../tests/test_governance.py#L192-L202) asserts. It does not ask
"can user A read user B's documents" — every other check already covers that. It
opens a connection **with no tenant context at all**, runs a bare
`select count(*) from documents`, and demands the answer be zero.

That is not a test of a feature. There is no product behaviour where the
application queries without a tenant. It is a test that *the layer exists*, and
it is the only thing in the entire suite that fails when layer 3 quietly
evaporates.

## Why this is not hypothetical

This exact thing nearly shipped, and it is written up in
[LESSONS.md](../../LESSONS.md) §22. Managed Postgres providers give you an
owner-ish role by default — Neon's `neondb_owner` has `rolbypassrls = true`.
Deploying with the credential the provider hands you would have removed layer 3
in production while every local test stayed green, because locally the app role
is created by the migration and behaves correctly.

Nothing would have alerted anyone. The application would have worked. The
isolation guarantee would simply have been two layers deep instead of three, in
the one environment where real customer data lives, until some future bug in
layer 1 or 2 turned a contained mistake into a breach.

The deploy now provisions a separate least-privilege role and verifies the deny
against the real database, rather than trusting that the local behaviour carries
over.

## Restore

Nothing to restore — you changed no files, only an environment variable for the
duration of one command. Confirm anyway:

```bash
git status --short        # nothing
python -m evals.run       # all evals passed
```

## The takeaway

Three of them, in order of how much they should change how you work:

1. **Test that each layer exists, not only that the outcome is right.** Outcome
   tests cannot distinguish three layers from two. If you build defense in depth
   and only assert behaviour, you have built defense in depth *once* — at the
   moment you wrote it — and have no way to know when it decays.
2. **Know which of your security controls are properties of configuration
   rather than of code.** Those are the ones that survive code review, pass every
   test, and differ silently between your laptop and production. RLS-versus-role
   is one. Anything set by an environment variable is another.
3. **A green CI run means the properties you thought to check still hold.** It is
   evidence about your test suite as much as about your system.

Next: [04-spend-without-a-ceiling.md](04-spend-without-a-ceiling.md).
