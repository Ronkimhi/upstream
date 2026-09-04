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

from check_map import mapping_fingerprint
from market_paths import safe_name  # one filename rule, tested against fetch.py

failures: list[str] = []
lines: list[str] = []
CAMPAIGN_SCREEN_CUTOFF = "2026-08-31"
# These are the only screen files committed before campaign-v1 identity existed.  The
# allowlist is intentionally path-based: a writer cannot grandfather a new file merely by
# backdating its writable as_of field.
LEGACY_SCREEN_ALLOWLIST = frozenset({
    "ai-infrastructure.json",
    "ai-infrastructure__S2.json",
    "tibet-mega-dam.json",
})


def fail(msg: str) -> None:
    failures.append(msg)


def report(msg: str) -> None:
    lines.append(msg)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


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


def _screen_rows(screen: dict):
    for rows in (screen.get("buckets") or {}).values():
        for row in rows or []:
            if isinstance(row, dict):
                yield row


def _current_audit(mapping: dict) -> bool:
    """A screen may use a whole mapping only after its current semantic audit passed."""
    audit = mapping.get("audit")
    return (
        mapping.get("status") == "COMPLETE"
        and isinstance(audit, dict)
        and audit.get("status") == "PASS"
        and audit.get("mapping_fingerprint") == mapping_fingerprint(mapping)
    )


def _placement_admissible(placement: dict | None) -> bool:
    """A single placement a screen row may consume from an ACTIVE, un-audited mapping.

    Ron's decision, 2026-09-01. Until then a screen needed the whole census audited in
    fresh context first, so hormuz-maritime (84 listings) sat behind a stage cloud sessions
    could not run and five fires in a row stood down on it. The lazy funnel says analyse
    what the command asks for: a screen row consumes ONE placement, so what it needs is
    that placement qualified with every evidence item VERIFIED. The row declares
    `audit_scope: "PLACEMENT"` so the narrower basis is visible on the row itself, and the
    fresh-context audit moves to the placement being dived (check_analyst.py).
    """
    if not _is_qualified_placement(placement):
        return False
    evidence = placement.get("evidence") or []
    return all(isinstance(item, dict) and item.get("tag") == "VERIFIED" for item in evidence)


def _scenario_moved_links(chain: dict, scenario_id) -> set | None:
    for scenario in chain.get("scenarios") or []:
        if isinstance(scenario, dict) and scenario.get("id") == scenario_id:
            links = set()
            for moved in scenario.get("links_moved") or []:
                if isinstance(moved, dict) and moved.get("link_id"):
                    links.add(moved["link_id"])
                elif isinstance(moved, str):
                    links.add(moved)
            return links
    return None


def _screen_dates(screen: dict) -> set[str]:
    return {
        str(screen.get(field) or "")[:10]
        for field in ("as_of", "created_at")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(screen.get(field) or "")[:10])
    }


def _is_qualified_placement(placement: dict | None) -> bool:
    """A campaign screen may consume only a current, explicit ACTIVE placement."""
    return isinstance(placement, dict) and placement.get("status") == "ACTIVE" and \
        placement.get("active") is not False and placement.get("is_active") is not False and \
        bool(placement.get("evidence") or [])


def _campaign_strict(screen: dict, mapping_exists: bool) -> bool:
    if screen.get("identity_schema") == "campaign-v1":
        return True
    rows = list(_screen_rows(screen))
    normalized_refs = {
        "issuer_id", "listing_id", "mapping_ref", "profile_ref", "campaign_ref",
    }
    if any(any(key in row for key in normalized_refs) for row in rows):
        return True
    return mapping_exists and any(day >= CAMPAIGN_SCREEN_CUTOFF
                                  for day in _screen_dates(screen))


def _allowlisted_legacy(path: Path, screen: dict) -> bool:
    return path.name in LEGACY_SCREEN_ALLOWLIST and \
        all(day < CAMPAIGN_SCREEN_CUTOFF for day in _screen_dates(screen))


