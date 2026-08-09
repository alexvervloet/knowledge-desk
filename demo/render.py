#!/usr/bin/env python3
"""Pretty-print the /ask SSE stream arriving on stdin. Reads no network, so it
works under the python.org macOS build that ships without a CA bundle."""

from __future__ import annotations

import json
import os
import sys

DIM, BOLD, CYAN, GREEN, YELLOW, RESET = (
    "\033[2m", "\033[1m", "\033[36m", "\033[32m", "\033[33m", "\033[0m",
)
WIDTH = 74


def main() -> int:
    org = os.environ.get("ORG", "?")
    question = os.environ.get("QUESTION", "")
    print(f"{BOLD}{CYAN}{org}{RESET} asks: {question}")

    answer: list[str] = []
    usage: dict = {}
    cost = 0.0
    err = None

    for line in sys.stdin:
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        kind = event["type"]
        if kind == "sources":
            paths = ", ".join(s["path"] for s in event["sources"]) or "nothing"
            print(f"  {DIM}retrieved:{RESET} {paths}")
        elif kind == "token":
            answer.append(event["text"])
        elif kind == "done":
            usage, cost = event["usage"], event["cost_usd"]
        elif kind == "error":
            err = event["message"]

    if err:
        print(f"  {YELLOW}{err}{RESET}")
        return 1

    buf, lines = "", []
    for word in "".join(answer).split():
        if len(buf) + len(word) + 1 > WIDTH:
            lines.append(buf)
            buf = word
        else:
            buf = f"{buf} {word}".strip()
    lines.append(buf)
    for text in lines:
        print(f"  {GREEN}{text}{RESET}")

    print(f"  {DIM}claude · {usage.get('input_tokens')} in / "
          f"{usage.get('output_tokens')} out · ${cost:.4f}{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
