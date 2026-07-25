"""Character-window chunking with overlap. Deliberately simple: the point of
this project is the operational layer, not retrieval quality, so a fixed window
is enough and stays predictable in tests. Token-aware chunking is a later swap.
"""

from __future__ import annotations

from knowledge_desk.config import settings


def chunk_text(
    text: str, size: int | None = None, overlap: int | None = None
) -> list[str]:
    size = size or settings.chunk_size
    overlap = settings.chunk_overlap if overlap is None else overlap
    if size <= 0:
        raise ValueError("chunk size must be positive")
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")

    text = text.strip()
    if not text:
        return []

    step = size - overlap
    chunks: list[str] = []
    for start in range(0, len(text), step):
        piece = text[start : start + size].strip()
        if piece:
            chunks.append(piece)
        if start + size >= len(text):
            break
    return chunks
