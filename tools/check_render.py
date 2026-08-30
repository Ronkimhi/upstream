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

A second class, added 2026-08-29 after the first real deep dive: a field that
tools/check_analyst.py REQUIRES of every dive and that no template renders. That is the
mirror of the defect above — the page is not inventing a number, it is withholding one the
gate insisted on — and it is harder to see, because every check reports green. It shipped
that way with `price_source_note`: mandatory on every dive resting on a single unconfirmed
price print, rendered nowhere, so the disclosure existed only in the JSON. See
gate_rendered_failures() below; the field list is scanned out of check_analyst.py rather
than retyped, so the next such field fails this check on the day it is added.

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
    # --- added 2026-08-30 with the payload projections -----------------------------
    # app/build.py stopped inlining every store at full fidelity: a market series is
    # downsampled to MARKET_SERIES_POINTS and only carried for tickers with a dive,
    # changelogs and notes are carried as a tail, impact evidence and link citations are
    # carried as counts. Every one of those is a truthful cut ONLY while the page says so.
    # These four rules are the ways the renderer could quietly un-say it.
    (r"\.length\s*\+\s*\"\s*(sessions|trading days|days)\b",
     "counting inlined series points as sessions lies the moment the series is "
     "downsampled — the hardcoded-window defect with an extra step",
     "anchor the window to a real date from the data (rows[0][0]), never to a point count"),
    (r"(rows|items|notes|changelog|candidates|requests)\s*\|\|\s*\[\]\s*\)\s*\.length\s*\+\s*\"\s*(total|in all|on file|held)",
     "printing the number of INLINED rows under a word that means the whole store is the "
     "\"300 HELD\" defect: the page carries a tail, the store holds more",
     "print the *_total / row_count / settled denominator app/build.py ships beside it"),
    (r"series\.rows\s*\|\|\s*\[\]\s*\)\s*\.length\s*[^\n]*\bof\b\s*\d",
     "comparing inlined price points against a literal asserts a series length nothing knows",
     "use series.row_count and series.inlined_rows, which app/build.py writes"),
    (r"evidence\s*\|\|\s*\[\]\s*\)\s*\.length[^\n]*impact",
     "impact evidence arrays are no longer carried on the page; counting them here would "
     "render 0 cited sources for a leg that has several",
     "render the leg's evidence_count, which app/build.py projects"),
]

# Structures whose absence silently degrades the page rather than breaking it.
REQUIRE = [
    ("app/templates/app.js", r"function num\(", "the num() zero-vs-null helper"),
    ("app/templates/app.js", r"function isPlottable\(",
     "the single definition of a plottable link (the scatter and the not-scored list "
     "must not disagree)"),
    ("app/templates/app.js", r"function opportunityChip\(",
     "the opportunity-tier renderer that keeps O1/O2/O3 distinct from data tiers"),
    ("app/templates/app.js", r"function campaignView\(",
     "the Campaign route, including its truthful missing-data state"),
    ("app/build.py", r"def build_campaign_ix\(",
     "the bounded campaign projection that keeps evidence and profiles out of the page"),
    ("app/templates/app.js", r"function agentView\(",
     "the per-agent contract page, the only place the instructions an agent runs under "
     "are readable and editable"),
    ("app/templates/app.js", r"function agentChip\(",
     "the owner chip on every Run button: it resolves the command to its agent through "
     "the shipped registry, so no button can run an agent the reader cannot identify"),
    ("app/templates/shell.html", r'id="upstream-queue"',
     "the click-queue block every Run button appends to"),
    ("app/templates/shell.html", r'id="upstream-edits"',
     "the contract-edit block the agent editor publishes into; without it every Save "
     "silently fails the same way a missing queue block kills every Run button"),
    ("app/templates/shell.html", r"window\.UPSTREAM_DATA = ",
     "the data blob marker app/build.py --check reads to compare the page against data/"),
    # --- the projections must stay visible to the reader (added 2026-08-30) ---------
    ("app/templates/app.js", r"function seriesNote\(",
     "the price-series disclosure: how many points the page is carrying against how many "
     "data/market/<T>.json holds, and the fact that a header-only ticker has none"),
    ("app/templates/app.js", r"function carriedTail\(",
     "the notes/history disclosure: the page carries a tail of an append-only list and "
     "must print the full count beside it"),
    ("app/templates/app.js", r"seriesNote\(mk, st\.ticker\)",
     "the series disclosure actually rendered under the price chart — a helper nobody "
     "calls is the same silence as no helper at all"),
    ("app/templates/app.js", r"evidence_count",
     "the count of impact-leg sources the page carries instead of their verbatim "
     "excerpts; without it a sourced leg reads as an unsourced assertion"),
    ("app/templates/app.js", r"st\.detail_inlined === false",
     "the dive page for a writeup this build could not carry in full — without this "
     "branch an uncarried dive draws empty Bull, Bear and Red team cards, which reads as "
     "\"never written\" instead of \"not on this page\""),
    ("app/build.py", r"def _elastic_stock_budgets\(",
     "the rule that prices the rest of the page first and gives dives the change, so a "
     "small repo carries every dive whole and a finished campaign still opens"),
    ("app/templates/app.js", r"legs_inlined",
     "the state where an appraisal is carried at chip fidelity only — the card must say "
     "the reasoning is in the file, not draw a blank where it would go"),
    ("app/build.py", r"def project_market\(",
     "the market projection that keeps unrendered fundamentals, insider and prints blocks "
     "off the page and windows the series to the tickers that draw one"),
    ("app/build.py", r"def project_impact\(",
     "the impact projection that keeps verbatim source_excerpt spans in data/impact/ and "
     "carries their count instead"),
    ("app/build.py", r"def build_payload\(",
     "the payload builder, extracted from main() so the page's scale is testable at "
     "campaign size instead of only at today's size"),
]