def check_campaign_identity(data: Path, screens: list) -> None:
    """Hold the exact mapping -> COMPLETE O1/O2 profile -> screen boundary.

    Strictness is objective: campaign-v1 fields, or a post-cutoff screen over a normalized
    mapping, activate it. Older data is readable only from the committed legacy allowlist,
    never because a writer supplied an old date. Campaign rows must be fully attributable
    before selection can promote their profile to O1.

    O1 is ACCEPTED here, and that is not a loosening. This function re-reads every screen on
    disk on every invocation, while `run selection` promotes a screened profile from O2 to O1.
    Demanding O2 exactly therefore broke every earlier screen the moment the first selection
    landed, which made screening and selecting mutually exclusive and the campaign funnel
    unrunnable (2026-08-30). O1 is a strictly LATER state of a profile that was O2 when it was
    screened, so it is a valid history; O3, DRAFT and BLOCKED are still refused, which is the
    rule that was actually doing work.
    """
    campaign_rows = legacy_screens = checked = placement_scoped = 0
    required = {
        "issuer_id", "listing_id", "ticker", "market_ticker", "chain_id", "link_id",
        "mapping_ref", "profile_ref", "data_tier",
    }
    for path, screen in screens:
        chain_id = screen.get("chain_id")
        mapping_path = data / "mappings" / f"{chain_id}.json"
        mapping = read_json(mapping_path)
        strict = _campaign_strict(screen, isinstance(mapping, dict))
        if not strict and _allowlisted_legacy(path, screen):
            legacy_screens += 1
            report(f"WARNING {path.name}: pre-campaign legacy screen has no campaign-v1 "
                   "identity schema; readable, but not silently exempt from identity controls")
            continue
        if not strict:
            fail(f"{path.name}: screen is not in the committed pre-{CAMPAIGN_SCREEN_CUTOFF} "
                 "legacy allowlist; identity requirements cannot be bypassed by backdating")
            continue
        if screen.get("identity_schema") != "campaign-v1":
            fail(f"{path.name}: campaign-era screen lacks identity_schema 'campaign-v1'")
        if not isinstance(mapping, dict):
            fail(f"{path.name}: campaign-v1 screen has no normalized mapping "
                 f"{mapping_path.name}")
            continue
        mapping_audited = _current_audit(mapping)
        listings = {
            item.get("listing_id"): item for item in mapping.get("listings") or []
            if isinstance(item, dict) and item.get("listing_id")
        }
        placements = {
            (item.get("chain_id"), item.get("link_id"), item.get("issuer_id")): item
            for item in mapping.get("placements") or [] if isinstance(item, dict)
        }
        scenario_id = screen.get("scenario_id")
        moved_links = None
        if scenario_id is not None:
            chain = read_json(data / "chains" / f"{chain_id}.json")
            moved_links = _scenario_moved_links(chain, scenario_id) \
                if isinstance(chain, dict) else None
            if moved_links is None:
                fail(f"{path.name}: scenario_id {scenario_id!r} does not resolve to a "
                     f"scenario with moved links on chain {chain_id!r}")
        seen_issuers = set()
        for row in _screen_rows(screen):
            campaign_rows += 1
            checked += 1
            where = f"{path.name}:{row.get('ticker') or row.get('market_ticker') or '?'}"
            missing = sorted(key for key in required if not str(row.get(key) or "").strip())
            if missing:
                fail(f"{where}: campaign screen row missing required identity fields: "
                     + ", ".join(missing))
                continue
            if row.get("chain_id") != chain_id:
                fail(f"{where}: row chain_id does not match screen chain_id {chain_id!r}")
            if row.get("mapping_ref") != f"data/mappings/{chain_id}.json":
                fail(f"{where}: mapping_ref must be data/mappings/{chain_id}.json")
            issuer_id, listing_id, link_id = (
                row["issuer_id"], row["listing_id"], row["link_id"])
            listing = listings.get(listing_id)
            if not listing or listing.get("issuer_id") != issuer_id:
                fail(f"{where}: listing_id {listing_id!r} does not resolve to issuer_id "
                     f"{issuer_id!r} in the qualified mapping")
            elif (listing.get("market_ticker") or listing.get("ticker")) != row.get("market_ticker") or \
                    listing.get("ticker") != row.get("ticker"):
                # A foreign listing carries the exchange's own ticker (BPCL) and the
                # vendor plane's suffixed one (BPCL.NS) as two fields; comparing the row's
                # market_ticker against the bare ticker refused every such row (found
                # 2026-09-04 on russian-diesel-ban, where three admissible names were
                # dropped rather than written with a market_ticker no file resolves).
                fail(f"{where}: ticker must match the mapped listing's ticker and "
                     "market_ticker its market_ticker (or ticker when unset)")
            placement = placements.get((chain_id, link_id, issuer_id))
            if not _is_qualified_placement(placement):
                fail(f"{where}: link_id {link_id!r} has no current qualified mapping "
                     f"placement for issuer_id {issuer_id!r}; placement.status must be "
                     "exactly ACTIVE")
            elif not mapping_audited:
                if row.get("audit_scope") != "PLACEMENT":
                    fail(f"{path.name}: campaign mapping {mapping_path.name} lacks a current "
                         "PASS audit and COMPLETE status; a row on an un-audited census must "
                         "declare audit_scope: \"PLACEMENT\" and rest on a placement whose "
                         "evidence is all VERIFIED (Ron, 2026-09-01), otherwise screen rows "
                         "cannot use a stale or failed census")
                elif not _placement_admissible(placement):
                    fail(f"{where}: audit_scope PLACEMENT on an un-audited mapping requires "
                         "every evidence item on the placement to be VERIFIED; INFERRED "
                         "issuer-role evidence needs the census audit")
                else:
                    placement_scoped += 1
            if moved_links is not None and link_id not in moved_links:
                fail(f"{where}: scenario screen row link_id {link_id!r} is not moved by "
                     f"scenario {scenario_id!r}")
            expected_profile_ref = f"data/companies/{issuer_id}.json"
            if row.get("profile_ref") != expected_profile_ref:
                fail(f"{where}: profile_ref must be {expected_profile_ref}")
            profile = read_json(data / "companies" / f"{issuer_id}.json")
            if not isinstance(profile, dict) or profile.get("issuer_id") != issuer_id:
                fail(f"{where}: profile_ref does not resolve to issuer_id {issuer_id!r}")
            elif profile.get("status") != "COMPLETE" or \
                    profile.get("opportunity_tier") not in {"O1", "O2"}:
                fail(f"{where}: campaign screen requires a COMPLETE O1 or O2 profile; O3, "
                     "DRAFT, and BLOCKED profiles are not screen inputs. O1 is only ever a "
                     "later promotion by `run selection` of a profile that was O2 when it "
                     "was screened, never an eligibility a writer may claim at screen time")
            if row.get("data_tier") not in {"T1", "T2", "T3"}:
                fail(f"{where}: data_tier must be T1, T2, or T3")
            elif isinstance(profile, dict) and row["data_tier"] != profile.get("data_tier"):
                fail(f"{where}: data_tier {row['data_tier']!r} does not match profile "
                     f"data_tier {profile.get('data_tier')!r}")
            if "opportunity_tier" in row:
                fail(f"{where}: opportunity_tier belongs on the profile, not the screen row")
            if issuer_id in seen_issuers and not str(row.get("secondary_link_basis") or "").strip():
                fail(f"{where}: duplicate issuer row needs a non-empty secondary_link_basis")
            seen_issuers.add(issuer_id)
    report(f"campaign identity: {checked}/{campaign_rows} campaign row(s) examined; "
           f"{placement_scoped} row(s) admitted on placement scope over an un-audited "
           f"mapping; {legacy_screens} legacy screen(s) warned")


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
    check_campaign_identity(data, screens)
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
