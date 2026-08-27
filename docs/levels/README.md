# Five levels

Every concept in this repo is explained five times, to five readers, in one
file. The format is stolen from the Wired video series: the same idea, explained
to a child, a teenager, an undergraduate, a graduate student, and an expert,
where each pass is not a longer version of the last one but a different
conversation with different things at stake.

The five readers here are engineers, not children, so the ladder is shifted up.

| Level | Reader | Assumed | The boundary |
|---|---|---|---|
| 1 | High school intro-to-CS student | Variables, loops, functions, lists. Maybe a little Python. | No databases, no HTTP, no linear algebra. Analogies carry the weight, and I say so when one breaks. |
| 2 | Second-year CS undergraduate | Data structures, Big-O, relational tables and SQL, how a web request works. | Vectors are geometry, not machine learning. Nothing assumes a model has ever been trained. |
| 3 | CS graduate learning AI | Embeddings, cosine distance, transformers at the level of "attention over tokens", can read a paper. | Knows the model. Has not run one in front of paying strangers. This is where the operational surprises land. |
| 4 | Engineering manager interviewing for AI engineering | Systems, incidents, headcount, budgets. Thin on AI specifics. | Vocabulary, failure modes, what a good answer sounds like, what a bad one hides. Written to be usable in an interview the same week. |
| 5 | Senior AI engineer | All of it. | No explaining. Sharp edges, measured numbers, what we got wrong, what is still wrong. |

## Why the ladder is shaped this way

Level 3 is the interesting break. A graduate who understands attention and
contrastive embedding objectives still has no reason to know that a permission
filter applied after a nearest-neighbour search returns fewer results than you
asked for, silently, forever. That is not a harder version of the machine
learning. It is a different subject that happens to sit next to it.

Level 4 is the other break, in the opposite direction. A manager does not need
the SQL. They need to know that "we filter the results by permission" and "we
filter inside the ranking query" are different sentences, and that only one of
them is safe. That difference is interviewable. Most of level 4 is written as
questions worth asking and the answers that should worry you.

## The concepts

| # | Concept | The one sentence |
|---|---|---|
| 0 | [The system](00-the-system.md) | What Knowledge Desk is, and why 96% of it is not the AI part. |
| 1 | [Retrieval and access control](01-retrieval-and-acl.md) | Finding the right passage, and never finding one you are not allowed to read. |
| 2 | [Tenant isolation](02-tenant-isolation.md) | Three independent walls between two customers' documents. |
| 3 | [The model call](03-the-model-call.md) | Grounding, refusal, and a document that tries to give the model orders. |
| 4 | [Ingestion and jobs](04-ingestion-and-jobs.md) | Turning uploaded files into searchable vectors without blocking a request. |
| 5 | [Cost and limits](05-cost-and-limits.md) | Why a spend cap checked afterwards is an invoice. |
| 6 | [Observability and audit](06-observability-and-audit.md) | Knowing what the system did, without leaking what it did it to. |
| 7 | [Evals and CI](07-evals-and-ci.md) | The tests that block a merge because they are not tests. |
| 8 | [Auth and roles](08-auth-and-roles.md) | Who you are, what rank you hold, and what your login timing gives away. |

## How to read these

Read one concept at your own level first. Then read the level above it. The gap
between the two is usually the thing worth learning, and it is often smaller
than it looks.

If you are here to teach, the level 1 and level 2 passes are self-contained and
work as reading assignments. If you are here to be interviewed, read every level
4 pass back to back, then read [01-retrieval-and-acl.md](01-retrieval-and-acl.md)
at level 5 to see what you were skating over.
