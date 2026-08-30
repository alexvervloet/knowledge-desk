"""Deterministic checks on a finished answer.

The layers before this one guess. A fence guesses that the model will respect a
boundary it can locate; defusing guesses which shapes it might honour. These
functions do not guess: they look at concrete output and answer yes or no. That
is what makes an output check the most reliable layer in an injection defense,
and why it belongs behind the others rather than instead of them.

**These are detectors, not a gate, and the reason is the streaming.** Tokens go
to the browser as they arrive, so by the time an answer is complete the caller
has already read it. Gating would mean buffering the whole answer and giving up
streaming, which is a real trade and not obviously the right one for a product
whose main interaction is watching an answer appear. So the findings ride along
in the `done` frame for the UI to show, and land in the audit log where a pattern
across many answers is visible. Calling them a gate would be the more flattering
description and the false one.

What these checks are not: proof that an answer is true. Verifying that a quote
exists in the passage it cites catches detached, invented, and stale citations,
and it says nothing about whether the quote supports the sentence built around
it. Entailment needs task-specific factuality work. A UI showing a green check
here would be claiming something this module cannot deliver.

What is deliberately not detected: a plain URL sitting in a sentence. Stripping
markdown images and foreign-domain links works because those have a structural
handle to grab. A human-readable phishing link written as ordinary prose has
none, and no filter here will catch it. Catching it needs domain reputation or a
policy of not surfacing model-authored links at all, each a project with its own
false positives. Left visibly unhandled rather than tuned away.
"""

from __future__ import annotations

import re
from typing import Any

from knowledge_desk import normalize
from knowledge_desk.providers import SYSTEM_CANARY, marker_shaped

# `[12]` in an answer is a claim that passage 12 exists. Matching the same shape
# the prompt uses to label them.
_CITATION = re.compile(r"\[\s*(\d{1,3})\s*\]")

# A citation followed by its evidence span. The system prompt asks for a short
# verbatim quote after each `[n]`, and this is the pair that gets verified. Run
# against folded text, where curly quotes have already become straight ones.
#
# Newlines are allowed inside the span. A streamed answer wraps, and a quote
# broken across two lines is the model reproducing the passage correctly; a
# pattern that stopped at the newline would read it as an unquoted citation and
# report the compliant answer. Both parts are non-greedy and length-bounded, so
# the match still stops at the nearest closing quote rather than running on.
_CITED_QUOTE = re.compile(r"\[\s*(\d{1,3})\s*\][^\"]{0,40}?\"([^\"]{1,300}?)\"")


def _comparable(text: str) -> str:
    """Whitespace-collapsed, case-folded form for comparing a quote to a passage.

    Lenient on purpose. A quote differing from its source only by line wrapping
    or capitalisation is the model reproducing the passage correctly, and
    flagging it would be a false positive. False positives are what turn a
    warning list into something nobody reads, and they buy nothing here: no
    attack turns on the case of a word.
    """
    return " ".join(normalize.fold(text)[0].split()).casefold()


# A markdown image is the exfiltration channel worth naming: a client that
# renders it fetches the URL without the reader doing anything, so a secret
# encoded into the query string leaves silently. A link is the milder cousin,
# needing a click. Both have a structural handle; a bare URL in prose does not.
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)")
_MD_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(\s*([^)\s]+)")


def check_answer(answer: str, contexts: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Findings for one finished answer. Empty means nothing was noticed.

    Every check runs against folded text, for the same reason the input side
    does: a secret respelled with a Cyrillic character walks past a comparison
    that reads bytes, and an output check is exactly where that would be most
    embarrassing.
    """
    folded, _ = normalize.fold(answer)
    findings: list[dict[str, str]] = []

    # A citation the retrieval never issued. Cheap to detect, and worth detecting
    # because a fabricated citation number destroys trust in every real one the
    # moment a reader follows it and finds nothing.
    allowed = range(1, len(contexts) + 1)
    bad = sorted({int(m.group(1)) for m in _CITATION.finditer(folded)}
                 - set(allowed))
    if bad:
        findings.append({
            "code": "citation_out_of_range",
            "detail": f"cited {', '.join(f'[{n}]' for n in bad)} with"
                      f" {len(contexts)} passage(s) retrieved",
        })

    # Whether each quoted claim is actually in the passage it cites. This is what
    # citation *existence* does not give you: a model that reads a forged policy
    # can attribute it to the real key of the passage that carried it, at which
    # point the key check passes and the false claim reads as sourced.
    #
    # It detects detached, invented, and stale citations. It does not prove
    # entailment: a quote can be real, in the right passage, and still not support
    # the sentence built around it. That needs task-specific factuality work, and
    # nothing here should be read as standing in for it.
    passages = [_comparable(str(c.get("text", ""))) for c in contexts]
    quoted: set[int] = set()
    unsupported: list[str] = []
    for m in _CITED_QUOTE.finditer(folded):
        n = int(m.group(1))
        quoted.add(n)
        if n not in allowed:
            continue  # already reported as out of range
        if _comparable(m.group(2)) not in passages[n - 1]:
            unsupported.append(f"[{n}]")
    if unsupported:
        findings.append({
            "code": "citation_unsupported",
            "detail": f"quoted text not found in the cited passage: "
                      f"{', '.join(sorted(set(unsupported)))}",
        })

    # A citation carrying no quote is verified against nothing. Reported, because
    # otherwise a model that quietly stops quoting disables the check above and
    # every finding here keeps reading green.
    unquoted = sorted({int(m.group(1)) for m in _CITATION.finditer(folded)}
                      & set(allowed) - quoted)
    if unquoted:
        findings.append({
            "code": "citation_unquoted",
            "detail": f"cited without an evidence span: "
                      f"{', '.join(f'[{n}]' for n in unquoted)}",
        })

    # The answer reproducing the fence means the model is describing the prompt's
    # structure back to the caller, which is what a successful injection looks
    # like from out here.
    if marker_shaped(folded):
        findings.append({
            "code": "fence_echoed",
            "detail": "the answer reproduced an untrusted-content marker",
        })

    if SYSTEM_CANARY in folded:
        findings.append({
            "code": "system_prompt_echoed",
            "detail": "the answer repeated part of the system prompt",
        })

    urls = _MD_IMAGE.findall(folded)
    if urls:
        findings.append({
            "code": "markdown_image",
            "detail": f"answer embedded {len(urls)} image URL(s): {urls[0]}",
        })
    links = _MD_LINK.findall(folded)
    if links:
        findings.append({
            "code": "markdown_link",
            "detail": f"answer embedded {len(links)} link URL(s): {links[0]}",
        })

    return findings
