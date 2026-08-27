# Retrieval and access control

Finding the five passages most likely to answer a question, out of everything the
company has uploaded, while making it structurally impossible to find one the
asker is not allowed to read.

This is the most important concept in the repo. If you read one of these files,
read this one.

---

## Level 1: high school intro to CS

Think about a library where you are only allowed into some of the rooms.

There are two ways the librarian can help you find a book about refunds.

The bad way: she walks the whole library, finds the five books most about
refunds, brings them to the desk, then checks your card and takes back the ones
from rooms you cannot enter. You end up with two books. Maybe zero. And here is
the annoying part, there might have been a perfectly good refunds book in a room
you *are* allowed into, sitting at number six on her list, and you never got it,
because she stopped counting at five before she looked at your card.

The good way: she looks at your card first, and then only ever walks the rooms
you are allowed into. She still brings back five books. All five are ones you can
have, and they are genuinely the five best ones available to you.

Knowledge Desk does it the good way, and the whole trick is that the permission
check and the "find the closest matches" step happen in the same operation
instead of one after the other. In code that means one database query does both
things at once. You can read it at
[tenancy.py:359-380](../../knowledge_desk/tenancy.py#L359-L380). It is about
eight lines.

There is a second reason the good way is better, and it is the serious one. In
the bad way, forbidden books were on the desk. They existed in the program, in
memory, in a list, and the only thing keeping them from you was one more line of
code remembering to remove them. Every time a system holds something it should
not hand over, you are one forgotten line away from handing it over. In the good
way, that book was never picked up.

How does the librarian know which rooms you can enter? Every chunk carries a
little list of labels saying who may read it, like `everyone-at-this-company`, or
`user:sam`, or `group:hr`. And you carry your own list of labels: everyone-at-the
company, your own name, and one for each group you belong to. If any label on
your list appears on the chunk's list, you may read it. That is the whole rule.

---

## Level 2: second-year CS undergraduate

Two mechanisms, and the interesting part is that they run as one query.

The ranking. Each chunk has an `embedding` column, a 1,024-float vector. Postgres
with the pgvector extension gives you a distance operator, `<=>`, which is cosine
distance. `order by c.embedding <=> $query_vector limit 5` is a k-nearest-
neighbour search written in SQL. Exact evaluation is a linear scan, so there is an
HNSW index over that column to make it approximate and fast.

The permission filter. Chunks carry an `acl` column, a JSONB array of strings.
The caller's access set is computed by
[`principals()`](../../knowledge_desk/tenancy.py#L325-L340) and is exactly:

```
["public-to-org", "user:<their id>", "group:<g1>", "group:<g2>", ...]
```

The predicate is `c.acl ?| $principals`, where `?|` is Postgres's "does this
JSONB array contain any of these keys" operator. There is a GIN index on `acl`
supporting it. Set intersection, in a database, in one operator.

Put together:

```sql
select c.document_id, c.ordinal, c.text, d.path,
       (c.embedding <=> $1) as distance
from chunks c join documents d on d.id = c.document_id
where c.org_id = $2 and d.status = 'ingested'
  and c.acl ?| $3
order by c.embedding <=> $1
limit $4
```

Everything that matters is in the `where` clause sitting above the `order by`.
The filter runs as part of choosing candidates, so a forbidden row is never
scored, never ranked, and never present in any variable the application can
accidentally return.

Two details worth carrying out of this:

`principals()` runs on every single query and caches nothing. That looks wasteful
and it is deliberate. If you cache someone's group membership, then removing them
from the HR group leaves them reading HR documents until the cache expires, and
you now own a cache invalidation bug whose failure mode is a data leak. One extra
round trip per question is a good price.

The predicate reads `c.acl`, on chunks, even though permission is a property of
the document. The ACL is copied down onto every chunk of that document. You have
been taught that this is denormalisation and that denormalisation is a smell, and
usually it is. Here it is a performance requirement, and the reasoning is written
out at [migrations/0009_chunk_acl.sql](../../migrations/0009_chunk_acl.sql). The
short version: the filter has to live on the same table as the vector or the
index cannot help.

The API layer above this is deliberately tiny.
[retrieval.py](../../knowledge_desk/retrieval.py) is 17 lines, and its function
body is two: embed the query, hand it to the scope. There is nowhere in it to put
a security bug.

---

## Level 3: CS graduate learning AI

You know top-k dense retrieval. Here is the thing about it nobody puts in the
tutorial.

Filtered approximate nearest neighbour is not the same problem as approximate
nearest neighbour. And the naive composition of the two is wrong in a way that
never raises.

Take the obvious implementation: run the HNSW search for k=5, then apply the
permission predicate in Python.

```python
hits = vector_search(query_embedding, k=5)     # global top 5
return [h for h in hits if allowed(h, user)]   # maybe 2 survive
```

Three separate problems, in increasing order of nastiness.

You get fewer than k results. The user asked for five passages of context and got
two. Recall drops silently and the answer quality drops with it. Nothing logs a
warning, because from the code's point of view everything worked.

Documents the user cannot read were materialised. They were in `hits`. They were
in memory, in a list, one `return` statement away from the response. The entire
safety of the system now rests on that comprehension being present and correct,
forever, in every code path that ever calls this. That is not defense, it is
diligence, and diligence has a half-life.

The ranking itself was computed against a corpus the user has no right to. Even
if the filter is perfect, distances were computed over other people's data. In
some settings that is itself the leak: response timing, result counts, and
`retrieval_stats`-style diagnostics can all disclose the existence of documents.

Pushing the predicate into the candidate fetch fixes all three at once. This is
the same instinct as predicate pushdown in a query planner, and it is the same
instinct as capability-based security: do not acquire the thing and then check,
make the acquisition itself impossible.

Now the part that bites specifically because the index is approximate. HNSW walks
a proximity graph and returns roughly the k nearest. If you attach a selective
filter, the naive implementation collects k graph candidates and then discards
the ones failing the predicate, so you are back to short result sets, just one
layer lower. pgvector's answer is iterative scan, enabled here per connection in
[db.py](../../knowledge_desk/db.py):

```python
conn.execute("set hnsw.iterative_scan = relaxed_order")
```

That lets the index keep re-probing until enough rows survive the filter, at the
cost of some ordering strictness. Without it, a user in a small group gets
degraded retrieval quality permanently and invisibly, which is a genuinely
horrible bug to be handed in production because the system looks healthy the
whole time.

One more thing you will not have thought about: the gap between "chunks this org
has" and "chunks this caller may see" is itself a number worth recording.
[`retrieval_stats()`](../../knowledge_desk/tenancy.py#L341) computes both counts
and they land on the trace's retriever span. When someone reports that the
assistant is useless, the first question is whether retrieval failed or whether
that person is allowed to see eleven chunks out of nine thousand. Those look
identical from the outside and have completely different fixes.

---

## Level 4: engineering manager interviewing for AI engineering

This concept is, in my opinion, the single best interview question in the whole
subject, because it separates people who have shipped from people who have
followed a tutorial, and it does it in about ninety seconds.

Ask it like this: "Users can only see some documents. How do you make sure the
assistant only retrieves the ones they are allowed to see?"

The tutorial answer: "We do the vector search, then filter the results by
permission before showing them." Said confidently. It sounds right. It is the
thing almost every RAG tutorial actually does.

The shipped answer: "The permission predicate goes inside the same query that
does the ranking, so forbidden rows are never scored. If you filter afterwards
you get two problems: the user silently gets fewer passages than you asked for,
which quietly degrades every answer, and forbidden content is sitting in memory
one bug away from the response."

If they only give you the first, the useful follow-up is: "If the top five
results globally include three the user cannot see, what does the user get?" The
answer is two results, and watching a candidate work that out live tells you
whether they are reasoning or reciting.

The strongest candidates go one further without being asked, and mention that a
filtered approximate index needs configuration to keep returning k results at
all. That is a detail you only learn by having shipped a filtered vector search
and then wondered why answers got worse for exactly the people in small teams.

What to take back to your own team.

This is not an AI problem. It is the same class of bug as `SELECT *` followed by
a permission check in the template layer, which you have already had an incident
about. The vector search does not change the lesson, it just makes the leak
fluent and well-cited instead of a raw database row, which makes it much harder
to notice in a screenshot.

Two things worth requiring in review of any retrieval code:

The permission predicate and the ranking appear in the same query. If they are in
different functions, in different files, or worse in different layers, treat that
as a finding, not a style preference.

The caller's permissions are recomputed, not cached. Ask what happens between
removing someone from a group and them stopping being able to retrieve that
group's documents. If the answer contains the word "TTL", you have a disclosed
data-retention window nobody has written down.

One number to ask for: how many results did the user actually receive versus how
many you requested. If nobody is measuring that, nobody would notice the failure
described above.

---

## Level 5: senior AI engineer

The query is [tenancy.py:359-380](../../knowledge_desk/tenancy.py#L359-L380).
Filter and ranking in one statement, `c.acl ?| principals` alongside
`order by c.embedding <=> vec limit k`, org id on top as the tenant boundary.
Nothing surprising in the shape. The interesting content is in the three
decisions around it.

Denormalising the ACL onto chunks. Measured, not assumed. With the predicate on
the joined `documents` row, the planner abandons the HNSW index and sorts the
corpus, because the relation deciding row survival is not the relation carrying
the ordered index. Isolating each predicate showed the join itself, `org_id`, and
`status` all keep the index in play, and only the cross-table ACL check breaks
it. Full note in
[migrations/0009_chunk_acl.sql](../../migrations/0009_chunk_acl.sql).

The liability this creates is a synchronisation obligation: `update_document_acl`
has to fan a permission change out to every chunk of that document. That is the
sort of invariant that survives exactly as long as the person who wrote it stays
on the team. If I were hardening this, that is where I would put a property test,
not another eval.

There is also a nice operational detail buried in the same migration: the ACL
backfill runs before the HNSW index is built (0009 before 0010) because updating
a row on a table with a vector index reinserts that row's vector into the graph
even when the vector is unchanged. Measured at 100k chunks: still running after
thirteen minutes with the index present, seconds without. Build the graph after
the data settles. That generalises to any vector-indexed table you ever backfill.

Iterative scan. `hnsw.iterative_scan = relaxed_order`, session-scoped per
physical connection in [db.py](../../knowledge_desk/db.py). This sits directly
next to the transaction-scoped tenant GUC and the contrast is deliberate: this
setting is identical for every tenant so pooled reuse is correct, the tenant
context is not so pooled reuse would be a leak. Two settings, same connection,
opposite scopes, both right. I like that the reasoning is written where someone
changing it will see it.

Worth being honest about what relaxed order costs. You are no longer getting a
strict distance ordering out of the index, and for a selective ACL over a large
corpus you are paying repeated graph probes. Under a sufficiently narrow
principal set this degenerates toward a scan, and there is no alarm on that path.
A per-query candidate-visited metric would be the thing to add.

`principals()` uncached. One round trip per ask, no invalidation surface. The
docstring makes the correct argument, which is that a group change taking effect
on the next query is worth more than the saved query. The thing I would watch is
that it is called twice per ask when tracing is on, once in `search` and once in
`retrieval_stats`, and `retrieval_stats` additionally runs two `count(*)` queries
over chunks. That is fine at this corpus size and is exactly the kind of
diagnostic that quietly becomes a p99 problem at 10M chunks. It is at least
gated on `tracer.active`.

What this design deliberately does not do, per
[04-rag-core.md](../04-rag-core.md): no reranking, no hybrid sparse-dense, no
query expansion, no MMR for diversity, fixed-width chunks with no token
awareness. All of those would improve answer quality and none of them would
change the security properties, which is the argument for leaving them out of a
project about the security properties. The chunker is 33 lines and is meant to be
thrown away.

The exercise that breaks this on purpose is
[01-break-the-acl-filter.md](../exercises/01-break-the-acl-filter.md). Move the
predicate out of the query into a post-filter and watch what the eval catches and
what it does not.
