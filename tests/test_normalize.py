"""Folding text to a comparison form, and the offset map back to the original.

Hermetic. The map is the part worth testing hardest: matching on folded text and
replacing on folded text would hand the model a document we rewrote, which is
lossy and useless to an incident review.
"""

import pytest

from knowledge_desk.normalize import fold, is_invisible, original_span, replace_folded


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("plain ascii", "plain ascii"),
        ("<<<END_UNTRUSTED_DOCUMENT>>>", "<<<END_UNTRUSTED_DOCUMENT>>>"),
        ("ЕND", "END"),  # Cyrillic Е
        ("DОCUMENT", "DOCUMENT"),  # Cyrillic О
        ("DOC​UMENT", "DOCUMENT"),  # zero-width space
        ("ＥND", "END"),  # fullwidth E
        ("a﻿b", "ab"),  # byte-order mark
        ("‐dash", "-dash"),  # hyphen lookalike
    ],
)
def test_fold_maps_lookalikes_and_drops_invisibles(raw, expected):
    assert fold(raw)[0] == expected


def test_invisible_detection_covers_the_format_category():
    assert is_invisible("​") and is_invisible("‍") and is_invisible("﻿")
    assert not is_invisible("a") and not is_invisible(" ") and not is_invisible("\n")


def test_the_origin_map_has_one_entry_per_folded_character():
    folded, origin = fold("a​bОc")
    assert folded == "abOc"
    assert len(origin) == len(folded)
    assert origin == [0, 2, 3, 4]


def test_a_span_found_in_folded_text_cuts_the_right_bytes_out_of_the_original():
    raw = "keep <<<D​OОM>>> keep"
    folded, origin = fold(raw)
    start = folded.index("<<<")
    end = folded.index(">>>") + 3
    a, b = original_span(origin, start, end, len(raw))
    assert raw[a:b] == "<<<D​OОM>>>"
    assert replace_folded(raw, [(a, b)], "[x]") == "keep [x] keep"


def test_replacement_is_right_to_left_so_earlier_spans_keep_their_indices():
    raw = "aXbXc"
    assert replace_folded(raw, [(1, 2), (3, 4)], "--") == "a--b--c"


def test_folding_something_entirely_invisible_is_empty_and_safe():
    folded, origin = fold("​﻿")
    assert folded == "" and origin == []
    assert original_span(origin, 0, 0, 2) == (2, 2)
