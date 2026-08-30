#!/usr/bin/env python3
"""Screen postlude gate. Stdlib only, offline.

The screen stage was the one writing stage with no gate. `run radar`, `run chain` and
`run deepdive` each had a check_*.py holding their CLAUDE.md verify column as an exit
code; `run screen` had only the schema validator, which checks that an earnings nugget
has the KEYS `quote`, `accession` and `url` — never that the quote is real.

That gap sat directly under the loudest rule in the method:

    docs/method.md section 1: "Earnings quotes: fetch the real document first
    (data/edgar/docs/), keep only quotes that appear verbatim in it
    (whitespace-normalized, case-folded), drop the rest, fail closed if no document.
    A quote that cannot be verified is not evidence."

Written twice in prose, enforced nowhere. A fabricated quote with a plausible accession
number passed every gate in the repo and rendered on the stock page as a citation. This
file is that rule as an exit code.

Checks, each reported with the denominator it examined:
  1. VERBATIM QUOTES: every earnings nugget appears, verbatim, in the EDGAR document on
     disk for that ticker. Normalization is whitespace-collapse + case-fold + smart
     punctuation folding, exactly the comparison method section 1 specifies. Fails
     CLOSED: a nugget whose document is missing is a failure, not a skip, because
     "unverifiable" and "verified" must never land in the same bucket.
  2. the nugget's accession matches the document it was verified against
  3. PENDING_DATA rows have a request row backing them (a row that pends with nothing
     queued pends forever, and reads on the page as work in progress)
  4. cross-file CIK agreement for every row carrying one, int-normalized
  5. run-day only: a ledger line names the screen, and every money-corner link of the
     chain either appears among the screen's link_ids or is named in universe_note

Scope: checks 3-5 bind on a day that actually wrote a screen. Checks 1-2 always run over
every screen on disk, because a quote does not become true again tomorrow.

Run: python3 tools/check_screen.py [--date YYYY-MM-DD] [--root PATH]
Exit 0 clean, 1 on any failure.
"""
import argparse
import datetime
import json
import re
import sys
import unicodedata
from pathlib import Path

failures: list[str] = []
lines: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def report(msg: str) -> None:
    lines.append(msg)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


def safe_name(t) -> str:
    return str(t).replace(".", "-").replace("/", "-")


# Smart punctuation folding. A quote copied out of a filing routinely arrives with the
# typographic forms and is retyped with the ASCII ones (or the reverse), which is a
# transcription difference, not a fidelity difference. Folding both sides means the
# check fails on changed WORDS, never on changed glyphs — a check that cried wolf on
# curly apostrophes would be turned off within a week, and then nothing would be checked.
_QUOTES = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u00b4": "'", "\u02bc": "'",
}
# Zero-width and soft-hyphen characters, which EDGAR HTML sprinkles through words and
# which are invisible to whoever copies the quote out of the filing.
_INVISIBLE = frozenset("\u200b\u200c\u200d\u00ad\ufeff")


def normalize(s: str) -> str:
    """Whitespace-normalized, case-folded, punctuation-folded - method section 1.

    Every Unicode dash (category Pd) folds to ASCII "-" and every Unicode space (Zs)
    folds to " ", rather than being enumerated. The hand-written list missed U+2010
    HYPHEN and U+2011 NON-BREAKING HYPHEN, which is what EDGAR actually emits inside
    "year-over-year" and "book-to-bill", so a correctly transcribed quote failed to match
    its own filing. That direction of error is the dangerous one: a verifier that rejects
    true quotes gets switched off, and then nothing is verified at all.
    """
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKC", s)
    out = []
    for ch in s:
        if ch in _INVISIBLE:
            continue
        if ch in _QUOTES:
            out.append(_QUOTES[ch])
        elif unicodedata.category(ch) == "Pd":      # every dash/hyphen form
            out.append("-")
        elif unicodedata.category(ch) == "Zs":      # every space form
            out.append(" ")
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip().casefold()


