#!/usr/bin/env python3
"""Check and repair the line anchors in the education docs.

The docs promise the same code at the same line references, and nothing keeps
that promise. `check_links.py` resolves link *targets*, so
`providers.py#L51-L52` passes whether or not line 51 still has anything to do
with the sentence pointing at it. The anchor is the part that rots and it was the
part nothing checked: three passes of security work moved these ranges, and each
time the repair was manual and the drift was found by reading rather than by CI.

The fix is to give each anchor an intent record. Markdown link titles are the
place: a link written

    [providers.py:61-74](../../knowledge_desk/providers.py#L61-L74 "fence_tags")

says which symbol it means. The title renders as a tooltip and is otherwise
invisible, so the docs read exactly as before. This script resolves the symbol
with `ast` and either verifies the range or rewrites it.

    python scripts/anchors.py            # check, exit 1 on drift
    python scripts/anchors.py --fix      # rewrite drifted anchors and link text
    python scripts/anchors.py --adopt    # add titles where a range already
                                         # matches a symbol exactly
    python scripts/anchors.py --tidy     # pull boundaries off blank lines

Links with no title are still checked for the two failures that need no intent:
link text disagreeing with its own anchor, and a range starting or ending on a
blank line. Those catch the sloppiness; only a title catches staleness.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "knowledge_desk"

# [mod.py:12-34](any/path/mod.py#L12-L34 "symbol")   title optional
LINK = re.compile(
    r"\[(?P<text_mod>[a-z_]+\.py):(?P<text_range>[\d-]+)\]"
    r"\((?P<path>[^)\s]*?(?P<mod>[a-z_]+\.py))"
    r"#L(?P<start>\d+)(?:-L(?P<end>\d+))?"
    r"(?:\s+\"(?P<symbol>[^\"]+)\")?\)"
)


def symbol_ranges(path: pathlib.Path) -> dict[str, tuple[int, int]]:
    """Every module-level symbol in `path`, mapped to its 1-based line span.

    Methods are included as `Class.method`. A decorated definition starts at its
    first decorator, because that is where a reader looking for the function
    would say it begins.
    """
    tree = ast.parse(path.read_text())
    out: dict[str, tuple[int, int]] = {}

    def span(node: ast.AST) -> tuple[int, int]:
        start = node.lineno
        for dec in getattr(node, "decorator_list", []):
            start = min(start, dec.lineno)
        return start, node.end_lineno or start

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out[node.name] = span(node)
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef):
                    out[f"{node.name}.{sub.name}"] = span(sub)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            out[node.name] = span(node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = (node.lineno, node.end_lineno or node.lineno)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = (node.lineno, node.end_lineno or node.lineno)
    return out


def docs() -> list[pathlib.Path]:
    return sorted([*(ROOT / "docs").rglob("*.md"), ROOT / "README.md"])


def _shrink(src: list[str], start: int, end: int) -> tuple[int, int]:
    """Pull a range's boundaries off blank lines, inwards.

    Always safe: the range still covers exactly the same code, since the lines
    given up were empty. Worth doing on its own (a range that visibly starts on
    nothing reads as a mistake) and worth doing before --adopt, which matches a
    symbol's span exactly and cannot see past a stray blank line.
    """
    while start < (end or start) and src[start - 1].strip() == "":
        start += 1
    while end > start and end <= len(src) and src[end - 1].strip() == "":
        end -= 1
    return start, end


def run(fix: bool, adopt: bool, tidy: bool) -> int:
    problems: list[str] = []
    ranges = {p.name: symbol_ranges(p) for p in PKG.glob("*.py")}
    lines = {p.name: p.read_text().splitlines() for p in PKG.glob("*.py")}

    for md in docs():
        text = md.read_text()
        rel = md.relative_to(ROOT)

        # rel bound as a default: re.sub calls this synchronously inside the
        # loop, but a closure over a loop variable is the kind of thing that
        # stops being true the moment someone makes this concurrent.
        def repair(m: re.Match[str], rel: pathlib.Path = rel) -> str:
            mod, symbol = m.group("mod"), m.group("symbol")
            start, end = int(m.group("start")), int(m.group("end") or 0)
            src = lines.get(mod)
            if src is None:
                return m.group(0)

            want: tuple[int, int] | None = None
            if symbol:
                want = ranges[mod].get(symbol)
                if want is None:
                    problems.append(f"{rel}: {mod} has no symbol {symbol!r}")
                    return m.group(0)
            elif adopt:
                # Migration: a range that already matches a symbol exactly gets
                # that symbol recorded, so it is checkable from now on.
                for name, (a, b) in ranges[mod].items():
                    if (a, b) == (start, end or start):
                        symbol, want = name, (a, b)
                        break

            if want is None:
                # No intent to check against, so only the failures that need
                # none: text disagreeing with its own anchor, and a boundary
                # landing on a blank line.
                expect = f"{start}-{end}" if end else str(start)
                if m.group("text_range") != expect:
                    problems.append(
                        f"{rel}: text says {mod}:{m.group('text_range')},"
                        f" anchor says L{expect}")
                    if fix:
                        return _render(m, mod, start, end, None)
                blank = [n for n in filter(None, (start, end))
                         if n <= len(src) and src[n - 1].strip() == ""]
                if blank:
                    problems.append(
                        f"{rel}: {mod}:{','.join('L' + str(n) for n in blank)}"
                        f" {'are' if len(blank) > 1 else 'is'} a blank line")
                    if tidy:
                        ns, ne = _shrink(src, start, end)
                        return _render(m, mod, ns, ne, None)
                return m.group(0)

            # A missing end means a single-line link, so compare it as a span of
            # one. Without this a one-line symbol reports drift on every run and
            # the rewrite is a no-op, which is a checker that can never go green.
            want_text = (f"{want[0]}-{want[1]}" if want[0] != want[1]
                         else str(want[0]))
            if (start, end or start) != want or m.group("text_range") != want_text:
                problems.append(
                    f"{rel}: {mod} {symbol!r} is at L{want[0]}-L{want[1]},"
                    f" link says L{start}-L{end or start}")
                if fix or adopt:
                    return _render(m, mod, want[0], want[1], symbol)
            elif adopt and not m.group("symbol"):
                return _render(m, mod, want[0], want[1], symbol)
            return m.group(0)

        new = LINK.sub(repair, text)
        if new != text and (fix or adopt or tidy):
            md.write_text(new)

    if problems:
        verb = "repaired" if (fix or adopt or tidy) else "found"
        print(f"{len(problems)} anchor problem(s) {verb}:")
        for p in problems:
            print(f"  {p}")
        return 0 if (fix or adopt or tidy) else 1
    print("All doc anchors resolve to the symbol they name.")
    return 0


def _render(m: re.Match[str], mod: str, start: int, end: int, symbol: str | None) -> str:
    span = f"{start}-{end}" if end and end != start else str(start)
    anchor = f"#L{start}" + (f"-L{end}" if end and end != start else "")
    title = f' "{symbol}"' if symbol else ""
    return f"[{mod}:{span}]({m.group('path')}{anchor}{title})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fix", action="store_true", help="rewrite drifted anchors")
    ap.add_argument("--adopt", action="store_true",
                    help="record the symbol for anchors that already match one")
    ap.add_argument("--tidy", action="store_true",
                    help="pull range boundaries off blank lines, inwards")
    args = ap.parse_args()
    return run(fix=args.fix, adopt=args.adopt, tidy=args.tidy)


if __name__ == "__main__":
    sys.exit(main())
