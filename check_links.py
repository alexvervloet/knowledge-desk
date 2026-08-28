"""Check that every relative link in the Markdown docs resolves to a real file.

Moving a doc silently breaks every relative link pointing at it, and nothing
else in CI reads Markdown. This walks the git-tracked .md files, resolves each
relative target against the file's own directory, and exits non-zero on a miss.

External links (http, mailto) and bare anchors are not checked. A `#Lnn` line
fragment on a source file is ignored: the file has to exist, the line number is
not verified.

    python check_links.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Link text may wrap across lines, so this is matched against the whole file
# rather than line by line.
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)", re.DOTALL)
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")

# Fenced blocks and inline code spans, which may quote link syntax verbatim.
# An inline span may wrap one line; more than that is more likely a stray
# backtick, and blanking to the next one would hide real links.
CODE = re.compile(r"```.*?```|`[^`\n]*(?:\n[^`\n]*)?`", re.DOTALL)

ROOT = Path(__file__).resolve().parent


def tracked_markdown() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ROOT / line for line in out.splitlines() if line]


def blank_code(text: str) -> str:
    """Blank out code, keeping length so match offsets still give line numbers."""
    return CODE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def broken(path: Path) -> list[tuple[int, str]]:
    text = blank_code(path.read_text())
    misses = []
    for match in LINK.finditer(text):
        target = match.group(1)
        if target.startswith(SKIP_PREFIXES):
            continue
        file_part = target.split("#")[0]
        if not file_part:
            continue
        if not (path.parent / file_part).exists():
            lineno = text.count("\n", 0, match.start()) + 1
            misses.append((lineno, target))
    return misses


def main() -> int:
    total = 0
    for path in tracked_markdown():
        for lineno, target in broken(path):
            total += 1
            print(f"{path.relative_to(ROOT)}:{lineno}: broken link -> {target}")
    if total:
        print(f"\n{total} broken link(s).", file=sys.stderr)
        return 1
    print("All relative Markdown links resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
