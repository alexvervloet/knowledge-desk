"""The assistant: retrieve within the caller's permissions, then stream a
grounded answer. If retrieval returns nothing the caller may see, it refuses
rather than answering from the model's own knowledge, so the Phase 3 access
boundary carries all the way through to the generated answer.

`answer_stream` yields event dicts the API renders as SSE frames:
  {"type": "meta", "answer_id": ..., "provider": ...}
  {"type": "sources", "sources": [{document_id, ordinal, path}]}
  {"type": "token", "text": ...}          zero or more
  {"type": "done", "usage": {...}, "cost_usd": float}
  {"type": "error", "message": ...}       on failure, instead of done
"""

from __future__ import annotations

from typing import Any, Iterator

from knowledge_desk import retrieval
from knowledge_desk.providers import get_answer_provider
from knowledge_desk.tenancy import TenantScope

REFUSAL = (
    "I don't have anything I'm allowed to cite for that. Nothing in the"
    " documents you can access matches this question."
)


def answer_stream(
    scope: TenantScope, question: str, k: int
) -> Iterator[dict[str, Any]]:
    contexts = retrieval.search(scope, question, k)
    provider = get_answer_provider()
    refused = not contexts
    answer_id = scope.record_answer(question, provider.name, refused)

    yield {"type": "meta", "answer_id": answer_id, "provider": provider.name}

    if refused:
        for word in REFUSAL.split():
            yield {"type": "token", "text": word + " "}
        yield {"type": "done", "usage": {"input_tokens": 0, "output_tokens": 0},
               "cost_usd": 0.0}
        return

    yield {
        "type": "sources",
        "sources": [
            {"document_id": str(c["document_id"]), "ordinal": c["ordinal"],
             "path": c["path"]}
            for c in contexts
        ],
    }

    try:
        for event in provider.stream(question, contexts):
            if event["type"] == "usage":
                yield {
                    "type": "done",
                    "usage": {"input_tokens": event["input_tokens"],
                              "output_tokens": event["output_tokens"]},
                    "cost_usd": event["cost_usd"],
                }
            else:
                yield event
    except Exception as exc:  # noqa: BLE001 - a provider failure must not 500 mid-stream
        yield {"type": "error", "message": f"answer generation failed: {exc}"}
