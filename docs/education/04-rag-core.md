# The RAG core, and why it stops early

The retrieval pipeline here is deliberately plain. This document says exactly how
plain, what that gives up, and why the complexity budget went elsewhere — because
"we chose not to build that" and "we didn't know about that" look identical in a
codebase, and only one of them is engineering.

## What is actually here

| Stage | Implementation | Config |
|---|---|---|
| Chunking | fixed character windows with overlap ([chunking.py](../knowledge_desk/chunking.py)) | `chunk_size: 1000`, `chunk_overlap: 150` |
| Embedding | Voyage `voyage-3`, 1024 dims, or a deterministic mock ([embeddings.py](../knowledge_desk/embeddings.py)) | `embed_model` |
| Index | none — exact scan with cosine distance | see below |
| Query | embed, ACL-filtered nearest neighbours ([tenancy.py:359-380](../knowledge_desk/tenancy.py#L359-L380)) | `retrieval_k: 6` |
| Rerank | none | |
| Threshold | none | |

That is the entire retrieval stack. Six knobs and no stages you did not expect.

## What each omission costs

### Fixed-window chunking

Splitting on character count cuts sentences in half, separates a heading from the
paragraph it introduces, and splits tables down the middle. The 150-character
overlap is a hedge: it means a fact straddling a boundary probably appears intact
in one of the two neighbouring chunks.

**What production adds:** token-aware splitting (so a chunk fits the embedding
model's window exactly rather than approximately), and structure-aware splitting
that prefers to break on headings, paragraphs, or list items.

**When it matters:** immediately, for structured documents — anything with
tables, code, or nested headings. Much less for the prose this system targets
(handbooks, policies, runbooks), where a 1000-character window usually contains a
whole topic anyway.

### No reranking

Retrieval returns the top 6 by cosine distance and those go straight into the
prompt. A reranker would fetch 50 candidates and use a cross-encoder to reorder
them, which is meaningfully more accurate because it scores the query and the
passage *together* rather than comparing two independently-computed vectors.

**When it matters:** when your corpus is large enough that the right passage is
often in the top 50 but not the top 6. On a per-tenant handbook it is usually
already at rank 1.

**The wrinkle here:** a reranker fetching 50 candidates makes the ACL filter more
important, not less. Every one of those 50 must already be permitted, or you have
built a very efficient way to pull forbidden text into your process. See
[exercise 1](exercises/01-break-the-acl-filter.md).

### No hybrid search

Pure vector search is bad at exact tokens: error codes, part numbers, surnames,
`ERR_4021`. Embeddings capture meaning, and an identifier has no meaning to
capture. The standard fix is to run BM25 alongside vector search and fuse the
rankings.

**When it matters:** as soon as users search for identifiers. It is the single
most common "why can't it find the thing I literally typed" complaint.

### No relevance threshold

This is the one that hurts most, and the project says so plainly in
[WALKTHROUGH.md](../WALKTHROUGH.md). Nearest-neighbour search always returns
neighbours. Ask about something the corpus knows nothing about, and you get the 6
least-unrelated chunks with full confidence, plus citations that look
authoritative.

It is a strange gap given the rest of the system's posture: enormous effort went
into making the assistant refuse when it retrieves *nothing permitted*
([assistant.py:90-96](../knowledge_desk/assistant.py#L90-L96)), and no effort
into refusing when it retrieves *nothing relevant*. Both should end in the same
honest empty answer.

**This is the recommended first contribution.** It is one `where` clause on one
query, and the eval to go with it is sketched at the end of
[03-evals.md](03-evals.md).

## The interesting one: no vector index

You would expect an HNSW index. There was one, in migration 0010, and migration
0011 removes it again. The reason is the most genuinely surprising finding in the
project, and it is a direct consequence of the security design.

Measured on 100k chunks, on a laptop:

| Configuration | p50 | p95 |
|---|---|---|
| Exact scan, RLS on | 569ms | 2272ms |
| HNSW index, RLS on | moved by 6ms | — |
| HNSW index, RLS bypassed | 367ms | — |

Adding the index moved p50 by six milliseconds, because the planner never used
it. The same query, same data, same session settings, run as a role that
bypasses row-level security, *did* use the index and returned in 367ms.

The write side made the decision easy: loading 100k chunks took 28 seconds
unindexed, and building the index afterwards took a further 355 seconds — with
every ongoing insert paying a graph insertion after that.

**Row-level security stops the planner from using the index.** The RLS policy is
a predicate the planner must satisfy, and it cannot stream ordered rows out of an
HNSW graph while also proving the policy holds. So it falls back to scanning.
Under RLS the index was pure cost — build time, write amplification, disk — for
no read benefit, and it was removed.

The investigation had two false leads, and they are worth more than the
conclusion ([LESSONS.md](../LESSONS.md) §17). The first guess was that the ACL
filter was to blame — half right, and fixing it did not fix the problem. The
second was that pgvector's `cosine_distance` not being marked `LEAKPROOF` blocked
the planner from pushing it below a security barrier: a tidy, plausible
explanation that turned out to be simply wrong, proved by marking it `LEAKPROOF`
and watching nothing change. Two convincing hypotheses, one correct diagnosis
only because both were tested rather than adopted.

A related finding is recorded in
[migrations/0009_chunk_acl.sql](../migrations/0009_chunk_acl.sql): the ACL filter
had to be *denormalised onto the chunk row* for the same class of reason. With
the predicate on the joined `documents` table, the planner abandons index-ordered
retrieval because the filter deciding which rows survive lives on a different
relation. Same lesson, one layer up.

**The general lesson:** your security architecture is a performance constraint,
and the two are not negotiable independently. This is not a Postgres quirk —
anything enforcing per-row visibility underneath an approximate-nearest-neighbour
index has the same problem, because ANN indexes work by *not* looking at most
rows, and per-row policies work by looking at each one.

If you outgrow the scan, the escape hatch is a deliberate security trade, not a
tuning knob: drop the RLS policy on `chunks`, recreate the index, and accept two
isolation layers instead of three — which is what most multi-tenant RAG systems
ship with anyway. The exact SQL and the reasoning are in
[WALKTHROUGH.md](../WALKTHROUGH.md). Do it knowingly, and keep the
permission-leak eval, because it becomes your last automated check.

## Why the complexity went elsewhere

Every project has a complexity budget. This one spent it on tenancy, access
control, cost, and evals, and left retrieval simple. Three reasons that are worth
weighing for your own work:

**The retrieval stack is the replaceable part.** Chunking, embedding, and ranking
are 126 lines behind a narrow interface. Swapping in token-aware chunking, a
reranker, and hybrid search is a contained afternoon's work that touches almost
nothing else. The tenancy model is not like that — get the isolation boundary
wrong and it is a rewrite, because every query, every index, and every migration
assumes it.

**Simple retrieval makes everything else legible.** Fixed windows and exact scan
are predictable in tests. When the permission-leak eval fails, the cause is a
permissions bug, never "the reranker reordered the candidates". Deterministic
retrieval is why the evals can be strict and non-flaky, which is what lets them
gate merges at all.

**The gap in the market is here, not there.** There are thousands of good
tutorials on chunking strategies and reranking. There are very few worked
examples of retrieval that respects per-document ACLs, of spend caps that survive
an abandoned stream, or of an eval gate that blocks a merge. That is the
[thesis](01-thesis.md).

## If you want to extend it

In the order I would actually do them:

1. **Distance threshold** — smallest change, fixes the most-cited weakness, turns
   "confidently wrong" into "honestly empty".
2. **Token-aware chunking** — swap `chunk_text` for a tokenizer-based splitter.
   Contained in one file with existing tests around it.
3. **Hybrid search** — add a `tsvector` column and fuse rankings. Postgres does
   BM25-ish full text natively, so this needs no new dependency. Keep the ACL
   predicate on *both* arms of the fusion.
4. **Reranking** — biggest quality win, biggest latency cost, and the one that
   most needs the ACL filter to be airtight first.

Each of those should arrive with an eval, for the reason in
[03-evals.md](03-evals.md): the property you cannot measure is the property you
will regress.

Next: [05-reading-the-history.md](05-reading-the-history.md).
