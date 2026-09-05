#!/usr/bin/env python3
"""Classify a Dependabot commit message as major, minor-or-patch, or unknown.

`dependabot/fetch-metadata` would normally do this, and it cannot be used here.
It reads the `pull_request` key out of the event payload and has no input for
naming a PR, so it does nothing useful under a `workflow_run` trigger. The
metadata it parses is not private, though: Dependabot writes it into the commit
message as a trailer, and that is what this reads.

Fail closed. A message with no `update-type` line at all classifies as `unknown`
and the caller leaves the pull request alone, because "I could not tell" and
"it is safe" are different answers and only one of them may merge.
"""

from __future__ import annotations

import sys
from enum import StrEnum

MAJOR_MARKER = "update-type: version-update:semver-major"
ANY_MARKER = "update-type: version-update:semver-"


class Verdict(StrEnum):
    MAJOR = "major"
    MINOR_OR_PATCH = "minor-or-patch"
    UNKNOWN = "unknown"


def classify(message: str) -> Verdict:
    """A grouped update lists several dependencies, so one major anywhere is major."""
    if MAJOR_MARKER in message:
        return Verdict.MAJOR
    if ANY_MARKER in message:
        return Verdict.MINOR_OR_PATCH
    return Verdict.UNKNOWN


def main() -> int:
    print(classify(sys.stdin.read()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
