#!/usr/bin/env python3
"""Renderer lint. Stdlib only, offline.

`tools/validate.py` governs what is in data/. Nothing governed what the PAGE does with
it, and the two are different failure modes: the renderer can invent a number that never
existed in any file. The 2026-08-29 pressure test found four of these, and they share one
shape — a JavaScript fallback (`||`, a default argument, a hardcoded literal) standing in
for a value that was absent, printed to the reader as though it were data:

  * `esc(mk.series.currency || "USD")` — no market file has ever carried `currency`, so
    every price chart asserted a currency from a fallback.
  * `l.heat.capture ? l.heat.capture.score : 40` — a link with no capture score rendered
    a hover card reading "capture 40".
  * `(h.impact || {}).score || "–"` — a legitimate score of 0 rendered as missing.
  * `"% over 90 sessions"` — printed 90 whatever the actual window was.

Each is one character away from correct and none of them is visible in review. This file
is the denylist, so the class cannot come back silently. It also guards the structures the
click-queue depends on.

Run: python3 tools/check_render.py [--root PATH]
Exit 0 clean, 1 on any failure.
"""
import argparse
import re
import sys
from pathlib import Path

# (regex, what it would do to the reader, how to write it instead)
DENY = [
    (r"\|\|\s*40\b",
     "a score falling back to the literal 40 renders an invented number as data",
     "test `x != null` and render 'unscored' when it is absent"),
    (r":\s*40\s*;",
     "a ternary defaulting a score to 40 renders an invented number as data",
     "test `x != null` and render 'unscored' when it is absent"),
    (r"currency\s*\|\|\s*[\"']",
     "no market file carries `currency`; a fallback here asserts a currency nothing knows",
     "print the currency only when the data actually has one"),
    (r"\.score\s*\|\|\s*[\"']",
     "`score || \"...\"` renders a legitimate score of 0 as missing",
     "use num(score) / an explicit `!= null` test"),
    (r"probability_pct\s*\|\|\s*[\"']",
     "`probability_pct || \"?\"` renders a legitimate 0% as unknown",
     "use num(probability_pct, \"?\")"),
    (r"over\s+90\s+sessions",
     "a hardcoded window length lies whenever fewer rows exist",
     "print the length of the window actually used"),
    (r"TODAY\s*=\s*\(?\s*D\.built_at",
     "anchoring TODAY to built_at freezes staleness badges at build time",
     "anchor TODAY to the viewer's clock; keep built_at only as the build stamp"),
    (r"impact\s*≥\s*\d+|crowdedness\s*≤\s*\d+|capture\s*≥\s*\d+",
     "money-corner thresholds retyped in the UI duplicate tools/validate.py and can drift",
     "read them from D.method, which app/build.py injects"),
]

# Structures whose absence silently degrades the page rather than breaking it.
REQUIRE = [
    ("app/templates/app.js", r"function num\(", "the num() zero-vs-null helper"),
    ("app/templates/app.js", r"function isPlottable\(",
     "the single definition of a plottable link (the scatter and the not-scored list "
     "must not disagree)"),
    ("app/templates/shell.html", r'id="upstream-queue"',
     "the click-queue block every Run button appends to"),
    ("app/templates/shell.html", r"window\.UPSTREAM_DATA = ",
     "the data blob marker app/build.py --check reads to compare the page against data/"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    args = ap.parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent

    failures, checked = [], 0
    js_path = root / "app" / "templates" / "app.js"
    if not js_path.exists():
        print(f"check_render: {js_path} not found")
        return 1
    lines = js_path.read_text().splitlines()
    for pattern, why, instead in DENY:
        rx = re.compile(pattern)
        checked += 1
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith(("*", "/*", "//")):
                continue  # the comments here quote these patterns on purpose
            if rx.search(line):
                failures.append(f"app.js:{i}: {why}\n      found: {stripped[:110]}\n      "
                                f"instead: {instead}")
    for rel, pattern, what in REQUIRE:
        checked += 1
        p = root / rel
        if not p.exists():
            failures.append(f"{rel}: missing entirely, and it must carry {what}")
        elif not re.search(pattern, p.read_text()):
            failures.append(f"{rel}: lost {what}")

    print(f"check_render: {checked} invariant(s) checked over {len(lines)} lines of app.js")
    if failures:
        print("\nFAILURES:")
        for f_ in failures:
            print(f"  - {f_}")
        print(f"\ncheck_render: {len(failures)} failure(s)")
        return 1
    print("check_render: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
