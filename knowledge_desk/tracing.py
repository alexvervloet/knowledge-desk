"""Langfuse tracing, enabled only when LANGFUSE_* keys are configured.

One trace per question, tagged by org and user: a root span, a retriever child
that records how many chunks the org has versus how many this user is allowed to
see (so the ACL filter is visible in the trace), and the answer typed as a
generation carrying token and cost estimates.

Keyless (dev, CI, mock mode) every call here is a no-op. Observability must never
take the product down, so every method is exception-proof: a Langfuse failure
degrades to a log line, never an error in the request. Uses the explicit
start_observation object API rather than the context-manager one, because the
answer is streamed and context-var "current spans" can leak between interleaved
requests.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("knowledge_desk")

_client = None


def init() -> None:
    global _client
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        log.info("langfuse: no keys configured, tracing disabled")
        return
    try:
        from langfuse import Langfuse

        client = Langfuse()  # reads LANGFUSE_PUBLIC_KEY / SECRET_KEY / HOST
        if not client.auth_check():
            log.error("langfuse: auth_check failed, tracing disabled")
            return
        _client = client
        log.info("langfuse: tracing enabled")
    except Exception:
        log.exception("langfuse: init failed, tracing disabled")


class AskTracer:
    """Collects one question's trace. Every method is exception-proof, and all
    are no-ops when Langfuse is not configured."""

    def __init__(self, question: str, org_id: str, user_id: str, email: str,
                 provider_name: str, model: str) -> None:
        self._root: Any = None
        self._retrieval: Any = None
        self._gen: Any = None
        self._answer: list[str] = []
        self._question = question
        self._model = model
        if _client is None:
            return
        try:
            from langfuse import propagate_attributes

            # Trace-level name and user land via attribute propagation; the span
            # creation below is synchronous, so the context manager cannot leak
            # across interleaved requests.
            with propagate_attributes(trace_name="ask", user_id=user_id):
                self._root = _client.start_observation(
                    name="ask", as_type="span", input=question,
                    metadata={"org_id": org_id, "email": email, "provider": provider_name},
                )
                self._retrieval = self._root.start_observation(
                    name="retrieval", as_type="retriever", input=question
                )
        except Exception:
            log.exception("langfuse: trace start failed")
            self._root = None

    @property
    def active(self) -> bool:
        return self._root is not None

    def sources(self, sources: list[dict[str, Any]], stats: dict[str, int] | None) -> None:
        """End the retriever span with the chosen sources and the ACL-filter
        counts, then open the generation span."""
        if self._root is None:
            return
        try:
            if self._retrieval is not None:
                self._retrieval.update(output={"sources": sources, "acl": stats or {}})
                self._retrieval.end()
                self._retrieval = None
            self._gen = self._root.start_observation(
                name="answer", as_type="generation", model=self._model,
                input=self._question,
            )
        except Exception:
            log.exception("langfuse: sources recording failed")

    def token(self, text: str) -> None:
        self._answer.append(text)

    def done(self, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        if self._root is None or self._gen is None:
            return
        try:
            self._gen.update(
                output="".join(self._answer),
                usage_details={"input": input_tokens, "output": output_tokens},
                cost_details={"total": cost_usd},
            )
        except Exception:
            log.exception("langfuse: done recording failed")

    def finish(self, error: str | None = None) -> None:
        if self._root is None:
            return
        try:
            if self._retrieval is not None:
                self._retrieval.end()
            if self._gen is not None:
                self._gen.end()
            output = error if error is not None else "".join(self._answer)
            self._root.set_trace_io(input=self._question, output=output)
            if error is not None:
                self._root.update(level="ERROR", status_message=error, output=error)
            else:
                self._root.update(output=output)
            self._root.end()
        except Exception:
            log.exception("langfuse: trace finish failed")
