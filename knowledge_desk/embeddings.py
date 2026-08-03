"""Embedders. The mock is deterministic (same text always maps to the same
vector) so ingestion is reproducible and testable with no keys or network. The
Voyage embedder is used only when a Voyage key is present.

Both produce 1024-d vectors to match the `chunks.embedding` column. The mock
raises on the sentinel EMBED_FAIL_MARKER, standing in for an input the embedding
provider rejects (a permanent per-document failure), so the queue's retry and
dead-letter path is exercisable in tests. Binary content is handled earlier, at
the connector boundary: Postgres text columns cannot even store a NUL byte.
"""

from __future__ import annotations

import hashlib
import math
import random

from knowledge_desk.config import settings

EMBED_DIM = 1024

# A document containing this marker fails embedding on every attempt. Test hook
# that mimics a provider rejecting a specific input.
EMBED_FAIL_MARKER = "[[EMBED-FAIL]]"


def _unit(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class MockEmbedder:
    name = "mock"
    dim = EMBED_DIM

    def _one(self, text: str) -> list[float]:
        if EMBED_FAIL_MARKER in text:
            raise ValueError("embedding provider rejected this input")
        seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
        rng = random.Random(seed)
        return _unit([rng.uniform(-1.0, 1.0) for _ in range(self.dim)])

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._one(text)


class VoyageEmbedder:
    name = "voyage"
    dim = EMBED_DIM

    def __init__(self) -> None:
        import voyageai  # lazy: only needed when a key is set

        self._client = voyageai.Client(api_key=settings.voyage_api_key)
        self._model = settings.embed_model

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        result = self._client.embed(texts, model=self._model, input_type=input_type)
        # The SDK types embeddings as float or int lists; ours are always floats.
        return [[float(v) for v in row] for row in result.embeddings]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]


def get_embedder():
    """Voyage when a key is present, otherwise the loud deterministic mock."""
    if settings.voyage_api_key:
        return VoyageEmbedder()
    return MockEmbedder()
