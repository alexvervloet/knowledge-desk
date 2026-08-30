# Concept index

Concept → file → line. Use this as a lookup table; you are not meant to read it
start to finish. Line numbers were correct at the time of writing — if one has
drifted, the symbol name next to it will still find the code.

## The model call

| Concept | Where | What to notice |
|---|---|---|
| System prompt | [providers.py:30-53](../../knowledge_desk/providers.py#L30-L53 "_SYSTEM") `_SYSTEM` | Two jobs in one prompt: answer only from context, and treat context as data. Both are load-bearing. |
| Untrusted-content boundary | [providers.py:61-74](../../knowledge_desk/providers.py#L61-L74) `fence_tags` | Markers carrying a per-request nonce. A fixed delimiter is one the attacker can simply type. |
| Defusing the prompt's grammar | [providers.py:77-153](../../knowledge_desk/providers.py#L77-L153) `_GRAMMAR` | Markers, `[n]` citation keys, and the `path:` line. A passage that writes your grammar gets to lie about what kind of text it is. |
| Folding before matching | [normalize.py](../../knowledge_desk/normalize.py) `fold` | A filter that compares bytes loses to an attacker who picks them. Keeps an offset map so replacement lands on the original. |
| Counting what you defuse | [assistant.py](../../knowledge_desk/assistant.py) `count_defused` | A corpus where forgeries are nonzero and rising is one somebody is writing into. |
| Output checks | [outputchecks.py](../../knowledge_desk/outputchecks.py) `check_answer` | The layer that does not guess. Detectors, not a gate: the answer has already streamed. |
| Rendering retrieved passages | [providers.py:164-182](../../knowledge_desk/providers.py#L164-L182) `_render_context` | Everything the uploader supplied goes inside the fence; only the minted `[n]` label stays out. |
| The region a fence cannot cover | [providers.py:194-253](../../knowledge_desk/providers.py#L194-L253) `unfenced_untrusted` | Asks which untrusted values landed outside the markers. Gates the property, not the two fields with evals. |
| Streaming interface | [providers.py:301](../../knowledge_desk/providers.py#L301), [providers.py:344](../../knowledge_desk/providers.py#L344) `stream` | Mock and Claude implement the same event contract: zero or more `token`, exactly one `usage`, last. |
| Keyless mock fallback | [providers.py:287-319](../../knowledge_desk/providers.py#L287-L319) `MockAnswerProvider` | Loud on purpose — a banner in every reply, so a mock answer can never be mistaken for a real one. |
| Token cost estimate | [providers.py:157-158](../../knowledge_desk/providers.py#L157-L158) `_cost` | Per-million-token pricing table; the only place money is computed. |
| Orchestration | [assistant.py:50-201](../../knowledge_desk/assistant.py#L50-L201 "answer_stream") `answer_stream` | The whole request lifecycle in one readable function. Start here. |
| Refusal instead of guessing | [assistant.py:29-32](../../knowledge_desk/assistant.py#L29-L32 "REFUSAL"), [:90-96](../../knowledge_desk/assistant.py#L89-L103) | Empty permitted retrieval → refuse. The access boundary reaching the generated text. |
| SSE frames over HTTP | [main.py:328-353](../../knowledge_desk/main.py#L328-L353 "ask") `ask` | Event dicts become `data:` lines. Frontend parser: [api.ts](../../frontend/src/api.ts). |

## Retrieval and access control

| Concept | Where | What to notice |
|---|---|---|
| Query embedding | [retrieval.py:15-17](../../knowledge_desk/retrieval.py#L15-L17 "search") `search` | 3 lines. Deliberately thin — the access control lives in the data layer. |
| **ACL inside the candidate fetch** | [tenancy.py:397-418](../../knowledge_desk/tenancy.py#L397-L418 "TenantScope.search") `TenantScope.search` | The single most important query in the repo. Filter and ranking in the same SQL. |
| The caller's access set | [tenancy.py:359-373](../../knowledge_desk/tenancy.py#L359-L373 "TenantScope.principals") `principals` | Recomputed per query, never cached, so a group change takes effect immediately. |
| ACL denormalised onto chunks | [migrations/0009_chunk_acl.sql](../../migrations/0009_chunk_acl.sql) | Why the filter reads `c.acl` and not `d.acl`: a cross-table predicate kills the index. |
| Chunking | [chunking.py](../../knowledge_desk/chunking.py) `chunk_text` | Fixed character windows with overlap. See [04-rag-core.md](04-rag-core.md) for what this gives up. |
| Embeddings + mock | [embeddings.py:32-71](../../knowledge_desk/embeddings.py#L32-L71) | Deterministic mock vectors so tests are stable without a key. |

## Tenant isolation, three layers

The property: an answer can never be built from a document the asker cannot
read. Each layer below enforces it independently.

| Layer | Where | What to notice |
|---|---|---|
| 1. `org_id` on every query | [tenancy.py:1-8](../../knowledge_desk/tenancy.py#L1-L8), class `TenantScope` | One choke point, so leakage is a code-review target rather than spread across handlers. |
| 2. ACL in the ranking query | [tenancy.py:397-418](../../knowledge_desk/tenancy.py#L397-L418 "TenantScope.search") | Forbidden rows are never scored. |
| 3. Row-level security | [migrations/0007_rls.sql](../../migrations/0007_rls.sql) | `force row level security` + deny-by-default policy, under everything else. |
| The GUC that RLS keys on | [db.py:1-15](../../knowledge_desk/db.py#L1-L15) | Transaction-scoped on purpose. Session-scoped would ride a pooled connection into the next request. |
| Proof it holds | [test_governance.py:166-202](../../tests/test_governance.py#L166-L202) | Asserts an unscoped query returns zero rows, and that pooled reuse does not inherit context. |
| Why the least-privilege role matters | [LESSONS.md](../../LESSONS.md) §8, §22 | RLS is a property of the role you connect as. A managed database's default role may bypass it. |

## Cost, quotas, and abuse

| Concept | Where | What to notice |
|---|---|---|
| Limits checked before generation | [assistant.py:35-47](../../knowledge_desk/assistant.py#L35-L47 "_limit_block") `_limit_block` | A cap checked afterwards is an invoice, not a cap. |
| Per-org daily spend | [tenancy.py:459](../../knowledge_desk/tenancy.py#L459) `spend_last_24h` | |
| Per-org monthly questions | [tenancy.py:478](../../knowledge_desk/tenancy.py#L478) `questions_this_month` | |
| **Deployment-wide ceiling** | [tenancy.py:468](../../knowledge_desk/tenancy.py#L468) `platform_spend_today` | Per-tenant caps bound one tenant; with open signup they do not bound the bill. |
| Billing an abandoned stream | [assistant.py:157-171](../../knowledge_desk/assistant.py#L157-L171) | Tokens were generated before the client hung up. Book an estimate, flagged as estimated. |
| Blocked questions recorded | [tenancy.py:452](../../knowledge_desk/tenancy.py#L452) `mark_blocked` | A refusal you cannot count is a refusal you cannot debug. |
| Storage quota inside the write txn | [tenancy.py:225-251](../../knowledge_desk/tenancy.py#L225-L251) | Checked in the same transaction that writes, so concurrent uploads cannot race past it. |
| Token-bucket rate limiter | [ratelimit.py](../../knowledge_desk/ratelimit.py) | In-memory, per key, with idle-bucket eviction. Per process — see the caveat in WALKTHROUGH. |
| Request body size limit | [bodylimit.py](../../knowledge_desk/bodylimit.py) | Rejects oversized uploads before reading them into memory. |

## Ingestion and the job queue

| Concept | Where | What to notice |
|---|---|---|
| Sync, not append | [ingest.py:34-142](../../knowledge_desk/ingest.py#L34-L142 "sync_documents") `sync_documents` | The upload is the complete desired state. Absent paths get deleted. |
| Content-hash idempotency | [ingest.py:30-31](../../knowledge_desk/ingest.py#L30-L31 "_hash") `_hash`, [:66-76](../../knowledge_desk/ingest.py#L66-L76) | Byte-identical content costs nothing: no chunking, no embedding, no API call. |
| Queue claim, `skip locked` | [jobs.py:53-66](../../knowledge_desk/jobs.py#L53-L66 "claim_one") `claim_one` | Concurrent workers step over each other's locked rows. A queue in Postgres, no broker. |
| Retry with backoff | [jobs.py:73-101](../../knowledge_desk/jobs.py#L73-L101) | Exponential backoff, then dead-letter. |
| Partial-failure containment | [ingest.py:203](../../knowledge_desk/ingest.py#L203) `_mark_document_failed` | One bad document fails itself, not the batch. |
| Worker loop | [worker.py](../../knowledge_desk/worker.py) | 52 lines. Also purges expired sessions. |

## Observability and audit

| Concept | Where | What to notice |
|---|---|---|
| One trace per question | [tracing.py:73-169](../../knowledge_desk/tracing.py#L73-L169 "AskTracer") `AskTracer` | Retrieval span + generation with usage and cost, tagged by org and user. |
| Tracing can never break the app | [tracing.py:74-75](../../knowledge_desk/tracing.py#L74-L75) | Every method exception-proof, all no-ops without keys. |
| ACL made visible in the trace | [tenancy.py:365](../../knowledge_desk/tenancy.py#L365) `retrieval_stats` | Records how many of the org's chunks the caller was allowed to see. |
| Audit log | [audit.py:21](../../knowledge_desk/audit.py#L21) `log` | Every consequential action, PII-redacted before write. |
| PII redaction | [pii.py](../../knowledge_desk/pii.py) | Applied to audit detail but deliberately *not* to stored questions — the reasoning is at [tenancy.py:408-420](../../knowledge_desk/tenancy.py#L408-L420). |
| Error detail never reaches the caller | [assistant.py:141-156](../../knowledge_desk/assistant.py#L141-L156) | A reference token goes to the user, the detail to the log. Exception text leaked DB host and role names before this. |

## Evals and CI

| Concept | Where | What to notice |
|---|---|---|
| The eval gate | [evals/run.py](../../evals/run.py) | Three end-to-end properties, exits nonzero on failure. |
| Permission leak eval | [evals/run.py:88-108](../../evals/run.py#L88-L108) | A secret only X may see must not reach Y, by search or ask. |
| Grounded answer eval | [evals/run.py:111-120](../../evals/run.py#L111-L120) | A permitted, matching document is cited. |
| Prompt injection eval | [evals/run.py:131-155](../../evals/run.py#L131-L155) | Structural assertions, so it is meaningful against the mock too. |
| Wired as a required step | [.github/workflows/ci.yml:46](../../.github/workflows/ci.yml#L46) | The gate only matters because it blocks the merge. |

Explained in full in [03-evals.md](03-evals.md).

## Auth and roles

| Concept | Where | What to notice |
|---|---|---|
| Password hashing | [auth.py:24-40](../../knowledge_desk/auth.py#L24-L40) | Pre-hash before the KDF, so long passwords are not silently truncated. |
| Timing-safe login miss | [auth.py:40-51](../../knowledge_desk/auth.py#L40-L51 "dummy_hash") `dummy_hash` | Hash on the miss path too, or response time discloses which emails exist. |
| Role ranking | [auth.py:21](../../knowledge_desk/auth.py#L21 "ROLE_RANK") `ROLE_RANK` | |
| Role gates live in the data layer | [tenancy.py:46-62](../../knowledge_desk/tenancy.py#L46-L62) | Not in routes — a new route cannot forget a check that lives under it. |
| No granting above yourself | [tenancy.py:50-62](../../knowledge_desk/tenancy.py#L50-L62 "TenantScope.require_can_grant") `require_can_grant` | Handing out a role you do not hold is privilege escalation with an extra step. |
| No unconsented membership | [accounts.py](../../knowledge_desk/accounts.py) `add_member` | An email that already has an account is refused. Nothing in the request is evidence the holder agreed to join. |
| Security response headers | [securityheaders.py](../../knowledge_desk/securityheaders.py) | The SPA is served same-origin, so the CSP lands on the page holding the session token. No inline-script escape. |
| Domain errors → HTTP | [errors.py](../../knowledge_desk/errors.py) | One mapping, so handlers do not invent status codes. |

## Where to go next

The [exercises](exercises/) break the entries marked most important above:
the ACL-in-fetch query, the delimiter neutralisation, and row-level security.
