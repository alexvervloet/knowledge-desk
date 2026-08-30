"""The docs anchor checker.

Hermetic, and pointed at fixtures rather than the real docs: a test that asserts
the repository is currently clean would fail for whoever is mid-edit, and the
thing worth testing is that the checker notices, not that today it has nothing
to notice.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import anchors  # noqa: E402

SOURCE = '''\
"""Module docstring."""

CONST = {
    "a": 1,
}


def alpha(x):
    """Doc."""
    return x


class Thing:
    def method(self):
        return 1
'''


@pytest.fixture
def repo(tmp_path, monkeypatch):
    pkg = tmp_path / "knowledge_desk"
    pkg.mkdir()
    (pkg / "mod.py").write_text(SOURCE)
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("no links here\n")
    monkeypatch.setattr(anchors, "ROOT", tmp_path)
    monkeypatch.setattr(anchors, "PKG", pkg)
    return tmp_path


def link(start, end=None, symbol=None, mod="mod.py"):
    span = f"{start}-{end}" if end else str(start)
    anchor = f"#L{start}" + (f"-L{end}" if end else "")
    title = f' "{symbol}"' if symbol else ""
    return f"[{mod}:{span}](knowledge_desk/{mod}{anchor}{title})\n"


def write(repo, body):
    (repo / "docs" / "page.md").write_text(body)


def page(repo):
    return (repo / "docs" / "page.md").read_text()


def test_symbol_ranges_covers_functions_classes_methods_and_assignments(repo):
    got = anchors.symbol_ranges(repo / "knowledge_desk" / "mod.py")
    assert got["CONST"] == (3, 5)
    assert got["alpha"] == (8, 10)
    assert got["Thing"] == (13, 15)
    assert got["Thing.method"] == (14, 15)


def test_a_correct_titled_anchor_passes(repo):
    write(repo, link(8, 10, "alpha"))
    assert anchors.run(fix=False, adopt=False, tidy=False) == 0


def test_a_moved_symbol_is_caught(repo):
    """The failure nothing caught before: the range still spans real code, so
    every other check reads green while the reference means something else."""
    write(repo, link(3, 5, "alpha"))
    assert anchors.run(fix=False, adopt=False, tidy=False) == 1


def test_fix_repoints_a_moved_symbol_and_its_link_text(repo):
    write(repo, link(3, 5, "alpha"))
    anchors.run(fix=True, adopt=False, tidy=False)
    assert '[mod.py:8-10](knowledge_desk/mod.py#L8-L10 "alpha")' in page(repo)


def test_an_unknown_symbol_is_reported_rather_than_ignored(repo):
    write(repo, link(8, 10, "gone"))
    assert anchors.run(fix=False, adopt=False, tidy=False) == 1


def test_link_text_disagreeing_with_its_own_anchor_is_caught_without_a_title(repo):
    write(repo, "[mod.py:1-2](knowledge_desk/mod.py#L8-L10)\n")
    assert anchors.run(fix=False, adopt=False, tidy=False) == 1


def test_a_boundary_on_a_blank_line_is_caught_without_a_title(repo):
    write(repo, link(8, 11))  # line 11 is blank
    assert anchors.run(fix=False, adopt=False, tidy=False) == 1


def test_tidy_pulls_a_boundary_off_a_blank_line(repo):
    write(repo, link(8, 11))
    anchors.run(fix=False, adopt=False, tidy=True)
    assert "#L8-L10" in page(repo)


def test_adopt_records_a_symbol_only_on_an_exact_match(repo):
    write(repo, link(8, 10) + link(8, 9))
    anchors.run(fix=False, adopt=True, tidy=False)
    body = page(repo)
    assert '#L8-L10 "alpha"' in body  # exact span, adopted
    assert "#L8-L9)" in body  # partial span, left alone


def test_a_single_line_symbol_does_not_report_forever(repo):
    """(21, None) compared against (21, 21) reported drift on every run while the
    rewrite was a no-op, which is a checker that can never go green."""
    (repo / "knowledge_desk" / "mod.py").write_text("ONE = 1\n")
    write(repo, '[mod.py:1](knowledge_desk/mod.py#L1 "ONE")\n')
    assert anchors.run(fix=False, adopt=False, tidy=False) == 0
