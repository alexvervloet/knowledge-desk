"""Embedders. The mock is deterministic (same text always maps to the same
vector) so ingestion is reproducible and testable with no keys or network. The
Voyage embedder is used only when a Voyage key is present.

Both produce 1024-d vectors to match the `chunks.embedding` column. The mock
raises on a NUL byte, standing in for a binary or garbage file that should fail
its ingest job rather than be embedded as if it were text.
"""

from __future__ import annotations

import hashlib
import math
import random

from knowledge_desk.config import settings

EMBED_DIM = 1024


def _unit(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class MockEmbedder:
    name = "mock"
    dim = EMBED_DIM

    def _one(self, text: str) -> list[float]:
        if "\x00" in text:
            raise ValueError("cannot embed binary content (NUL byte present)")
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
        return result.embeddings

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]


def get_embedder():
    """Voyage when a key is present, otherwise the loud deterministic mock."""
    if settings.voyage_api_key:
        return VoyageEmbedder()
    return MockEmbedder()
