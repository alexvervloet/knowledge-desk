# Ingestion and jobs

Turning uploaded files into searchable, permission-tagged chunks, without making
the person who clicked upload wait for it, and without losing work when something
fails halfway.

---

## Level 1: high school intro to CS

Uploading a document is fast. Getting it ready to be searched is slow.

Getting it ready means chopping it into chunks and then turning each chunk into
its list of numbers, and that second part means asking another company's computer
over the internet, once per batch of chunks. A big handbook might be hundreds of
chunks. That could take a while.

If the program did all of that before answering the upload request, the person
who clicked upload would sit watching a frozen page, and if their browser gave up
waiting, the work might be half done with nobody left to finish it.

So the work gets split in two.

The upload request does the fast part. It saves the text, and it writes a note in
a list that says "somebody needs to process this document". Then it answers
immediately.

A second program, running all the time on its own, reads that list. It takes the
oldest note, does the slow work, and crosses the note off. Then it takes the next
one. When the list is empty it waits two seconds and looks again. That program is
[worker.py](../../../knowledge_desk/worker.py) and it is 52 lines long.

The list is called a queue and this pattern is everywhere. Your food delivery
order, the video you uploaded, the photo being backed up. Anything where the app
says "we'll let you know when it's done" is doing this.

Two nice touches worth knowing about.

If you upload the same file twice without changing it, nothing happens the second
time. The program keeps a fingerprint of the text, and if the fingerprint matches
what it already has, it skips the whole thing. That is free, and it saves real
money, because the slow step costs money per use.

If the slow work fails, say the other company's computer is down, the note does
not get crossed off. It gets put back on the list to try again in a few seconds,
then a bit longer, then longer still. After a few tries it gives up and marks the
note as broken, so a human can look at it. What it never does is quietly forget.

---

## Level 2: second-year CS undergraduate

The split is in [ingest.py](../../../knowledge_desk/ingest.py). `sync_documents`
runs on the request path. `process_ingest_document` runs in the worker.

`sync_documents` is a reconcile, not an append. The uploaded set is the complete
desired state for that source: paths that are new or changed get enqueued, paths
that are unchanged are left alone, and paths that are absent get marked deleted.
That is a meaningfully different API from "add this document", and it is the
right one, because it makes re-running a sync harmless.

