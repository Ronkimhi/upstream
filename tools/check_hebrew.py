#!/usr/bin/env python3
"""The Hebrew page gate (Ron, 2026-09-13). Stdlib only, offline.

Ron asked for the whole platform in plain Hebrew, the register the chain explainers
already use: the words a ten-year-old follows, no jargon where a plain word exists.
`tools/check_render.py` governs what the page does with a number; nothing governed what
LANGUAGE the page speaks, and a renderer is edited by many sessions, each of which can add
one English label without noticing. This file is that governance, as a lint over the
sources the page is assembled from, so the page cannot drift back to English one string
at a time.

Four checks, each printing what it examined beside what it found:

  1. app/templates/app.js: every string literal that carries visible text (text between
     HTML tags, plus title=, aria-label= and placeholder= attribute values) is Hebrew, or is
     a token the page must keep in Latin script and says why (ALLOW below: machine
     vocabulary such as FINAL and O1, brand and product names, font names, commands and
     paths). An English phrase anywhere, a capitalised English word standing alone as a
     label, or any Latin text between tags that no rule allows, fails. The scan is a small
     JS tokenizer, not a regex over the file: app.js carries regex literals with quote
     characters in them (`/[&<>"']/g`) that break naive pairing.
     Stated blind spot: a tagless literal that is one lowercase Latin word (`"copied"`)
     is indistinguishable from a class name or a data key (`"neutral"`, `"impact"`)
     without reading the code around it, so it is not flagged. The 2026-09-13 pass
     translated those by hand; a later one slipping back is caught by eye, not here.
  2. The page declares itself: app.js sets dir=rtl and lang=he on the document element at
     boot and carries he(), the shells load a Hebrew-capable face (Heebo), app.css sets
     the base font stack Hebrew-first, keeps charts left-to-right and isolates Latin
     tokens. Without these the Hebrew renders in a fallback face, left-aligned, with
     tickers and dates scrambled by bidi reordering.
  3. app/templates/guide.html is written in Hebrew: it carries substantial Hebrew text and
     no English sentence outside <code>/<pre> (commands and paths stay as they are typed).
  4. tools/opportunities.py and tools/campaign_board.py, the two build-time prose
     generators whose sentences land on the Board and the Campaign page, carry no English
     sentence template.

Run: python3 tools/check_hebrew.py [--root PATH] [--list] [--js FILE]
Exit 0 clean, 1 on any failure. --list prints every flagged literal with its line.
--js lints one JavaScript file (a part of app.js while it is being translated) and
nothing else.
"""
import argparse
import html
import re
import sys
from pathlib import Path

HEBREW = re.compile("[֐-׿]")
# two Latin words in a row (the second lowercase, at least two letters): a phrase
PHRASE = re.compile(r"[A-Za-z][a-z]+(?:['’]s)?\s+[a-z]{2,}\b")
# one capitalised English word standing alone (a label such as "Board" or "Signals")
LABEL = re.compile(r"^\W*([A-Z][a-z]{3,})\W*$")
ENGLISH_SENTENCE = re.compile(r"(?:[A-Za-z]+\s+){5,}[A-Za-z]+")

FONT_NAME = (r"(?:Inter|Heebo|Newsreader|Georgia|serif|sans-serif|monospace|ui-monospace|"
             r"SFMono-Regular|Menlo|Arial|Segoe UI|-apple-system|Roboto|Iowan Old Style|"
             r"JetBrains Mono|'JetBrains Mono'|\"JetBrains Mono\")")

