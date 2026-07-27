# Knowledge Desk

A multi-tenant, permissions-aware knowledge assistant. Each organization
connects its documents, the system indexes them per tenant, and users ask
questions and get grounded, cited answers drawn only from the documents they
are allowed to see.

This is a portfolio project about the operational layer around an LLM
application, not the retrieval technique. The interesting parts are tenancy,
access-controlled retrieval, background ingestion, quotas and cost attribution,
audit, and evals that gate merges. The RAG core is reused from earlier work.

## Status

Early scaffolding. See [PLAN.md](PLAN.md) for the phased build and the
definition of done for each phase.

## Stack

FastAPI, Postgres with pgvector, a Postgres-backed job queue and worker, React
plus Vite plus TypeScript, Voyage embeddings, Claude for answers, Langfuse for
observability, Docker, and GitHub Actions. Runs keyless with a loud mock
fallback, so it works and tests green with no API keys.

## Run it locally

```bash
docker compose up -d                      # Postgres + pgvector on :5436
python -m knowledge_desk.migrate          # creates schema, RLS, and the app role
python check_setup.py                     # preflight

uvicorn knowledge_desk.main:app --reload  # API on :8000
python -m knowledge_desk.worker           # background embedder (separate shell)

cd frontend && npm install && npm run dev # UI on :5173 (set VITE_API_BASE=http://localhost:8000)
```

Tenant isolation is enforced in three layers: the data layer's `org_id` filter,
the ACL-aware candidate fetch in retrieval, and Postgres row-level security
underneath (the app connects as a least-privilege role so RLS applies).

## License

MIT. See [LICENSE](LICENSE).
