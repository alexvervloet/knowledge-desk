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
plus Vite, Voyage embeddings, Claude for answers, Langfuse for observability,
Docker, and GitHub Actions. Runs keyless with a loud mock fallback, so it works
and tests green with no API keys.

## License

MIT. See [LICENSE](LICENSE).