# Latin text the page keeps on purpose. Each entry is a regex matched against the whole
# visible segment (fullmatch after trimming) and a reason, because an allowlist without
# reasons grows until it allows everything. `markup_ok` says whether the rule also holds
# for text that sits between tags (visible for certain) or only for tagless literals,
# where a lowercase word is usually a class name or a data key.
ALLOW = [
    (r"use strict", "the strict-mode directive is not rendered", True),
    (r"(?:heat block missing|NULL heat|UNINVESTABLE: no listed name, no instrument|no expression scored)",
     "the unrankable-reason keys tools/opportunities.py writes into the Top 3 projection; matched as lookup keys, shown through their Hebrew values", False),
    (r"[A-Z0-9_./%:+\-]+(?:\s+[A-Z0-9_./%:+\-]+)*",
     "machine vocabulary and ids: FINAL, O1, T1, CHOKE POINT, REQ-…, ISO dates", True),
    (FONT_NAME + r"(?:\s*,\s*" + FONT_NAME + r")*", "font stacks handed to the canvas and to CSS", True),
    (r"(?:italic\s+)?(?:\d{3}\s+)?[\d.]+px\s+.*", "canvas font shorthand", True),
    (r"(?:▲\s*)?(?:UPSTREAM|Upstream|Claude|ChatGPT|GitHub|Codex|SPY|Google Fonts|Newsreader|Heebo)(?:\s*(?:Code|Pro|Max|Team|Enterprise|Plus|Actions|Pages))?",
     "brand and product names", True),
    (r"(?:Adam|Nell|Tally|Atlas|Ember|Sieve|Stocky|Cass|Hitch|Ron)", "the agents and the owner", True),
    (r"(?:run|refresh|request data|note|log trade|check health)\b.*",
     "typed commands are the machine's grammar and stay as typed", True),
    (r"python3\s+.*", "a shell command", True),
    (r"(?:data|app|tools|docs|\.claude)/[A-Za-z0-9_./<>\-]*", "repository paths", True),
    (r"[a-z0-9_.\-]+\.(?:json|md|html|py|js|jsonl)", "file names", True),
    (r"https?://\S+", "URLs", True),
    (r"upstream\.[A-Za-z]+", "localStorage keys", False),
    (r"#/[A-Za-z0-9/_\-]*", "routes", True),
    (r"[a-z]+(?:[-_][a-z0-9]+)+", "CSS classes, ids, data keys and slugs", False),
    (r"[a-z]+", "a single lowercase word in a tagless literal is a key, a class or a unit", False),
    (r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", "month abbreviations in dates", True),
    (r"(?:Enter|Escape|Space|Tab|Backspace|Delete|Home|End|PageUp|PageDown|Shift|Control|Alt|Meta|Arrow(?:Left|Right|Up|Down))",
     "DOM KeyboardEvent key names, compared in handlers and never shown", False),
    (r"(?:SHA-256|HTTP|UTF-8|PIN|CSV|JSON|PDF|SVG|DOM|O1|O2|O3|T1|T2|T3)", "acronyms and tiers", True),
    (r"[MBTKydh%]", "unit suffixes: money, years, days, hours, percent", True),
    (r"(?:x|×|\+|-|–|—|·|\?|!|…|✓|→|←|↑|↓|▸|▾|▴|⟲|⟳|✕|★|◎|◇|▢|◐|▲|›|/|\||\[|\]|\(|\)|,|\.|:|;|@|#|=|~|\^|\*|\$|%|&|_|\"|')+",
     "glyphs and punctuation", True),
    (r"(?:Pro|Max|Team|Enterprise|Plus|Fork|Settings|Apps|Connectors|Create PR|Sign in with GitHub|"
     r"Select folder|Cloud|Code|Source|Sign in)", "UI labels of other products, quoted as they appear there", True),
    (r"(?:PowerShell|macOS|Windows|Node\.js|Python 3\.12|GitHub Actions|GitHub Pages|Cloudflare(?: Pages| Access)?|"
     r"Zero Trust|Workers|Pages|Applications|Self-hosted|One-time PIN|Allow|Git|Zero Trust, Access, Applications)",
     "product names quoted in the guide's install steps", True),
]
ALLOW_RX = [(re.compile(p), why, markup_ok) for p, why, markup_ok in ALLOW]

ATTR_TEXT = re.compile(r"""\b(?:title|aria-label|placeholder|alt)=(?:"([^"]*)"|'([^']*)')""")
TAG = re.compile(r"<[^<>]*>")
OPEN_TAG_TAIL = re.compile(r"<[^<>]*$")      # a literal that ends inside a tag: '<span class="chip '
TAG_HEAD = re.compile(r"^[^<>]*>")           # a literal that starts inside a tag: '" title="x">copy'


def js_string_literals(src: str):
    """Yield (line_number, literal_text) for every string literal in JS source.

    A small state machine: it knows strings, template literals, line and block comments,
    and regex literals (a `/` after an operator, a bracket or a keyword starts a regex, so
    the quote characters inside `/[&<>"']/g` never open a string).
    """
    i, n, line = 0, len(src), 1
    prev_sig = ""  # last significant (non-space) character in code state
    out = []
    while i < n:
        ch = src[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += src.count("\n", i, j)
            i = j
            continue
        if ch in "\"'`":
            q = ch
            j = i + 1
            buf = []
            while j < n and src[j] != q:
                if src[j] == "\\" and j + 1 < n:
                    esc = src[j + 1]
                    if esc == "u" and j + 5 < n:
                        buf.append(chr(int(src[j + 2:j + 6], 16)))
                        j += 6
                        continue
                    buf.append({"n": "\n", "t": "\t"}.get(esc, esc))
                    j += 2
                    continue
                if src[j] == "\n":
                    line += 1
                buf.append(src[j])
                j += 1
            out.append((line, "".join(buf)))
            i = j + 1
            prev_sig = q
            continue
        if ch == "/":
            before = src[max(0, i - 6):i].rstrip()
            starts_regex = prev_sig == "" or prev_sig in "(,=:[!&|?{};+-*%<>~^" or \
                before.endswith(("return", "typeof", "case"))
            if starts_regex:
                j = i + 1
                in_class = False
                while j < n:
                    c = src[j]
                    if c == "\\":
                        j += 2
                        continue
                    if c == "[":
                        in_class = True
                    elif c == "]":
                        in_class = False
                    elif c == "/" and not in_class:
                        break
                    elif c == "\n":
                        break
                    j += 1
                i = j + 1
                prev_sig = "/"
                continue
        if not ch.isspace():
            prev_sig = ch
        i += 1
    return out


def visible_segments(literal: str):
    """[(segment, from_markup)]: the text a reader would see if this literal reached the
    DOM. Attribute values that render (title, aria-label, placeholder, alt) and the text
    between tags count as markup text; a literal with no markup at all is returned whole,
    because it may be a key or a class as easily as a label."""
    segs = []
    for m in ATTR_TEXT.finditer(literal):
        v = m.group(1) if m.group(1) is not None else m.group(2)
        if v:
            segs.append((v, True))
    has_markup = "<" in literal or ">" in literal
    stripped = TAG.sub(" ", literal)
    stripped = OPEN_TAG_TAIL.sub(" ", stripped)
    stripped = TAG_HEAD.sub(" ", stripped)
    stripped = html.unescape(stripped)
    for part in re.split(r"[·|]", stripped):
        part = part.strip()
        if part:
            segs.append((part, has_markup))
    return segs


# Latin runs that may sit INSIDE a Hebrew sentence without making it English: a typed
# command, a path, a URL, a brand, an agent's name, a key letter. Stripped before the
# phrase test so `"הרץ run radar כדי לרענן"` reads as the Hebrew sentence it is.
EMBEDDED_OK = re.compile(
    r"\b(?:run|refresh|request data|note|log trade|check health)\b[ A-Za-z0-9_.<>\-]*"
    r"|(?:data|app|tools|docs|\.claude)/[A-Za-z0-9_./<>\-]*"
    r"|https?://\S+"
    r"|\b(?:Claude(?: Code)?|ChatGPT|GitHub|Codex|SPY|Upstream|UPSTREAM|Adam|Nell|Tally|Atlas|Ember|Sieve|Stocky|Cass|Hitch|Ron|Esc|PCS|Piotroski F|Beneish M|Altman Z)\b"
    r"|[A-Z0-9_.\-]{2,}")


def allowed(seg: str, from_markup: bool):
    s = seg.strip().strip(".,:;()[]{}\"'").strip()
    if not s:
        return "empty"
    if HEBREW.search(s):
        rest = EMBEDDED_OK.sub(" ", s)
        return None if PHRASE.search(rest) else "hebrew"
    for rx, why, markup_ok in ALLOW_RX:
        if (markup_ok or not from_markup) and rx.fullmatch(s):
            return why
    return None


def english_failures(literal: str):
    """Why this literal would put English in front of the reader, or []."""
    fails = []
    for seg, from_markup in visible_segments(literal):
        if allowed(seg, from_markup) is not None:
            continue
        if PHRASE.search(seg):
            fails.append(f"English phrase {seg.strip()[:70]!r}")
        elif LABEL.match(seg):
            fails.append(f"English label {seg.strip()!r}")
        elif from_markup and re.search(r"[A-Za-z]{2,}", seg):
            fails.append(f"Latin text between tags {seg.strip()[:70]!r}")
    return fails


def lint_js(p: Path, list_all: bool, require_boot: bool):
    if not p.exists():
        return [f"{p}: missing"], f"{p.name}: NOT FOUND"
    src = p.read_text(encoding="utf-8")
    lits = js_string_literals(src)
    flagged, seen, hebrew_n = [], 0, 0
    for line, lit in lits:
        segs = visible_segments(lit)
        if not segs:
            continue
        seen += 1
        if HEBREW.search(lit):
            hebrew_n += 1
        for f in english_failures(lit):
            flagged.append(f"{p.name}:{line}: {f}")
    fails = list(flagged) if list_all else flagged[:40]
    if len(flagged) > 40 and not list_all:
        fails.append(f"{p.name}: … {len(flagged) - 40} more (run with --list)")
    if require_boot:
        for pat, what in [
            (r'documentElement\.setAttribute\("dir",\s*"rtl"\)', "the root dir=rtl declaration at boot"),
            (r'documentElement\.setAttribute\("lang",\s*"he"\)', "the root lang=he declaration at boot"),
            (r"function he\(", "the he() vocabulary gloss: machine tokens rendered as Hebrew words"),
        ]:
            if not re.search(pat, src):
                fails.append(f"{p.name}: lost {what}")
    report = (f"{p.name}: {len(lits)} string literals, {seen} with visible text, "
              f"{hebrew_n} Hebrew, {len(flagged)} English")
    return fails, report


def check_declarations(root: Path):
    fails = []
    rules = [
        ("app/templates/shell.html", r"family=Heebo", "the Heebo face on the dashboard shell"),
        ("app/templates/guide-shell.html", r"family=Heebo", "the Heebo face on the standalone guide"),
        ("app/templates/guide-shell.html", r'dir="rtl"', "dir=rtl on the standalone guide"),
        ("app/templates/app.css", r"font:\s*400\s+[\d.]+px/[\d.]+\s+Heebo", "Heebo first in the body font stack"),
        ("app/templates/app.css", r"svg\s*\{[^}]*direction:\s*ltr", "charts kept left-to-right (axes and text-anchor)"),
        ("app/templates/app.css", r"unicode-bidi:\s*isolate", "bidi isolation on Latin tokens inside Hebrew prose"),
    ]
    for rel, pat, what in rules:
        p = root / rel
        if not p.exists():
            fails.append(f"{rel}: missing entirely, and it must carry {what}")
        elif not re.search(pat, p.read_text(encoding="utf-8")):
            fails.append(f"{rel}: lost {what}")
    return fails, f"declarations: {len(rules)} checked"


def check_guide(root: Path):
    p = root / "app" / "templates" / "guide.html"
    if not p.exists():
        return [f"{p}: missing"], "guide: NOT FOUND"
    src = p.read_text(encoding="utf-8")
    body = src.split("</style>", 1)[-1]
    body = re.sub(r"<(pre|code)\b[^>]*>.*?</\1>", " ", body, flags=re.S)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    attrs = " ".join(v[0] or v[1] for v in ATTR_TEXT.findall(body))
    text = html.unescape(TAG.sub(" ", body)) + " " + attrs
    heb = len(HEBREW.findall(text))
    fails = []
    if heb < 2000:
        fails.append(f"guide.html: only {heb} Hebrew characters; the guide is written in Hebrew")
    sentences = [m.group(0) for m in ENGLISH_SENTENCE.finditer(text)]
    for s in sentences[:10]:
        fails.append(f"guide.html: English sentence outside <code>/<pre>: {s[:80]!r}")
    return fails, f"guide.html: {heb} Hebrew characters, {len(sentences)} English sentences outside code"


def check_generators(root: Path):
    fails = []
    checked = 0
    for rel, needles in [
        ("tools/opportunities.py", ["Moves hard", "keeps the profit", "no red team yet", "waiting for a dive",
                                    "Census done", "No listed name", "Open the verdict"]),
        ("tools/campaign_board.py", ["no chain exists for this theme yet", "no link carries a heat verdict",
                                     "bounded batch, at most", "profiles are in place", "awaits its fresh-context"]),
    ]:
        p = root / rel
        if not p.exists():
            fails.append(f"{rel}: missing")
            continue
        src = p.read_text(encoding="utf-8")
        checked += 1
        for nd in needles:
            if nd in src:
                fails.append(f"{rel}: English sentence template still present: {nd!r}")
    return fails, f"generators: {checked} scanned"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("--list", action="store_true", help="print every flagged literal")
    ap.add_argument("--js", help="lint this one JavaScript file and nothing else")
    args = ap.parse_args()
    if args.js:
        fails, report = lint_js(Path(args.js), True, require_boot=False)
        print("check_hebrew: " + report)
        for f in fails:
            print(f"  - {f}")
        print("check_hebrew: " + ("OK" if not fails else f"{len(fails)} failure(s)"))
        return 1 if fails else 0
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    failures, reports = [], []
    for fn in (lambda: lint_js(root / "app" / "templates" / "app.js", args.list, True),
               lambda: check_declarations(root), lambda: check_guide(root), lambda: check_generators(root)):
        f, r = fn()
        failures.extend(f)
        reports.append(r)
    print("check_hebrew: " + " | ".join(reports))
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        print(f"\ncheck_hebrew: {len(failures)} failure(s)")
        return 1
    print("check_hebrew: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
