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

from collections.abc import Iterator
from typing import Any

from knowledge_desk import audit, retrieval
from knowledge_desk.config import settings
from knowledge_desk.providers import get_answer_provider
from knowledge_desk.tenancy import TenantScope
from knowledge_desk.tracing import AskTracer

REFUSAL = (
    "I don't have anything I'm allowed to cite for that. Nothing in the"
    " documents you can access matches this question."
)


def _limit_block(scope: TenantScope) -> str | None:
    """Return a reason string if this org is over an operational limit, else
    None. Checked before any model call so spend and volume are hard-capped."""
    if scope.spend_last_24h() >= settings.daily_budget_usd:
        return "daily budget exhausted"
    if scope.questions_this_month() >= settings.monthly_question_cap:
        return "monthly question limit reached"
    return None


def answer_stream(
    scope: TenantScope, question: str, k: int
) -> Iterator[dict[str, Any]]:
    provider = get_answer_provider()
    model = settings.answer_model if provider.name == "claude" else provider.name
    tracer = AskTracer(question, scope.org_id, scope.ctx.user_id, scope.ctx.email,
                       provider.name, model)
    error_message: str | None = None
    # Everything the finally block needs to bill a stream that does not finish.
    answer_id: str | None = None
    contexts: list[dict[str, Any]] = []
    streamed: list[str] = []
    billed = False
    try:
        # Hard limits first: a blocked question is recorded but never reaches the model.
        blocked_reason = _limit_block(scope)
        if blocked_reason is not None:
            answer_id = scope.record_answer(question, provider.name, refused=False)
            scope.mark_blocked(answer_id)
            audit.log(scope.org_id, scope.ctx.user_id, "question.blocked",
                      {"answer_id": answer_id, "reason": blocked_reason})
            yield {"type": "meta", "answer_id": answer_id, "provider": provider.name}
            error_message = (f"[LIMIT] request blocked: {blocked_reason}."
                             " No answer was generated.")
            yield {"type": "error", "message": error_message}
            return

        contexts = retrieval.search(scope, question, k)
        refused = not contexts
        answer_id = scope.record_answer(question, provider.name, refused)
        audit.log(scope.org_id, scope.ctx.user_id, "question.asked",
                  {"answer_id": answer_id, "refused": refused})

        yield {"type": "meta", "answer_id": answer_id, "provider": provider.name}

        if refused:
            for word in REFUSAL.split():
                tracer.token(word + " ")
                yield {"type": "token", "text": word + " "}
            yield {"type": "done", "usage": {"input_tokens": 0, "output_tokens": 0},
                   "cost_usd": 0.0}
            return

        sources = [
            {"document_id": str(c["document_id"]), "ordinal": c["ordinal"],
             "path": c["path"]}
            for c in contexts
        ]
        tracer.sources(sources, scope.retrieval_stats() if tracer.active else None)
        yield {"type": "sources", "sources": sources}

        for event in provider.stream(question, contexts):
            if event["type"] == "usage":
                scope.finalize_answer(answer_id, event["input_tokens"],
                                      event["output_tokens"], event["cost_usd"])
                billed = True
                tracer.done(event["input_tokens"], event["output_tokens"],
                            event["cost_usd"])
                yield {
                    "type": "done",
                    "usage": {"input_tokens": event["input_tokens"],
                              "output_tokens": event["output_tokens"]},
                    "cost_usd": event["cost_usd"],
                }
            else:
                streamed.append(event["text"])
                tracer.token(event["text"])
                yield event
    except Exception as exc:  # noqa: BLE001 - a provider failure must not 500 mid-stream
        error_message = f"answer generation failed: {exc}"
        yield {"type": "error", "message": error_message}
    finally:
        # A stream that never reaches its usage frame — the client disconnected,
        # or the provider failed part way — still cost real tokens, because the
        # model generated them before we stopped reading. Left unbilled, aborting
        # each request just before the end spends without ever touching the
        # budget. Book an estimate instead, flagged as such.
        #
        # Only when something was actually streamed: that is the evidence the
        # model ran at all, and it keeps a failure that happened before the first
        # token from inventing a charge.
        if answer_id is not None and not billed and streamed:
            usage = provider.estimate(question, contexts, "".join(streamed))
            scope.finalize_answer(answer_id, usage["input_tokens"],
                                  usage["output_tokens"], usage["cost_usd"],
                                  estimated=True)
        tracer.finish(error=error_message)
