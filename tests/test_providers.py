"""Pricing and per-model request shape.

The rates are not display values: `finalize_answer` stores what `_cost`
returns, and the per-org rolling budget and the platform daily cap are both
summed from that column.
"""

import logging

from knowledge_desk.providers import _PRICING, _SUPPORTS_EFFORT, _UNPRICED, _cost, _supports_effort

MILLION = 1_000_000


def test_each_priced_model_bills_its_own_rate():
    """Sonnet was priced 50% high here, which quietly shrank every Sonnet org's
    effective budget by a third."""
    assert _cost("claude-opus-5", MILLION, 0) == 5.0
    assert _cost("claude-sonnet-5", MILLION, 0) == 2.0
    assert _cost("claude-haiku-4-5", MILLION, 0) == 1.0
    assert _cost("claude-haiku-4-5", 0, MILLION) == 5.0


def test_an_unpriced_model_bills_at_the_dearest_rate_and_says_so(caplog):
    """It used to fall back to Opus silently, so an unlisted model produced
    numbers that looked right and were not, in whichever direction the real
    price happened to lie."""
    with caplog.at_level(logging.WARNING):
        cost = _cost("claude-not-a-model", MILLION, 0)

    assert cost == _UNPRICED[0]
    assert "no price for model" in caplog.text
    assert "claude-not-a-model" in caplog.text


def test_the_unpriced_rate_is_never_cheaper_than_a_priced_one():
    """Over-counting stops a customer early, which they can ask about.
    Under-counting spends money they never agreed to."""
    assert all(rate <= _UNPRICED for rate in _PRICING.values())


# --- request shape ---------------------------------------------------------


def test_effort_is_sent_only_to_models_that_accept_it():
    """Haiku 4.5 returns 400 "This model does not support the effort parameter",
    so sending it unconditionally meant answer_model could not actually be set
    to Haiku: every answer failed."""
    assert _supports_effort("claude-opus-5") is True
    assert _supports_effort("claude-sonnet-5") is True
    assert _supports_effort("claude-haiku-4-5") is False


def test_an_unrecorded_model_omits_effort_and_says_so(caplog):
    """Omitting it costs some tuning. Sending it where it is rejected costs
    every answer, so the unknown case takes the survivable failure."""
    with caplog.at_level(logging.WARNING):
        assert _supports_effort("claude-not-a-model") is False

    assert "no effort capability recorded" in caplog.text
    assert "claude-not-a-model" in caplog.text


def test_every_priced_model_has_an_effort_capability():
    """The two tables are edited together or they drift, and the drift is only
    visible when somebody switches models."""
    assert set(_PRICING) == set(_SUPPORTS_EFFORT)


# --- refusals --------------------------------------------------------------


def test_the_default_answer_model_is_one_that_does_not_refuse_this_prompt():
    """`claude-opus-5` returns stop_reason "refusal" with category
    "reasoning_extraction" on this app's system prompt, so every answer comes
    back empty. Sonnet 5, Haiku 4.5 and Opus 4.8 answer the identical prompt.

    This is a reminder rather than a proof: it cannot detect the day another
    model starts refusing. `tests/test_real_provider.py` is the one that can, and
    it only runs when a key is present.
    """
    from knowledge_desk.config import settings

    assert (
        settings.answer_model != "claude-opus-5"
    ), "claude-opus-5 refuses this system prompt outright; see LESSONS.md"


def test_a_refusal_raises_instead_of_streaming_nothing(monkeypatch):
    """A declined request is HTTP 200 with no content. Left alone it reaches the
    caller as an assistant with nothing to say, which is indistinguishable from
    an honest refusal to cite and invisible to every mock-provider test."""
    import pytest

    from knowledge_desk.providers import AnswerRefused, ClaudeAnswerProvider

    class _Refused:
        stop_reason = "refusal"
        stop_details = type("D", (), {"category": "reasoning_extraction"})()
        usage = type("U", (), {"input_tokens": 1905, "output_tokens": 0})()

    class _Stream:
        text_stream: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return _Refused()

    class _Messages:
        def stream(self, **kwargs):
            return _Stream()

    class _Client:
        messages = _Messages()

    provider = ClaudeAnswerProvider.__new__(ClaudeAnswerProvider)
    provider._model = "claude-sonnet-5"
    provider._client = _Client()

    with pytest.raises(AnswerRefused, match="reasoning_extraction"):
        list(provider.stream("anything", []))
