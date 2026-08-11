# Reading the history

The most valuable teaching material in this repo is not the code. It is the
record of how the code got that way: over 200 small commits, 29 written-up
mistakes in [LESSONS.md](../LESSONS.md), and a [CHANGELOG](../CHANGELOG.md)
tying them together.

Finished code tells you what someone decided. History tells you what they tried,
what broke, and what they believed right up until it did. Only one of those
teaches judgement.

## Why this repo is readable as history

Two habits, deliberately kept:

**Commits are small and single-purpose.** One file, or the smallest coherent set.
`feat`, `fix`, `test`, `docs`, `perf`, `refactor`, `ops`. This means a `git show`
is legible in one screen, and a change and its test are adjacent rather than
buried in a 40-file squash.

**Surprises are written down when they happen.** [LESSONS.md](../LESSONS.md) was
appended to at the moment each thing went wrong, not reconstructed afterwards.
That is why the entries contain the false leads — reconstruction quietly deletes
those, and the false leads are the most useful part.

## The pattern to look for

Notice the shape that recurs. It is almost always four commits, in this order:

```
feat:  build the mechanism the fix will need
fix:   change the behaviour
test:  pin it so it cannot come back
docs:  record why, in LESSONS.md
```

The `feat` coming *before* the `fix` is the interesting part. It usually means
the bug was not fixable with the data model as it stood — a schema or an
interface had to grow first. When you see that pair, you are looking at a place
where someone discovered their design could not express the correct behaviour.

## Five threads worth pulling

Each of these is a complete story: a wrong assumption, the discovery, the fix,
and the test that holds it. Read them with `git show <sha>`.

### 1. The pooled connection that inherits a tenant

**Lesson [§13](../LESSONS.md)** · `44f3dad`, `d009f9b`

The tenant context that row-level security keys on was set as a session
variable. Correct in every test, because tests opened fresh connections. Once
connections were pooled, a session-scoped setting survived the commit, rode the
connection back into the pool, and handed the *next* request the previous
tenant's org context.

The fix is one keyword — `set_config(..., true)` makes it transaction-scoped —
and the test (`d009f9b`) is the interesting artifact: it borrows repeatedly until
the pool demonstrably hands back the same connection object, so the assertion
actually exercises reuse instead of accidentally testing fresh connections. Read
that test before you write your own concurrency tests.

### 2. Retrieved documents are the untrusted input

**Lesson [§16](../LESSONS.md)** · `b7cbe85`, `8a18e94`

The moment the system indexed documents that users uploaded, the retrieved text
became attacker-controlled input flowing into a prompt. `b7cbe85` adds the
delimiters, the neutralisation, and the system-prompt language; `8a18e94` makes
it a merge gate.

Worth noting the gap between the two commits: the defense existed first, and
*then* someone made it impossible to remove silently. Both halves are the work.
You broke this in [exercise 2](exercises/02-forge-the-delimiters.md).

### 3. The index that row-level security made useless

**Lesson [§17](../LESSONS.md)** · `d13cd64` → `0423a8f` → `2034eac` → `dde39e2`

The best thread in the repo, because it is a negative result that was kept.

`d13cd64` adds an HNSW index, as any scalable vector project should. `0423a8f`
denormalises the ACL onto chunks after discovering that a filter on a joined
table defeats the index. `2034eac` **removes the index again**, having measured
that RLS stops the planner using it at all. `dde39e2` then makes the benchmark
able to A/B the index, so the negative result is reproducible rather than a
claim.

Read the two false leads in §17. The second — that `cosine_distance` not being
`LEAKPROOF` was the blocker — is a tidy, expert-sounding, completely wrong
explanation that was only killed by testing it. That is what debugging actually
looks like, and finished code never shows it.

### 4. The bill that arrives after the client hangs up

**Lesson [§26](../LESSONS.md)** · `f695719` → `2e5a5c8` → `f537014` → `7d1072e`

A textbook instance of the pattern above. A stream the client abandons never
reaches its usage frame, so it was never billed — meaning aborting each request
just before the end was free inference.

The fix needed two new capabilities first: a column recording whether usage was
estimated (`f695719`), and a provider method that can price usage it never got to
report (`2e5a5c8`). Only then could the fix land (`f537014`), followed by the
test (`7d1072e`). Four commits, and the ordering tells you the bug was a
missing concept, not a missing line.

### 5. Per-tenant caps that bounded nothing

**Lesson [§28](../LESSONS.md)** · `7d0f573` → `710a406` → `9afad42` → `cad86f2`

Per-org budgets were in place and working. Then someone asked what bounds the
*deployment's* spend when signup is open and every new org arrives with a fresh
allowance. The answer was nothing.

This is a reasoning bug, not a code bug — every line was correct — and it is the
kind that only surfaces when you state the property out loud and check whether
your controls actually imply it. You reproduced it in
[exercise 4](exercises/04-spend-without-a-ceiling.md).

## How to explore it yourself

```bash
# The shape of the whole build
git log --oneline

# Every bug fix, which is where the lessons live
git log --oneline --grep '^fix'          # 18 of them

# Everything that touched the most security-critical file
git log --oneline -- knowledge_desk/tenancy.py

# A single change, in full, with its diff
git show 44f3dad

# When did this line become what it is, and why
git log -L 359,380:knowledge_desk/tenancy.py
```

That last one is the most underused command in git. Point it at the ACL query
from [exercise 1](exercises/01-break-the-acl-filter.md) and you get every
revision of exactly those lines, with the commit message explaining each change.

## The habit worth stealing

The lessons file works because of *when* it is written. An entry added while the
surprise is fresh contains the thing you believed beforehand. An entry
reconstructed at the end of a project contains only the conclusion, tidied — and
the conclusion is the part you would have remembered anyway.

Three questions per entry:

- What did I expect?
- What actually happened?
- What will I do differently?

The first question is the one people skip, and it is the one that makes the entry
worth re-reading. §17's value is not "RLS blocks the planner" — it is that a
competent engineer spent hours on two convincing wrong theories first.

## Where to go from here

You have finished the path. Reasonable next steps, in order of how much you will
learn:

1. **Add the distance threshold** — write the eval first
   ([03-evals.md](03-evals.md)), watch it fail, then fix
   [`TenantScope.search`](../knowledge_desk/tenancy.py#L359-L380).
2. **Add an eval for a property you think is under-defended**, and find out
   whether it holds.
3. **Read [WALKTHROUGH.md](../WALKTHROUGH.md)'s "Where it will disappoint you"**
   and pick something from it. It is an honest defect list, which is rare enough
   in a portfolio project to be worth studying on its own.