def check_quotes(data: Path, screens: list) -> None:
    """Check 1 + 2. The core of this file."""
    checked = verified = 0
    for path, s in screens:
        for bucket, rows in (s.get("buckets") or {}).items():
            for row in rows or []:
                ticker = row.get("ticker")
                for i, nug in enumerate(row.get("earnings_nuggets") or []):
                    checked += 1
                    where = f"{path.name}:{bucket}:{ticker}:nugget[{i}]"
                    quote = nug.get("quote") if isinstance(nug, dict) else None
                    if not isinstance(quote, str) or not quote.strip():
                        fail(f"{where}: earnings nugget carries no quote text")
                        continue
                    doc_path = data / "edgar" / "docs" / f"{safe_name(ticker)}.json"
                    doc = read_json(doc_path)
                    if not isinstance(doc, dict) or not isinstance(doc.get("text"), str):
                        # FAIL CLOSED. method section 1: "fail closed if no document".
                        fail(f"{where}: quoted a filing with no document on disk "
                             f"({doc_path.relative_to(data.parent)}) — a quote that cannot "
                             f"be verified is not evidence, so this is a failure, not a skip")
                        continue
                    if normalize(quote) in normalize(doc["text"]):
                        verified += 1
                    else:
                        fail(f"{where}: quote does NOT appear in {doc.get('form')} "
                             f"{doc.get('accession')} on disk. Quoted: "
                             f"{quote.strip()[:90]!r}")
                        continue
                    acc = nug.get("accession")
                    if acc and doc.get("accession") and str(acc) != str(doc["accession"]):
                        fail(f"{where}: cites accession {acc} but was verified against "
                             f"{doc['accession']} — the document on disk is a different "
                             f"filing; re-request the cited one")
    if checked:
        report(f"quotes: {verified}/{checked} earnings nugget(s) verified verbatim "
               f"against data/edgar/docs/")
    else:
        report("quotes: 0 earnings nuggets on disk (nothing to verify; the verifier is "
               "armed for the first one written)")


def check_pending_backed(data: Path, screens: list) -> None:
    """Check 3."""
    reqs = read_json(data / "requests.json", {"requests": []}) or {}
    requested = {str(r.get("ticker")) for r in reqs.get("requests", []) if r.get("ticker")}
    pending = unbacked = 0
    for path, s in screens:
        for bucket, rows in (s.get("buckets") or {}).items():
            for row in rows or []:
                marks = [v for v in (row.get("fundamentals"), row.get("crowdedness"))
                         if v == "PENDING_DATA"]
                if not marks:
                    continue
                pending += 1
                if str(row.get("ticker")) not in requested:
                    unbacked += 1
                    fail(f"{path.name}:{row.get('ticker')}: row is PENDING_DATA but no "
                         f"request row exists for it in data/requests.json — it will "
                         f"pend forever while reading as work in progress")
    report(f"pending: {pending - unbacked}/{pending} PENDING_DATA row(s) have a request "
           f"backing them" if pending else "pending: no PENDING_DATA rows")


def check_cik_agreement(data: Path, screens: list) -> None:
    """Check 4. int-normalized: a string CIK is a type wart, a different CIK is a defect.

    This is the check that would have caught VRT carrying 1674910 in the screen against
    1674101 everywhere else — digits transposed, so an EDGAR request keyed on the screen
    row would have resolved to the wrong filer or to nothing at all.
    """
    checked = agreed = 0
    for path, s in screens:
        for bucket, rows in (s.get("buckets") or {}).items():
            for row in rows or []:
                if row.get("cik") in (None, ""):
                    continue
                t = row.get("ticker")
                try:
                    row_cik = int(str(row["cik"]).strip())
                except (TypeError, ValueError):
                    fail(f"{path.name}:{t}: cik {row['cik']!r} is not a number")
                    continue
                for label, other in (("market", data / "market" / f"{safe_name(t)}.json"),
                                     ("edgar doc", data / "edgar" / "docs" / f"{safe_name(t)}.json")):
                    o = read_json(other)
                    if not isinstance(o, dict) or o.get("cik") in (None, ""):
                        continue
                    checked += 1
                    try:
                        other_cik = int(str(o["cik"]).strip())
                    except (TypeError, ValueError):
                        fail(f"{other.name}: cik {o['cik']!r} is not a number")
                        continue
                    if other_cik == row_cik:
                        agreed += 1
                    else:
                        fail(f"{path.name}:{t}: cik {row_cik} disagrees with the {label} "
                             f"file's {other_cik} — one of them points at the wrong filer")
    report(f"cik: {agreed}/{checked} cross-file CIK comparison(s) agree" if checked
           else "cik: no screen row carries a CIK to cross-check")


