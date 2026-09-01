"""Pricing. These rates are not display values: `finalize_answer` stores what
`_cost` returns, and the per-org rolling budget and the platform daily cap are
both summed from that column.
"""

import logging

from knowledge_desk.providers import _PRICING, _UNPRICED, _cost

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