# --- second defect class: gate-required, reader-invisible -------------------------
#
# The denylist above catches the page ASSERTING something the data never said. This
# catches its mirror: the data saying something MANDATORY that the page never shows.
#
# `tools/check_analyst.py` fails a dive that omits certain fields, so a field can be
# required and rendered nowhere at once — which is worse than an absent field, because
# the gate reports green while the reader is never told the thing the gate insisted on.
# `price_source_note` shipped in exactly that state (found 2026-08-29 by the first real
# dive): required on every dive whose market file is SINGLE_SOURCE, printed by no
# template, so a dive whose every level rested on one unconfirmed print read like any
# other dive on the site. The dive worked around it by repeating the warning in a note,
# which is a per-dive habit that rots the first time a dive forgets.
#
# The list of fields is NOT retyped here. It is scanned out of check_analyst.py, so a
# field added to the gate tomorrow fails this check until someone either renders it or
# exempts it with a stated reason. A hand-maintained copy would have drifted, and the
# drift would be invisible in precisely the direction that hurts: silently shorter.
GATE_FIELD_RX = re.compile(
    r'(?<![A-Za-z0-9_])d\.get\("([a-z_]+)"\)'     # d.get("price_source_note")
    r'|"([a-z_]+)" not in d'                        # "link_id" not in d
)

# Fields the gate checks WITHOUT naming them: check_analyst's quote walk recurses the
# whole dive looking for `quote` strings, so these arrays are gate-verified while the
# regex above cannot see them. Verified evidence that renders nowhere is the same defect
# as an unrendered disclosure — the check verifies it, the reader never reads it.
WALKED_BY_QUOTE_SCAN = ("filing_evidence",)

# field -> why it legitimately never reaches the page. An entry here is a claim that a
# reader gains nothing from the field; it must be argued, not assumed, which is why the
# reason is a required string rather than a bare set member.
RENDER_EXEMPT: dict[str, str] = {}

# Vacuous-pass guards. Both scans key on a variable name: `d` for the dive in
# check_analyst.py, `st` for the dive in app.js. Rename either and a regex scan finds
# nothing and reports a clean pass over an empty set, which is the worst outcome a check
# can have. So: refuse to pass on an implausibly small harvest, and refuse to pass
# without a sentinel field proving the JS side is being read at all.
MIN_GATE_FIELDS = 15
JS_DIVE_SENTINEL = "st.verdict"


def gate_rendered_failures(analyst_src: str, js_src: str) -> tuple:
    """Every dive field the analyst gate names must have a render path in app.js.

    Pure string in, failures out, so it is testable without a tree on disk.
    Returns (failures, one-line report of what was examined).
    """
    fields = {a or b for a, b in GATE_FIELD_RX.findall(analyst_src)}
    fields |= set(WALKED_BY_QUOTE_SCAN)
    failures = []
    if len(fields) < MIN_GATE_FIELDS:
        failures.append(
            f"gate-field scan found only {len(fields)} dive field(s) in check_analyst.py "
            f"(expected at least {MIN_GATE_FIELDS}) — the gate's dive variable was probably "
            f"renamed away from `d`, and this check was about to pass over nothing")
        return failures, f"gate fields: SCAN BROKEN ({len(fields)} found)"
    if JS_DIVE_SENTINEL not in js_src:
        failures.append(
            f"app.js no longer contains `{JS_DIVE_SENTINEL}` — stockView's dive variable was "
            f"renamed, so scanning for `st.<field>` would report every field unrendered or, "
            f"worse, every field rendered. Update JS_DIVE_SENTINEL and the scan together")
        return failures, "gate fields: SCAN BROKEN (dive variable renamed in app.js)"
    rendered = 0
    for f in sorted(fields):
        if f in RENDER_EXEMPT:
            continue
        if re.search(r"\bst\." + f + r"\b", js_src):
            rendered += 1
        else:
            failures.append(
                f"app.js never renders `st.{f}`, which tools/check_analyst.py requires of "
                f"every dive — a field the gate insists on and the page hides is a green "
                f"check over an uninformed reader. Render it in stockView, or add it to "
                f"RENDER_EXEMPT with the reason a reader gains nothing from it")
    return failures, (f"gate fields: {rendered}/{len(fields) - len(RENDER_EXEMPT)} dive "
                      f"field(s) required by check_analyst.py have a render path in app.js"
                      + (f" ({len(RENDER_EXEMPT)} exempt)" if RENDER_EXEMPT else ""))


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

    analyst = root / "tools" / "check_analyst.py"
    if not analyst.exists():
        failures.append("tools/check_analyst.py not found — the gate-required/unrendered "
                        "check cannot run, and a check that cannot run must say so")
        gate_report = "gate fields: NOT CHECKED"
    else:
        gate_fails, gate_report = gate_rendered_failures(analyst.read_text(),
                                                         js_path.read_text())
        failures.extend(gate_fails)
        checked += 1

    print(f"check_render: {checked} invariant(s) checked over {len(lines)} lines of app.js")
    print(f"  {gate_report}")
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