def check_run_day(data: Path, screens: list, chains_dir: Path, today: str) -> None:
    """Check 5. Only binds on a day a screen was written."""
    touched = [(p, s) for p, s in screens if str(s.get("as_of", ""))[:10] == today]
    if not touched:
        report(f"NOT RUN TODAY ({today}): 0 of {len(screens)} screen(s) written. "
               "Structural checks above still applied.")
        return
    ledger = ""
    try:
        ledger = (data / "ledger.md").read_text()
    except Exception:  # noqa: BLE001
        report("ledger: unreadable — skipping the ledger check (fails open, as elsewhere)")
    # Match the ledger line's COMMAND field, not the whole line. A line that merely
    # contains "screen" and the chain name is not evidence a screen ran: every line
    # written today while building this gate mentions both, and two successive
    # tightenings of a substring test still passed over unrelated lines. The ledger format
    # is `date | TYPE | <command> | by: ...`, so the command is field 3 and it either says
    # `run screen <chain>` or it does not.
    cmds = []
    for ln in ledger.splitlines():
        if not ln.startswith(today):
            continue
        parts = [f_.strip() for f_ in ln.split("|")]
        if len(parts) >= 3 and re.match(r"^(RUN|AMEND)$", parts[1]):
            cmds.append(parts[2])
    for p, s in touched:
        sid = str(s.get("id") or p.stem)
        chain_id = str(s.get("chain_id") or "")
        want = re.compile(rf"^(run screen|refresh)\b.*\b({re.escape(sid)}|{re.escape(chain_id)})\b")
        if not any(want.match(c) for c in cmds):
            fail(f"{p.name}: screen written today but no {today} ledger line whose COMMAND "
                 f"field is a screen run for {sid} (found: {cmds or 'no RUN/AMEND lines today'})")
        chain = read_json(chains_dir / f"{s.get('chain_id')}.json")
        if not isinstance(chain, dict):
            continue
        money = [l_["id"] for l_ in chain.get("links", [])
                 if isinstance(l_.get("heat"), dict) and l_["heat"].get("money_corner")]
        covered = {r.get("link_id") for rows in (s.get("buckets") or {}).values()
                   for r in rows or []}
        note = str(s.get("universe_note") or "")
        for m in money:
            if m not in covered and m not in note:
                fail(f"{p.name}: money-corner link '{m}' produced no row and is not named "
                     f"in universe_note — a money corner searched and found empty is a "
                     f"finding worth writing; one silently absent is indistinguishable "
                     f"from one never searched")
        report(f"{p.name}: {len(covered - {None})} link(s) attributed, "
               f"{len(money)} money-corner link(s) on the chain")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--root")
    args = ap.parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    today = args.date or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    data = root / "data"

    screens = []
    for p in sorted((data / "screens").glob("*.json")):
        if p.name.startswith("_"):
            continue
        s = read_json(p)
        if isinstance(s, dict):
            screens.append((p, s))
        else:
            fail(f"{p.name}: unreadable JSON")

    report(f"check_screen: {today}")
    report(f"  screens on disk: {len(screens)}")
    check_quotes(data, screens)
    check_pending_backed(data, screens)
    check_cik_agreement(data, screens)
    check_run_day(data, screens, data / "chains", today)

    print("\n".join(lines))
    if failures:
        print("\nFAILURES:")
        for f_ in failures:
            print(f"  - {f_}")
        print(f"\ncheck_screen: {len(failures)} failure(s)")
        return 1
    print("\ncheck_screen: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
