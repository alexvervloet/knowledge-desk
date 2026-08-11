# Learning from Knowledge Desk

A course built out of a working system. Knowledge Desk is a multi-tenant,
permissions-aware knowledge assistant that is deployed and green in CI; these
docs turn it into something you can learn the operational side of AI engineering
from, without first having to reverse-engineer the whole thing.

## Who this is for

Engineers who can code but have not shipped an LLM application. You are
comfortable with Python and SQL, you have probably built a RAG demo that worked
on your laptop, and you have not yet had to answer questions like "which of
these two customers is this chunk allowed to reach", "what stopped that user
from spending $4,000 last night", or "how do you know the last commit didn't
reintroduce the leak".

You do **not** need prior experience with FastAPI, pgvector, or LLM APIs. Every
concept is anchored to a file and a line number you can open.

## What this teaches, and what it does not

**Teaches:** access-controlled retrieval, tenant isolation in depth, indirect
prompt injection defense, evals as a merge gate, cost attribution and hard spend
caps, background embedding with a real job queue, tracing an LLM call, and the
failure modes that only show up once other people's documents are in your index.

**Does not teach:** model training or fine-tuning, agents and tool use,
multi-turn conversation, chunking and reranking research, or non-text ingestion.
[04-rag-core.md](04-rag-core.md) explains why the retrieval core here is
deliberately plain, which is itself one of the lessons.

## The path

Work in order the first time. Steps 1–3 are reading; step 4 is where the
learning actually happens.

| # | Doc | What you get | Time |
|---|---|---|---|
| 0 | [Root README](../README.md) → "Run it locally" | The stack running on your machine. It runs keyless with a mock provider, so you need no API keys. | 20 min |
| 1 | [01-thesis.md](01-thesis.md) | Why the interesting part of an LLM product is not the model call. Read this before the code or the code looks over-engineered. | 10 min |
| 2 | [WALKTHROUGH.md](../WALKTHROUGH.md) | A narrated trip through one question, end to end, with the branch points and gotchas. | 30 min |
| 3 | [02-concept-index.md](02-concept-index.md) | Concept → file → line. Use it as a lookup table for the rest. | skim |
| 4 | **[exercises/](exercises/)** | **Break each safety property and watch what catches you.** The core of this course. | 90 min |
| 5 | [03-evals.md](03-evals.md) | Why an eval is not a test, and why these three block merges. | 20 min |
| 6 | [04-rag-core.md](04-rag-core.md) | What a production retrieval stack adds, and why this one deliberately stops early. | 15 min |
| 7 | [05-reading-the-history.md](05-reading-the-history.md) | How to mine 207 commits and 29 recorded mistakes for the decisions behind the code. | 30 min |

## The shortest useful version

If you read one thing, read [01-thesis.md](01-thesis.md). If you do one thing,
do [exercises/03-remove-the-invisible-layer.md](exercises/03-remove-the-invisible-layer.md),
which is the exercise that surprises experienced engineers.

## Ground rules for the exercises

Every exercise breaks working code on purpose. They are all built the same way:

1. You make a small, specific edit.
2. You run a command.
3. You compare what happened to the output recorded in the doc.
4. You restore with `git checkout <file>` — always given explicitly.

Every failure quoted in these docs was produced by actually running it, not
predicted. If your output differs, that is worth investigating rather than
assuming the doc is right.

## Honest limitations of this material

This is one system's opinionated answer, not a survey. It uses Postgres for
everything (vectors, queue, audit) because a single dependency is easier to
reason about, not because that is always right. It defends a tenant boundary
hard and treats retrieval quality as out of scope. A different product would
weigh those the other way, and [04-rag-core.md](04-rag-core.md) says so plainly.
