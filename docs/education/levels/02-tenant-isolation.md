# Tenant isolation

Two companies, Acme and Globex, keep their documents in the same database, in the
same tables, reachable by the same code. The property that has to hold is that no
question asked by anyone at Acme can ever be answered using anything belonging to
Globex. It is enforced three separate times, on purpose.

---

## Level 1: high school intro to CS

Say you and a friend both keep your homework in the same shared folder on the
school computer. Your files and their files sit side by side. The only thing
stopping you from opening theirs is that you do not click on them.

That is a terrible security system, and it is roughly what a database looks like
by default. Acme's documents and Globex's documents are rows in the same table.
Nothing about the table knows they belong to different companies except a column
that says so.

So the code has to say so, every single time. Every question the program asks the
database has to include "and only the rows belonging to this company". Miss it
once, in one place, and the wrong company's handbook comes back.

"Just remember to include it every time" is not a plan. Programs have hundreds of
places that talk to the database, written by different people, over years. Some
of those places will forget. So Knowledge Desk does three things instead of one.

First, all the database questions live in one file, and that file adds the
company filter for you. You do not get to forget, because you do not get to write
the query yourself. If your code talks to the database and is not in that file,
that is the bug, and it is easy to spot in review.

Second, the search query that finds passages carries the company filter and the
permission filter together, the thing described in
[01-retrieval-and-acl.md](01-retrieval-and-acl.md).

Third, and this is the one I find genuinely clever, the database itself is told
to refuse. Postgres, the database being used here, can be given a rule that says
"unless the program has announced which company it is acting for, return nothing
at all, for every query, forever". Not "return an error". Return zero rows. So a
query that forgot its filter does not leak. It comes back empty.

Three walls. Any one of them holds on its own. The reason for building three is
that you cannot prove you never made a mistake, so you build so that one mistake
is not enough.

---

## Level 2: second-year CS undergraduate

Every org-scoped table carries an `org_id` column. That is the boundary. The
three layers are three independent ways of making sure it is honoured.

Layer one is a choke point in application code. `TenantScope`, in
[tenancy.py](../../../knowledge_desk/tenancy.py), holds an `AuthContext` with the
acting user's id, org, role, and email, and every method stamps `org_id` onto the
SQL it issues. The module docstring states the rule directly: if a query touches
org data and is not a method here, that is the bug. This is 561 lines and it is
the largest file in the project, which tells you where the actual complexity of
the product lives.

Layer two is the ACL predicate inside the retrieval query, covered in the
previous document.

Layer three is Postgres row-level security, in
[migrations/0007_rls.sql](../../../migrations/0007_rls.sql). This is the one you
probably have not used, so here is how it works.

RLS lets you attach a policy to a table which is silently ANDed into every query
against it. The policy here is:

```sql
org_id = current_setting('app.current_org', true)::uuid
```

`current_setting` reads a session variable, which Postgres calls a GUC. The
application sets it at the start of each transaction, inside
[`connect(org_id)`](../../../knowledge_desk/db.py). If it is unset, the comparison
is null, no rows match, and every query on that table returns empty.

Deny by default, enforced by the database, underneath all your application code.
A handler that forgets its `where org_id = ...` gets zero rows instead of
somebody else's data.

Two implementation details that are easy to get wrong, and both are the kind of
thing that turns this layer into decoration:

The role matters. A superuser bypasses RLS. So does the table's owner, unless you
say `force row level security`. Migrations here run as the owner, but the
application connects as a dedicated least-privilege role called `kd_app`, and the
migration forces RLS so ownership does not grant an escape. If the app connected
as the owner, all of this code would run and enforce nothing.

The variable is transaction-scoped. `set_config(name, value, true)` sets it for
the current transaction only. Get this wrong and you have a genuinely nasty bug,
which is the next level's material.

---

## Level 3: CS graduate learning AI

The pooled-connection failure is worth walking through slowly, because it is
subtle, it is silent, and the fix is one boolean.

Connections to Postgres are expensive to open, so applications keep a pool and
hand connections out per request. Request A borrows connection 7, does its work,
returns it. Request B borrows connection 7 next.

Now suppose you set the tenant GUC session-scoped:

```sql
set app.current_org = '<acme-uuid>'
```

That setting belongs to the session, which means it belongs to the physical
connection, which means it survives the commit and rides back into the pool.
Request B, from Globex, borrows connection 7, and unless it sets the GUC before
its first read, its RLS policy evaluates against Acme's uuid. Layer three now
actively points the wrong way. It is not merely absent, it is asserting a false
tenant.

Transaction scope fixes it, because the setting reverts at commit:

```python
conn.execute("select set_config('app.current_org', %s, true)", (org_id,))
```

