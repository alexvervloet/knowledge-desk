"""Fold text to a comparison form, keeping a map back to the original.

A filter that matches on bytes loses to an attacker who picks the bytes. `Іgnore`
with a Cyrillic І and `Ign​ore` with a zero-width space are different byte
sequences and the same word to every reader, ours and the model's. Enumerating
lookalikes one at a time is a race you lose slowly (the Unicode confusables table
runs to thousands of entries), so this covers the two families that matter for
marker forgery: invisible characters, and the Latin lookalikes people actually
reach for.

The offset map is the point. Matching on folded text and replacing on folded text
would hand the model a document we rewrote, which is both lossy and useless for
an incident review that wants to know what the document said. `fold` returns the
folded string alongside, for each folded character, the index it came from in the
original, so a span found in the folded text can be cut out of the original.

Deliberately not NFKC. Full compatibility normalization rewrites ligatures, width
variants, and a long tail of other things, and it changes lengths in ways that
make an exact offset map fiddly. Everything here is either a deletion or a
one-for-one substitution, so the map is exact and the code stays short enough to
audit.
"""

from __future__ import annotations

import unicodedata

# Latin lookalikes from the alphabets a confusable attack actually uses. Cyrillic
# and Greek capitals first, because marker text is upper case, then the lower
# case forms and the handful of digit and punctuation confusables.
_LOOKALIKES = {
    # Cyrillic
    "А": "A",
    "В": "B",
    "Е": "E",
    "К": "K",
    "М": "M",
    "Н": "H",
    "О": "O",
    "Р": "P",
    "С": "C",
    "Т": "T",
    "У": "Y",
    "Х": "X",
    "Ѕ": "S",
    "І": "I",
    "Ј": "J",
    "а": "a",
    "в": "b",
    "е": "e",
    "к": "k",
    "м": "m",
    "н": "h",
    "о": "o",
    "р": "p",
    "с": "c",
    "т": "t",
    "у": "y",
    "х": "x",
    "ѕ": "s",
    "і": "i",
    "ј": "j",
    "ԁ": "d",
    "ɡ": "g",
    "ⅼ": "l",
    "ո": "n",
    "ս": "u",
    # Greek
    "Α": "A",
    "Β": "B",
    "Ε": "E",
    "Ζ": "Z",
    "Η": "H",
    "Ι": "I",
    "Κ": "K",
    "Μ": "M",
    "Ν": "N",
    "Ο": "O",
    "Ρ": "P",
    "Τ": "T",
    "Υ": "Y",
    "Χ": "X",
    "ο": "o",
    "ν": "v",
    "α": "a",
    "ρ": "p",
    "τ": "t",
    "υ": "u",
    "χ": "x",
    # Fullwidth Latin, which is a compatibility form NFKC would have caught.
    **{chr(0xFF21 + i): chr(ord("A") + i) for i in range(26)},
    **{chr(0xFF41 + i): chr(ord("a") + i) for i in range(26)},
    # Punctuation that shows up in marker forgery.
    "＜": "<",
    "＞": ">",
    "／": "/",
    "＿": "_",
    "－": "-",
    "‐": "-",
    "‑": "-",
    "–": "-",
    "—": "-",
    "﹘": "-",
    # Curly quotes, so a quoted evidence span parses whichever pair the model
    # reaches for.
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u2033": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
}

# Characters that render as nothing and exist to break a string comparison. The
# Cf category covers the zero-width joiners, the directional overrides, and the
# byte-order mark; the explicit few are the ones outside it.
_INVISIBLE = {"­", "​", "⁠", "﻿"}


def is_invisible(ch: str) -> bool:
    return ch in _INVISIBLE or unicodedata.category(ch) == "Cf"


def fold(text: str) -> tuple[str, list[int]]:
    """Return (folded_text, origin) where origin[i] is the index in `text` that
    folded_text[i] came from.

    Invisible characters are dropped, so the folded string is shorter and the map
    skips their indices. Every other character maps one for one, which is what
    keeps `origin` exact.
    """
    out: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(text):
        if is_invisible(ch):
            continue
        out.append(_LOOKALIKES.get(ch, ch))
        origin.append(i)
    return "".join(out), origin


def original_span(origin: list[int], start: int, end: int, length: int) -> tuple[int, int]:
    """Map a [start, end) span in folded text back to a span in the original.

    The end is the index after the last folded character, so it maps to one past
    that character's origin. An empty span, and a span running to the end of the
    string, both fall back to the original length.
    """
    if start >= len(origin):
        return length, length
    first = origin[start]
    last = origin[end - 1] if 0 < end <= len(origin) else origin[-1]
    return first, last + 1


def replace_folded(text: str, spans: list[tuple[int, int]], marker: str) -> str:
    """Replace the given folded-coordinate spans in `text` with `marker`.

    Applied right to left so earlier spans keep their indices. Callers pass spans
    already mapped through `original_span`.
    """
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + marker + text[end:]
    return text
