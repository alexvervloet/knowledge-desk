# Concept index

Concept → file → line. Use this as a lookup table; you are not meant to read it
start to finish. Line numbers were correct at the time of writing — if one has
drifted, the symbol name next to it will still find the code.

## The model call

| Concept | Where | What to notice |
|---|---|---|
| System prompt | [providers.py:27-45](../knowledge_desk/providers.py#L27-L45) `_SYSTEM` | Two jobs in one prompt: answer only from context, and treat context as data. Both are load-bearing. |
| Untrusted-content boundary | [providers.py:47-59](../knowledge_desk/providers.py#L47-L59) `_DOC_OPEN`, `_neutralize` | Delimiters the system prompt can refer to, plus the code that stops a document forging them. |
| Rendering retrieved passages | [providers.py:61-77](../knowledge_desk/providers.py#L61-L77) `_render_context` | Where untrusted text is wrapped before it ever reaches the model. |
| Streaming interface | [providers.py:95](../knowledge_desk/providers.py#L95), [providers.py:135](../knowledge_desk/providers.py#L135) `stream` | Mock and Claude implement the same event contract: zero or more `token`, exactly one `usage`, last. |
| Keyless mock fallback | [providers.py:83-113](../knowledge_desk/providers.py#L83-L113) `MockAnswerProvider` | Loud on purpose — a banner in every reply, so a mock answer can never be mistaken for a real one. |
| Token cost estimate | [providers.py:54-58](../knowledge_desk/providers.py#L54-L58) `_cost` | Per-million-token pricing table; the only place money is computed. |
| Orchestration | [assistant.py:50-154](../knowledge_desk/assistant.py#L50-L154) `answer_stream` | The whole request lifecycle in one readable function. Start here. |
| Refusal instead of guessing | [assistant.py:29-32](../knowledge_desk/assistant.py#L29-L32), [:90-96](../knowledge_desk/assistant.py#L90-L96) | Empty permitted retrieval → refuse. The access boundary reaching the generated text. |
| SSE frames over HTTP | [main.py:328-350](../knowledge_desk/main.py#L328-L350) `ask` | Event dicts become `data:` lines. Frontend parser: [api.ts](../frontend/src/api.ts). |

## Retrieval and access control

| Concept | Where | What to notice |
|---|---|---|
| Query embedding | [retrieval.py:15-17](../knowledge_desk/retrieval.py#L15-L17) `search` | 3 lines. Deliberately thin — the access control lives in the data layer. |
| **ACL inside the candidate fetch** | [tenancy.py:359-380](../knowledge_desk/tenancy.py#L359-L380) `TenantScope.search` | The single most important query in the repo. Filter and ranking in the same SQL. |
| The caller's access set | [tenancy.py:325-340](../knowledge_desk/tenancy.py#L325-L340) `principals` | Recomputed per query, never cached, so a group change takes effect immediately. |
| ACL denormalised onto chunks | [migrations/0009_chunk_acl.sql](../migrations/0009_chunk_acl.sql) | Why the filter reads `c.acl` and not `d.acl`: a cross-table predicate kills the index. |
| Chunking | [chunking.py](../knowledge_desk/chunking.py) `chunk_text` | Fixed character windows with overlap. See [04-rag-core.md](04-rag-core.md) for what this gives up. |
| Embeddings + mock | [embeddings.py:32-71](../knowledge_desk/embeddings.py#L32-L71) | Deterministic mock vectors so tests are stable without a key. |

## Tenant isolation, three layers

The property: an answer can never be built from a document the asker cannot
read. Each layer below enforces it independently.

| Layer | Where | What to notice |
|---|---|---|
| 1. `org_id` on every query | [tenancy.py:1-8](../knowledge_desk/tenancy.py#L1-L8), class `TenantScope` | One choke point, so leakage is a code-review target rather than spread across handlers. |
| 2. ACL in the ranking query | [tenancy.py:359-380](../knowledge_desk/tenancy.py#L359-L380) | Forbidden rows are never scored. |
| 3. Row-level security | [migrations/0007_rls.sql](../migrations/0007_rls.sql) | `force row level security` + deny-by-default policy, under everything else. |
| The GUC that RLS keys on | [db.py:1-16](../knowledge_desk/db.py#L1-L16) | Transaction-scoped on purpose. Session-scoped would ride a pooled connection into the next request. |
| Proof it holds | [test_governance.py:166-202](../tests/test_governance.py#L166-L202) | Asserts an unscoped query returns zero rows, and that pooled reuse does not inherit context. |
| Why the least-privilege role matters | [LESSONS.md](../LESSONS.md) §8, §22 | RLS is a property of the role you connect as. A managed database's default role may bypass it. |

## Cost, quotas, and abuse

| Concept | Where | What to notice |
|---|---|---|
| Limits checked before generation | [assistant.py:35-47](../knowledge_desk/assistant.py#L35-L47) `_limit_block` | A cap checked afterwards is an invoice, not a cap. |
| Per-org daily spend | [tenancy.py:435](../knowledge_desk/tenancy.py#L435) `spend_last_24h` | |
| Per-org monthly questions | [tenancy.py:454](../knowledge_desk/tenancy.py#L454) `questions_this_month` | |
| **Deployment-wide ceiling** | [tenancy.py:444](../knowledge_desk/tenancy.py#L444) `platform_spend_today` | Per-tenant caps bound one tenant; with open signup they do not bound the bill. |
| Billing an abandoned stream | [assistant.py:139-153](../knowledge_desk/assistant.py#L139-L153) | Tokens were generated before the client hung up. Book an estimate, flagged as estimated. |
| Blocked questions recorded | [tenancy.py:428](../knowledge_desk/tenancy.py#L428) `mark_blocked` | A refusal you cannot count is a refusal you cannot debug. |
| Storage quota inside the write txn | [tenancy.py:199-227](../knowledge_desk/tenancy.py#L199-L227) | Checked in the same transaction that writes, so concurrent uploads cannot race past it. |
| Token-bucket rate limiter | [ratelimit.py](../knowledge_desk/ratelimit.py) | In-memory, per key, with idle-bucket eviction. Per process — see the caveat in WALKTHROUGH. |
| Request body size limit | [bodylimit.py](../knowledge_desk/bodylimit.py) | Rejects oversized uploads before reading them into memory. |

## Ingestion and the job queue

| Concept | Where | What to notice |
|---|---|---|
| Sync, not append | [ingest.py:32-127](../knowledge_desk/ingest.py#L32-L127) `sync_documents` | The upload is the complete desired state. Absent paths get deleted. |
| Content-hash idempotency | [ingest.py:28-30](../knowledge_desk/ingest.py#L28-L30) `_hash`, [:66-76](../knowledge_desk/ingest.py#L66-L76) | Byte-identical content costs nothing: no chunking, no embedding, no API call. |
| Queue claim, `skip locked` | [jobs.py:48-63](../knowledge_desk/jobs.py#L48-L63) `claim_one` | Concurrent workers step over each other's locked rows. A queue in Postgres, no broker. |
| Retry with backoff | [jobs.py:73-101](../knowledge_desk/jobs.py#L73-L101) | Exponential backoff, then dead-letter. |
| Partial-failure containment | [ingest.py:203](../knowledge_desk/ingest.py#L203) `_mark_document_failed` | One bad document fails itself, not the batch. |
| Worker loop | [worker.py](../knowledge_desk/worker.py) | 52 lines. Also purges expired sessions. |

## Observability and audit

| Concept | Where | What to notice |
|---|---|---|
| One trace per question | [tracing.py:45-129](../knowledge_desk/tracing.py#L45-L129) `AskTracer` | Retrieval span + generation with usage and cost, tagged by org and user. |
| Tracing can never break the app | [tracing.py:46-47](../knowledge_desk/tracing.py#L46-L47) | Every method exception-proof, all no-ops without keys. |
| ACL made visible in the trace | [tenancy.py:341](../knowledge_desk/tenancy.py#L341) `retrieval_stats` | Records how many of the org's chunks the caller was allowed to see. |
| Audit log | [audit.py:21](../knowledge_desk/audit.py#L21) `log` | Every consequential action, PII-redacted before write. |
| PII redaction | [pii.py](../knowledge_desk/pii.py) | Applied to audit detail but deliberately *not* to stored questions — the reasoning is at [tenancy.py:384-396](../knowledge_desk/tenancy.py#L384-L396). |
| Error detail never reaches the caller | [assistant.py:123-138](../knowledge_desk/assistant.py#L123-L138) | A reference token goes to the user, the detail to the log. Exception text leaked DB host and role names before this. |

## Evals and CI

| Concept | Where | What to notice |
|---|---|---|
| The eval gate | [evals/run.py](../evals/run.py) | Three end-to-end properties, exits nonzero on failure. |
| Permission leak eval | [evals/run.py:88-108](../evals/run.py#L88-L108) | A secret only X may see must not reach Y, by search or ask. |
| Grounded answer eval | [evals/run.py:111-120](../evals/run.py#L111-L120) | A permitted, matching document is cited. |
| Prompt injection eval | [evals/run.py:131-155](../evals/run.py#L131-L155) | Structural assertions, so it is meaningful against the mock too. |
| Wired as a required step | [.github/workflows/ci.yml:46](../.github/workflows/ci.yml#L46) | The gate only matters because it blocks the merge. |

Explained in full in [03-evals.md](03-evals.md).

## Auth and roles

| Concept | Where | What to notice |
|---|---|---|
| Password hashing | [auth.py:24-40](../knowledge_desk/auth.py#L24-L40) | Pre-hash before the KDF, so long passwords are not silently truncated. |
| Timing-safe login miss | [auth.py:41-52](../knowledge_desk/auth.py#L41-L52) `dummy_hash` | Hash on the miss path too, or response time discloses which emails exist. |
| Role ranking | [auth.py:21](../knowledge_desk/auth.py#L21) `ROLE_RANK` | |
| Role gates live in the data layer | [tenancy.py:46-62](../knowledge_desk/tenancy.py#L46-L62) | Not in routes — a new route cannot forget a check that lives under it. |
| No granting above yourself | [tenancy.py:50-62](../knowledge_desk/tenancy.py#L50-L62) `require_can_grant` | Handing out a role you do not hold is privilege escalation with an extra step. |
| Domain errors → HTTP | [errors.py](../knowledge_desk/errors.py) | One mapping, so handlers do not invent status codes. |

## Where to go next

The [exercises](exercises/) break the entries marked most important above:
the ACL-in-fetch query, the delimiter neutralisation, and row-level security.
