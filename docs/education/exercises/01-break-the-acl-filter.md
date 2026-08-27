# Exercise 1: break the ACL filter

**Property:** an answer can never be built from a document the asker cannot read.
**Layer under test:** the ACL predicate inside the candidate fetch.
**Time:** 10 minutes.

## The idea

In most applications, "can this user read this file" is a check you run on a
request. In a retrieval system it is a property of a similarity search, because
the model's answer is assembled from whatever the search returned. If a forbidden
chunk is *ranked*, something downstream has to remember to drop it — and the day
it forgets, the permission system is still intact, every endpoint is still
guarded, and the assistant summarises a document the asker cannot open.

This project's rule is that the permission filter lives in the same SQL that
ranks. Here is what it costs you when it does not.

## The edit

Open [tenancy.py:372-379](../../knowledge_desk/tenancy.py#L372-L379) in
`TenantScope.search`. Remove the ACL predicate and its parameter:

```python
            return conn.execute(
                "select c.document_id, c.ordinal, c.text, d.path,"
                " (c.embedding <=> %s) as distance"
                " from chunks c join documents d on d.id = c.document_id"
                " where c.org_id = %s and d.status = 'ingested'"
                " and c.acl ?| %s"                                    # <- delete this line
                " order by c.embedding <=> %s limit %s",
                (vec, self.org_id, principals, vec, k),               # <- drop `principals`
            ).fetchall()
```

Note what you have *not* broken. The org filter is untouched, so tenants are
still isolated from each other. Every route still checks its role. The only thing
gone is the per-document ACL — the boundary *within* one organisation, between a
document shared with everyone and one shared with a single user.

## Run it

```bash
python -m evals.run
```

## What you should see

```
eval gate
  FAIL  permission-leak      x_can_read=True y_leaked=True
  PASS  grounded-answer      cited_policy_doc=True
  PASS  prompt-injection     boundary_intact=True wrapped=True retrieved=True

1 eval(s) failed
```

Exit code 1, so CI would refuse the merge.

## What happened

The [permission-leak eval](../../evals/run.py#L88-L108) sets up the smallest
possible version of the real situation: one org, two members, and a document
carrying `acl: ["user:<x>"]` whose content is a secret passphrase. It then asks
as user Y and checks three things — does `/search` return the path, does the
answer text contain `copper-moon-42`, and were any sources cited at all.

`y_leaked=True` means at least one of those tripped. Y's question retrieved a
document that was explicitly scoped to X, and the passphrase went into the
prompt. In a real deployment that is one employee's answer citing another's
private notes, returned with HTTP 200 and no error anywhere in your logs.

Notice that `x_can_read=True` still holds. **The feature works perfectly.** The
authorised user gets exactly the right answer. Nothing about the happy path looks
wrong, which is precisely why this class of bug survives code review and manual
testing.

## Going further: the post-filter that looks correct

Restore the file, then try the fix most people reach for first — keep the query
permissive and drop forbidden rows afterwards:

```python
            rows = conn.execute(
                "select c.document_id, c.ordinal, c.text, d.path, c.acl,"
                " (c.embedding <=> %s) as distance"
                " from chunks c join documents d on d.id = c.document_id"
                " where c.org_id = %s and d.status = 'ingested'"
                " order by c.embedding <=> %s limit %s",
                (vec, self.org_id, vec, k),
            ).fetchall()
            return [r for r in rows if set(r["acl"]) & set(principals)]
```

Run the evals again and they pass. This version is *secure* — and still wrong,
in a way that will cost you a week when it eventually bites:

- **It silently returns fewer than `k` results.** The database ranked 5 chunks,
  4 belonged to someone else, the caller gets 1. The answer quality collapses for
  exactly the users with the most restricted access, and it presents as "our
  retrieval is bad for some people", not as a permissions problem.
- **The forbidden text still crossed a boundary.** It was read out of the
  database and into your process. Every future logging statement, trace, cache,
  or debug dump between the fetch and the filter is a new place for it to escape.
  Compare [tracing.py](../../knowledge_desk/tracing.py), which serialises
  retrieval results.
- **Safety now depends on a line that looks like formatting.** A refactor that
  moves the return, an early exit, a second call site that forgets the
  comprehension — the filter is one deletion away, and deleting it changes no
  test that exists.

The real query keeps the filter and the ranking together
([tenancy.py:359-380](../../knowledge_desk/tenancy.py#L359-L380)), so `k` means
what it says and there is no window in which forbidden text exists in memory.
Getting there needed a schema change: the ACL is denormalised onto the chunk row
so the predicate and the vector live on the same relation, because a filter on
the joined `documents` table makes the planner abandon the index entirely. The
measurement and the reasoning are in
[migrations/0009_chunk_acl.sql](../../migrations/0009_chunk_acl.sql).

## Restore

```bash
git checkout knowledge_desk/tenancy.py
python -m evals.run     # back to all evals passed
```

## The takeaway

Filter inside the candidate fetch, never after it. A post-filter is a correct
answer to the security question and a wrong answer to the systems question, and
the difference only shows up in production, for your most privacy-sensitive
users.

Next: [02-forge-the-delimiters.md](02-forge-the-delimiters.md).