A recycled connection always starts with no tenant, and no tenant means deny by
default. The reasoning is in the module docstring of
[db.py](../../../knowledge_desk/db.py), and the test asserting it is in
[test_governance.py:166-202](../../../tests/test_governance.py#L166-L202), which
checks both that an unscoped query returns zero rows and that pooled reuse does
not inherit context.

The obligation this creates is that every statement inside a `connect(org_id)`
block has to run in the same transaction. Commit in the middle and you drop the
tenant context for everything after it. psycopg's implicit transaction handling
makes that the default, so the trap only opens if someone adds an explicit commit
for a reason that seems good at the time.

Two more things at this level.

There is a deliberate exception. The `jobs` table carries an `org_id` and has no
RLS policy. A worker claims the next due job without knowing whose it is, so it
cannot set the tenant GUC before the claim, and a policy would make the queue
unreadable to the only process that drains it. The isolation happens immediately
after: the claimed job's `org_id` becomes the tenant context for the work, so
`process_ingest_document` runs inside the same RLS the API does. A job row holds
a document id, never document content. That reasoning is written at the top of
[jobs.py](../../../knowledge_desk/jobs.py), and the fact that it is written down is
the point. An unexplained exception to a security rule is indistinguishable from
a mistake.

And the uncomfortable property, which is the best thing in this repo. Delete
layer three entirely and every test passes, every eval passes, and the
application behaves identically. Layers one and two are doing the work in the
happy path. Layer three only ever matters on the day one of them has a bug, which
is a day that has not happened yet. There is an exercise built on exactly this,
[03-remove-the-invisible-layer.md](../exercises/03-remove-the-invisible-layer.md),
and I think it is the most valuable ninety minutes in the whole course, because
it makes concrete something everyone repeats and few people have felt: defense in
depth is invisible from behaviour, so behaviour cannot tell you whether you still
have it.

---

## Level 4: engineering manager interviewing for AI engineering

Multi-tenancy is the risk that actually ends companies in this space, and it is
not AI-specific at all, which is exactly why it gets neglected on AI teams. The
team is busy with the interesting part.

The framing I would use in an interview or a design review: there is one property,
"customer A's data cannot reach customer B", and the question is how many
independent things have to fail before it breaks. If the answer is one, you do not
have a security design, you have a correct implementation, and those are different
words for a reason.

This system says three:

The application layer routes every org-scoped query through a single class that
stamps the org id. The value is not that it is clever, it is that it makes
leakage a code-review target. One file to watch, one rule to state.

The retrieval query filters and ranks together, so forbidden rows are never
scored.

The database refuses by default. Row-level security with a policy keyed on a
per-transaction variable. Miss the filter, get zero rows.

Questions worth asking a candidate, roughly in order of how much they separate
people:

"How many independent controls stop cross-tenant access, and what does each one
catch that the others miss?" You are listening for whether they can name the
failure each layer is for. "We use RLS" is a technology. "RLS catches a handler
that forgot its filter, the scope class catches a developer writing raw SQL, the
query-level ACL catches a permission mistake inside a single tenant" is a design.

"If you use row-level security, which database role does your application connect
as?" This is a small question with a big tell. Superusers and table owners bypass
RLS. Plenty of teams have RLS enabled and connect as the owner, and their
policies do nothing at all. Somebody who has actually deployed it knows this,
usually because it bit them.

"How do you know row-level security is still working?" The honest answer is
"a test asserts an unscoped query returns nothing", and the very honest answer
adds "because deleting it changes no behaviour, so nothing else would tell us".

"What happens to your tenant context when a connection goes back to the pool?"
Fairly advanced, and a strong signal. This is a real bug shape that has burned
real teams.

The management-level version of all this: budget for the layer nobody can
demo. Defense in depth costs review time and delivers nothing visible, so it
loses every prioritisation argument on its merits. It has to be a standard, not a
proposal.

---

## Level 5: senior AI engineer

Nothing here is novel and that is the point, but a few specifics are worth
lifting.

`force row level security` plus a dedicated `kd_app` role, with migrations
running as owner. The migration creates the role, grants table and sequence
privileges, and sets `alter default privileges` so later migrations inherit the
grants. That last line is the kind of thing that is missing right up until the
migration that adds a table nobody can read. LESSONS §8 and §22 record the
managed-database version of this problem, where the default role you are given
bypasses RLS and your entire third layer evaporates on deploy while remaining
present in the source tree.

The GUC scope contrast in [db.py](../../../knowledge_desk/db.py) is the detail I
would put in front of anyone building multi-tenant Postgres.
`app.current_org` is transaction-scoped because pooled reuse of it is a leak.
`hnsw.iterative_scan` is session-scoped on the same connection because it is
identical for every tenant and pooled reuse is correct. Same file, adjacent
lines, opposite answers, both explained. The scope of a setting is a security
property, not a convenience.

The policy uses `current_setting('app.current_org', true)` with the missing-ok
flag, so an unset GUC yields null rather than raising, and null yields no rows.
Deny by default falls out of three-valued logic rather than an explicit branch,
which is elegant but does mean the failure is quiet. Migration 0008 exists
specifically to handle the empty-string case, which is the exact edge you hit
when something sets the GUC to `''` instead of leaving it unset.

The `jobs` exception is correctly reasoned and correctly documented, and I would
still call it the softest part of the design. The claim is unscoped by necessity,
the payload is a document id rather than content, and isolation resumes at
`connect(org_id)` inside the handler. The residual is that a bug in job dispatch
routes work under the wrong tenant context, and RLS would then faithfully enforce
the wrong org. Nothing catches that today. A cheap improvement would be
asserting, inside `process_ingest_document`, that the document row it reads
actually belongs to the org it was handed, which is one extra predicate and turns
a silent misroute into a dead-lettered job.

What I would want before calling this production-grade for a real customer base:
a periodic reconciliation job that samples rows and asserts `org_id` consistency
across `documents`, `chunks`, and the denormalised `chunks.acl`, because the
denormalisation from
[migrations/0009_chunk_acl.sql](../../../migrations/0009_chunk_acl.sql) creates a
second place for tenancy and permission to disagree. The three layers all protect
against forgetting a filter. None of them protect against two copies of the truth
drifting apart.
