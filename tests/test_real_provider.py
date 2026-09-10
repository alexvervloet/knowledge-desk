"""The one test that calls a real model.

Every other eval and test here is deliberately model-independent, which the
README describes as what makes them trustworthy and also what they cannot tell
you. This is the gap that argument leaves: a system prompt the API declines
outright passes all of them, because none of them ever asks a model anything.

That happened. `claude-opus-5` returned `stop_reason: refusal` with category
`reasoning_extraction` on this app's system prompt, every answer came back
empty, and the whole suite stayed green. See LESSONS.md.

Skipped without a key, so the default suite is unchanged. Run it before changing
the system prompt or the answer model, and in any CI job that has a key:

    pytest tests/test_real_provider.py

One call, a few hundred tokens, well under a cent.
"""

from __future__ import annotations

import anthropic
import pytest

from knowledge_desk.config import settings
from knowledge_desk.providers import (
    _SYSTEM,
    _build_user_turn,
    _supports_effort,
    new_fence_nonce,
)

pytestmark = pytest.mark.skipif(
    not settings.anthropic_api_key, reason="needs a real ANTHROPIC_API_KEY"
)

PASSAGE = "Acme refunds are processed within five business days of the request."


@pytest.mark.real_provider
def test_the_configured_model_does_not_refuse_this_system_prompt() -> None:
    contexts = [
        {"document_id": "d1", "ordinal": 0, "path": "handbook.md", "text": PASSAGE, "distance": 0.4}
    ]
    turn = _build_user_turn("how long do refunds take", contexts, new_fence_nonce())
    extra = {"output_config": {"effort": "low"}} if _supports_effort(settings.answer_model) else {}

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    with client.messages.stream(
        model=settings.answer_model,
        max_tokens=400,
        system=_SYSTEM,
        messages=[{"role": "user", "content": turn}],
        **extra,
    ) as stream:
        text = "".join(stream.text_stream)
        final = stream.get_final_message()

    category = getattr(final.stop_details, "category", None)
    assert final.stop_reason != "refusal", (
        f"{settings.answer_model} refused this system prompt (category: {category}). "
        "Every answer this app produces will be empty. See LESSONS.md."
    )
    # Not just "was not refused": a model that answers but ignores the citation
    # format has also broken the output checks, and that is worth one assertion
    # while a real call is already being paid for.
    assert "five business days" in text.lower()
    assert "[1]" in text
