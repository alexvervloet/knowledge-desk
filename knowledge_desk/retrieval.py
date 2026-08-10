"""Retrieval: embed a query and fetch the nearest chunks the caller is allowed
to see. The access control lives in TenantScope.search; this module only turns
text into a query vector and hands it over. The assistant answers on top of
these results.
"""

from __future__ import annotations

from typing import Any

from knowledge_desk.embeddings import get_embedder
from knowledge_desk.tenancy import TenantScope


def search(scope: TenantScope, query: str, k: int = 5) -> list[dict[str, Any]]:
    embedding = get_embedder().embed_query(query)
    return scope.search(embedding, k)
