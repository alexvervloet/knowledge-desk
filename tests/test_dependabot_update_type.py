"""The auto-merge gate's only judgment call, tested in both directions.

A checker that has never been made to fail is a checker nobody has a reason to
believe, and this one decides whether a pull request merges without a person.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from dependabot_update_type import Verdict, classify  # noqa: E402

TRAILER = """build(deps): bump actions/checkout from 4 to 7

Bumps actions/checkout from 4 to 7.

---
updated-dependencies:
- dependency-name: actions/checkout
  dependency-version: 7.0.0
  dependency-type: direct:production
  update-type: version-update:semver-{level}
...
"""


def test_major_is_left_for_review() -> None:
    assert classify(TRAILER.format(level="major")) is Verdict.MAJOR


def test_minor_and_patch_may_merge() -> None:
    assert classify(TRAILER.format(level="minor")) is Verdict.MINOR_OR_PATCH
    assert classify(TRAILER.format(level="patch")) is Verdict.MINOR_OR_PATCH


def test_a_group_with_one_major_in_it_is_major() -> None:
    """Grouped updates list several dependencies. One major anywhere decides it.

    This repo groups the python and npm minor-and-patch updates, and react had
    to move as a set, so a group carrying a major is not hypothetical here.
    """
    grouped = (
        "build(deps): bump the npm-minor-and-patch group\n"
        "---\n"
        "updated-dependencies:\n"
        "- dependency-name: vite\n"
        "  update-type: version-update:semver-patch\n"
        "- dependency-name: react\n"
        "  update-type: version-update:semver-major\n"
        "...\n"
    )
    assert classify(grouped) is Verdict.MAJOR


def test_a_message_with_no_update_type_fails_closed() -> None:
    """Dependabot has shipped versions that leave update-type off pip updates.
    'I could not tell' must not merge."""
    assert classify("build(deps): bump something from 1 to 2\n") is Verdict.UNKNOWN
    assert classify("") is Verdict.UNKNOWN
