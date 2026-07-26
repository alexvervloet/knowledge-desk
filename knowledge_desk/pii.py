"""Lightweight PII detection and redaction.

Regex-based and deliberately conservative: it catches the obvious, unambiguous
formats (email, US phone, SSN, card-shaped numbers) rather than trying to be a
full PII classifier. Two uses: flag documents that contain PII at ingest (so an
admin can see it), and redact PII out of audit-log detail before it is stored.
"""

from __future__ import annotations

import re

# Order matters: SSN before phone so a NNN-NN-NNNN string is not mislabeled.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("phone", re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b")),
]


def detect_types(text: str) -> list[str]:
    """Sorted, de-duplicated list of PII types present in `text`."""
    return sorted({name for name, pat in _PATTERNS if pat.search(text)})


def redact(text: str) -> str:
    """Replace each PII match with a [REDACTED-TYPE] marker."""
    for name, pat in _PATTERNS:
        text = pat.sub(f"[REDACTED-{name.upper()}]", text)
    return text


def redact_detail(detail: dict) -> dict:
    """Redact PII from the string values of an audit-log detail dict (one level)."""
    return {k: redact(v) if isinstance(v, str) else v for k, v in detail.items()}