Change detection is a sha256 of the content,
[`_hash`](../../../knowledge_desk/ingest.py#L28-L30). Byte-identical content costs
nothing: no chunking, no embedding, no API call. On a corpus that mostly does not
change, a nightly sync is close to free.

The queue is a Postgres table. No Redis, no RabbitMQ, no Celery. The claim is one
statement, in [jobs.py:48-63](../../../knowledge_desk/jobs.py#L48-L63):

```sql
update jobs set status = 'running', attempts = attempts + 1, updated_at = now()
where id = (
  select id from jobs
  where status = 'queued' and run_after <= now()
  order by created_at
  for update skip locked limit 1)
returning id, org_id, kind, payload, attempts, max_attempts
```

`for update skip locked` is the piece to understand. `for update` locks the row it
selects. `skip locked` tells Postgres to pass over rows already locked by another
transaction instead of waiting on them. So ten workers running this at once each
claim a different job, with no coordination and no broker. The database is already
solving mutual exclusion for you, and you are allowed to use it.

Retry, in [jobs.py:73-101](../../../knowledge_desk/jobs.py#L73-L101): on failure,
if attempts remain, the row goes back to `queued` with `run_after = now() +
backoff`, where backoff is `min(300, 2 ** attempts)` seconds. Exponential, capped
at five minutes. When attempts run out, status becomes `dead`, which is the
dead-letter state: the job stops retrying and stays visible for a human.

Delivery is at-least-once, not exactly-once. A worker can claim a job, do the work,
and die before marking it succeeded, in which case the job runs again. So the work
has to be idempotent, and it is:
`process_ingest_document` deletes the document's chunks and reinserts them, so
running it twice leaves the same rows. Enqueueing is idempotent too, through a
unique `idempotency_key` of `ingest:{document_id}:{content_hash}:{revision}` and an
`on conflict do nothing`. The revision is in there because content reverting to
an earlier hash still has to re-embed, and a key without it would be treated as
already done.

That combination, at-least-once delivery plus idempotent handlers, is how almost
all real queue systems work. Exactly-once is mostly a marketing claim about
someone else's at-least-once plus deduplication.

---

## Level 3: CS graduate learning AI

The parts specific to an embedding pipeline, rather than to queues in general.

Ingestion is where money is spent per byte. Embedding is a paid API call
proportional to corpus size, so the hash check at
[ingest.py:66-76](../../../knowledge_desk/ingest.py#L66-L76) is not a tidiness
feature, it is the difference between a sync that costs nothing and a sync that
re-embeds a 50,000 chunk corpus every night. Any RAG system without content-hash
change detection is quietly burning money on a schedule.

The `zip(texts, embeddings, strict=True)` at
[ingest.py:152-156](../../../knowledge_desk/ingest.py#L152-L156) deserves the
comment it has. Without `strict`, a short embedding list truncates silently, the
document is marked ingested holding a subset of its chunks, and you have a
permanent invisible hole in retrieval for that document. Nothing downstream would
ever have a reason to retry it. The document looks fine in the UI, answers about
it are just mysteriously worse, and no error was ever raised. Raising instead
sends it back through the queue and eventually dead-letters it where somebody can
see it.

I would generalise that: in an embedding pipeline, prefer failures that are loud
and recoverable over degradations that are silent and permanent. Retrieval
quality has no exception type, so the only errors you get are the ones you write.

The ACL is written onto the chunk at insert time, denormalised from the parent
document, because filtered vector search needs the predicate on the same relation
as the vector. That is the retrieval story in
[01-retrieval-and-acl.md](01-retrieval-and-acl.md) reaching back into ingestion,
and it creates a synchronisation obligation: changing a document's permissions
has to rewrite its chunks' ACLs. `update_document_acl` does that.

Chunks are inserted with `executemany`, one round trip, not `COPY`. The comment
says COPY would be faster and is unavailable because RLS forbids it, with the
detail in LESSONS §15. That is a real and non-obvious interaction: the fastest
bulk-load path in Postgres is not available to a table under row-level security,
so a security decision made in migration 0007 sets a ceiling on ingestion
throughput. Those two decisions were made months apart in different files, and
nothing would have warned you.

Failure containment is per document, at
[ingest.py:203](../../../knowledge_desk/ingest.py#L203). One document that cannot be
embedded, perhaps because the provider rejects its content, marks itself failed
and the rest of the batch proceeds. The alternative, failing the batch, means one
malformed file blocks a customer's entire corpus, which is the sort of thing that
turns into a support ticket about "search is broken".

What is missing, and would matter at scale: there is no batching across
documents. Each document is one job and one embedding call, so a sync of 500 tiny
files makes 500 API calls when the provider would happily take them in batches of
128. There is also no backpressure between enqueue and the worker, so a large
sync can build a deep queue with nothing but the poll interval controlling drain
rate.

---

## Level 4: engineering manager interviewing for AI engineering

This section is mostly ordinary systems engineering, which is why it is worth
your attention: it is the part of an AI product you can already evaluate
competently, and it is where a lot of the real reliability lives.

The shape to expect. Uploading and indexing are separate. The request path
records the work, a background worker does it. If a candidate describes embedding
documents inline in the upload request, that is a system that falls over on its
first real customer, and the failure looks like timeouts rather than errors.

The three questions worth asking:

"What happens when embedding fails halfway through a large document set?" You
want to hear retries with backoff, a dead-letter state, and per-document
containment rather than per-batch. The thing you are really testing is whether
they have run this in anger. People who have will volunteer the dead-letter queue
without being asked, because they have had to go and look at one.

"If I re-upload the same 10,000 documents tomorrow, what does it cost?" The right
answer is close to nothing, because content hashing detects that nothing changed.
If the answer is "we re-embed everything", you have found a recurring bill nobody
has costed. This is one of the most common real money leaks in production RAG,
and it is invisible because it is a steady spend rather than a spike.

"Do you need Kafka or Redis for the queue?" Watch for reflexive complexity. A
Postgres table with `SELECT ... FOR UPDATE SKIP LOCKED` gives you durable,
concurrent, retrying, at-least-once delivery, and this project's whole queue is
101 lines. At the volumes most companies have, that is the correct answer, and it
removes an entire piece of infrastructure from your on-call rotation. A candidate
who reaches for a broker should be able to say what volume justifies it.

The vocabulary you will be expected to use without stumbling: idempotency (doing
it twice has the same effect as doing it once), at-least-once delivery, dead
letter, exponential backoff, backpressure. None of these are AI terms. All of them
will come up in an AI systems interview.

One operational number to ask any team about: how far behind is the queue right
now. If nobody can answer, indexing lag is unmonitored, and indexing lag is what
users experience as "I uploaded that an hour ago and it can't find it".

---

## Level 5: senior AI engineer

The queue is 101 lines and correct for its scale. Claim by `for update skip
locked`, exponential backoff capped at 300s, dead letter on attempt exhaustion,
idempotency key on enqueue. Nothing to argue with in the mechanism.

The `jobs` table's exemption from RLS is documented at the top of
[jobs.py](../../../knowledge_desk/jobs.py) and the argument is sound: the claim is
necessarily tenant-blind, and the tenant context is established after the claim
from the job's own `org_id`. The residual risk is that nothing verifies the
document actually belongs to the org the job named. `process_ingest_document`
does select `where id = %s and org_id = %s`, so a mismatch yields `None` and the
handler returns quietly as if the document had been deleted. That is safe but
indistinguishable from the legitimate deleted case, so a systematic misroute
would look like a stream of no-op jobs rather than an alarm. Cheap fix, and I
would take it.

Things I would change or watch:

No cross-document batching. One job per document, one `embed_documents` call per
job. Voyage takes batches; a sync of many small files leaves most of the
provider's throughput on the table and pays per-request latency repeatedly. The
job granularity is right for failure containment, so the fix is a batching layer
in the worker rather than coarser jobs.

The worker polls every two seconds. Fine here, and `LISTEN/NOTIFY` would cut
indexing latency to near zero for the interactive case at the cost of a
notification path you then have to reason about when it is missed. Given the
queue is already the source of truth, notify-as-a-hint with the poll as the floor
is the version worth building.

`executemany` rather than `COPY`, because RLS forbids COPY (LESSONS §15). This is
the most interesting constraint in the file, because it is a case of a security
control setting a throughput ceiling through a mechanism nobody would predict
from either decision alone. If ingestion throughput ever becomes the problem, the
options are a separate non-RLS staging table with a trusted move, or accepting
the ceiling. Both are worse than they sound, which is a good reminder that
defense in depth has costs you find later.

The chunk replace is `delete` then `executemany` inside one transaction, so
retrieval sees either the old set or the new set, never a partial one. Correct,
and worth noting because the obvious alternative, delete-commit-insert, gives you
a window where a document exists and is unsearchable.

`run_pending(max_jobs=1000)` is the worker's inner step and is also what tests
call instead of running a worker. I like that: the same code path in both, no
test-only drain logic. The bound exists so one call cannot spin forever, and the
loop's cost is a claim query per iteration even when the queue is empty, which is
the poll.

Session purging rides in the worker on an hourly timer, with the comment that
expired sessions are refused on sight so staleness costs nothing and the worker
is simply the process already awake. That is the correct reasoning for putting an
unrelated chore in a loop, and it is written down, which is the part most people
skip.
