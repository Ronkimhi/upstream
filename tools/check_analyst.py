#!/usr/bin/env python3
"""Analyst postlude gate. Stdlib only, offline.

`CLAUDE.md` states what `run deepdive` and `run redteam` must verify before they commit.
That column was prose, so it could only ever be honoured by memory. This is the same list
as an exit code. Sibling of `tools/check_radar.py`; same contract, same shape.

Checks, each reported with the denominator it examined:
  0. every campaign-era DRAFT or FINAL resolves through one exact COMPLETE O1 profile,
     a COMPLETE mapping with a current PASS audit that passes check_map audit validation,
     qualified mapping listing and placement, and normalized screen handoff
  1. every dive touched today: verdict completeness per method section 7, its chain-link
     attribution, the price it reasoned from, and every filing passage it quotes verified
     verbatim against data/edgar/docs/<T>.json with the screen gate's own normalizer
  2. exactly 3 bull and 3 bear bullets
  3. review_by inside the clock (COMPOUNDER <= 90d, EVENT <= 21d from updated_at)
  4. the expectations gap table has its five required rows, and the market-implied column
     is sourced from data/market/<T>.json.quality, never authored in-session
  5. the earnings-quality grade exists, and its veto is actually applied
     (C caps at WATCH; D forbids INVESTABLE; NULL caps at WATCH)
  6. the independence test is answered, or the verdict caps at WATCH
  7. status FINAL implies a complete red_team block
  8. every TOO_LATE carries a shadow row that really exists
  9. confidence_audit matches the tags actually present in the file
 10. today's ledger line names the verdict, the clock and the earnings grade
 11. schema drift guard: fetch.QUALITY_FIELDS and acis.quality's series_fields agree

Scope: admission check 0 and schema-drift check 11 always run. Checks 1-10 only bind on a
day that actually wrote a dive. On a day with no dive the gate reports NOT RUN TODAY
rather than reporting a clean pass over run-specific checks it did not perform.

Run: python3 tools/check_analyst.py [--date YYYY-MM-DD] [--root PATH]
Exit 0 clean, 1 on any failure.
"""
import datetime
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import check_map
import check_profile
import market_paths


def _load_normalizer():
    """check_screen.normalize, imported rather than copied.

    Two gates that both claim to check a quote "verbatim" must agree on what that means.
    A second copy of this function would drift, and the drift would show up as a quote
    that one gate accepts and the other calls fabricated."""
    path = Path(__file__).resolve().parent / "check_screen.py"
    spec = importlib.util.spec_from_file_location("_check_screen", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.normalize


_normalize = _load_normalizer()

CLOCK_MAX_DAYS = {"COMPOUNDER": 90, "EVENT": 21}
GAP_ROWS = {"revenue_cagr_5y", "operating_margin", "reinvestment_return",
            "terminal", "net_gap_direction"}
GRADES = {"A", "B", "C", "D", None}
# A verdict written today off a series older than this is reasoning from a stale price.
# Five trading days, expressed in calendar days so a long weekend does not trip it.
STALE_SERIES_DAYS = 7
TAGS = ("VERIFIED", "INFERRED", "SPECULATIVE", "NULL")
# Campaign identity became a Stocky admission requirement on this date.
STOCKY_ADMISSION_GATE = "2026-08-30"
STOCKY_IDENTITY_FIELDS = (
    "issuer_id", "listing_id", "chain_id", "link_id", "screen_ref",
)
# The sole pre-campaign Stocky document committed in HEAD when campaign admission landed.
# Eligibility is the exact repo-relative path plus the committed byte content, never mutable
# lifecycle metadata. This intentionally rejects copies, renames, ticker/chain changes, and
# new files backdated through created_at, updated_at, or as_of.
STOCKY_LEGACY_BASELINE = {
    "data/stocks/VRT__ai-infrastructure.json":
        "98b1f6f3fba93fa7bbbde38ae5db761409471bfa758df3b37d51ae80d4e4d9cf",
}

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


def is_committed_legacy_stock(root: Path, path: Path) -> bool:
    """True only for an exact path-and-content match to the pre-gate HEAD baseline."""
    try:
        relative_path = path.relative_to(root).as_posix()
        content = path.read_bytes()
    except (OSError, ValueError):
        return False
    expected = STOCKY_LEGACY_BASELINE.get(relative_path)
    return expected == hashlib.sha256(content).hexdigest()


def _screen_for_ref(root: Path, screen_ref: str) -> dict | None:
    """Resolve the same explicit screen identities accepted by the profile handoff."""
    folder = root / "data" / "screens"
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        screen = read_json(path)
        if not isinstance(screen, dict):
            continue
        identities = {
            screen.get("id"), path.name, path.stem, str(path.relative_to(root)),
        }
        if screen_ref in identities:
            return screen
    return None


def _screen_rows(screen: dict):
    buckets = screen.get("buckets")
    if not isinstance(buckets, dict):
        return
    for rows in buckets.values():
        for row in rows or []:
            if isinstance(row, dict):
                yield row


def _qualified_placement_failures(placement: dict) -> list[str]:
    """The exact map row must carry the role evidence that qualified it."""
    findings = []
    if placement.get("status", "ACTIVE") != "ACTIVE":
        findings.append(
            f"mapping placement status must be ACTIVE, found {placement.get('status')!r}")
    if not str(placement.get("role") or "").strip():
        findings.append("qualified mapping placement has no role")
    evidence = placement.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        findings.append("qualified mapping placement has no evidence")
        return findings
    for i, item in enumerate(evidence):
        where = f"qualified mapping placement evidence[{i}]"
        if not isinstance(item, dict):
            findings.append(f"{where} is not an object")
            continue
        if not str(item.get("claim") or "").strip():
            findings.append(f"{where} has no claim")
        if item.get("tag") not in check_map.EVIDENCE_TAGS:
            findings.append(f"{where} has invalid tag {item.get('tag')!r}")
        if not str(item.get("source_name") or item.get("source") or "").strip():
            findings.append(f"{where} has no source_name")
        if not check_map.valid_date(item.get("source_date")):
            findings.append(f"{where} has invalid source_date")
        url = item.get("url") or item.get("source_url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            findings.append(f"{where} has no fetchable http(s) url")
    return findings


def _mapping_admission_failures(mapping: dict, chain_id=None, link_id=None,
                                issuer_id=None) -> list[str]:
    """Campaign-era Stocky requires a fresh-context audit behind the placement it dives.

    Two proofs satisfy it. The whole census: mapping COMPLETE with a valid current PASS
    audit, as before. Or, since Ron's decision of 2026-09-01, the one placement: a current
    PASS entry in `placement_audits[]` for this exact (chain, link, issuer), bound by
    check_map.placement_claim_digest to the placement's present role and sampled evidence.
    The audit stays where money is decided; it no longer has to cover 84 listings first.
    """
    findings = []
    placement_audit = check_map.placement_audit_for(
        mapping, chain_id, link_id, issuer_id) if chain_id and link_id and issuer_id else None
    if placement_audit is not None:
        for item in check_map.placement_audit_failures(mapping):
            findings.append(f"placement audit not admission-valid: {item}")
        return findings
    if mapping.get("status") != "COMPLETE":
        findings.append(
            "Stocky admission requires mapping status COMPLETE, "
            f"found {mapping.get('status')!r}, or a current PASS placement_audits entry "
            f"for {(chain_id, link_id, issuer_id)!r}")
    for item in check_map.audit_failures(mapping):
        findings.append(f"mapping audit not admission-valid: {item}")
    return findings


def stock_admission_failures(root: Path, stock: dict) -> list[str]:
    """Prove a campaign-era Stocky file came from one exact qualified O1 handoff.

    Identity never falls back to ticker. The dive, O1 profile, mapping listing and
    placement, and referenced screen row must all resolve to the same issuer/listing/link.
    """
    findings = []
    identity = {}
    for field in STOCKY_IDENTITY_FIELDS:
        value = stock.get(field)
        if not isinstance(value, str) or not value.strip():
            findings.append(f"campaign-era dive requires non-empty {field}")
        else:
            identity[field] = value
    ticker = stock.get("ticker")
    if not isinstance(ticker, str) or not ticker.strip():
        findings.append("campaign-era dive requires a non-empty canonical ticker")
    if findings:
        return findings

    issuer_id = identity["issuer_id"]
    listing_id = identity["listing_id"]
    chain_id = identity["chain_id"]
    link_id = identity["link_id"]
    screen_ref = identity["screen_ref"]
    if not check_map.ISSUER_ID_RE.fullmatch(issuer_id):
        findings.append(f"issuer_id {issuer_id!r} is not a stable safe identifier")
        return findings

    profile_path = root / "data" / "companies" / f"{issuer_id}.json"
    profile = read_json(profile_path)
    if not isinstance(profile, dict):
        findings.append(
            f"issuer_id {issuer_id!r} has no exact profile at "
            f"data/companies/{issuer_id}.json")
        return findings
    if profile.get("status") != "COMPLETE":
        findings.append(
            f"issuer profile status must be COMPLETE, found {profile.get('status')!r}")
    if profile.get("opportunity_tier") != "O1":
        findings.append(
            "Stocky admission requires opportunity_tier O1, found "
            f"{profile.get('opportunity_tier')!r}")
    for profile_finding in check_profile.validate_profile(root, profile_path, profile):
        findings.append(f"O1 profile is not admission-valid: {profile_finding}")

    selection = profile.get("selection_basis")
    handoff = selection.get("screen_handoff") if isinstance(selection, dict) else None
    if not isinstance(handoff, dict):
        findings.append(
            "O1 profile needs structured selection_basis.screen_handoff")
        return findings
    for dimension in sorted(check_profile.SELECTION_DIMENSIONS):
        value = selection.get(dimension)
        if not isinstance(value, dict) or not value:
            findings.append(
                f"O1 selection_basis.{dimension} must be a non-empty object")

    expected = {
        "issuer_id": profile.get("issuer_id"),
        "listing_id": handoff.get("listing_id"),
        "chain_id": handoff.get("chain_id"),
        "link_id": handoff.get("link_id"),
        "screen_ref": handoff.get("screen_ref"),
    }
    for field in STOCKY_IDENTITY_FIELDS:
        if identity[field] != expected[field]:
            findings.append(
                f"dive {field} {identity[field]!r} does not exactly match "
                f"O1 screen handoff {expected[field]!r}")

    if listing_id not in (profile.get("listing_refs") or []):
        findings.append(
            f"listing_id {listing_id!r} is not one of the O1 profile's listing_refs")
    exact_profile_placement = any(
        isinstance(placement, dict)
        and placement.get("chain_id") == chain_id
        and placement.get("link_id") == link_id
        for placement in profile.get("placements") or []
    )
    if not exact_profile_placement:
        findings.append(
            f"O1 profile has no exact placement for {(chain_id, link_id)!r}")

    mapping_path = root / "data" / "mappings" / f"{chain_id}.json"
    mapping = read_json(mapping_path)
    if not isinstance(mapping, dict):
        findings.append(
            f"chain_id {chain_id!r} has no exact mapping at "
            f"data/mappings/{chain_id}.json")
        return findings
    if mapping.get("chain_id") != chain_id:
        findings.append(
            f"mapping chain_id {mapping.get('chain_id')!r} does not match {chain_id!r}")
    findings.extend(_mapping_admission_failures(mapping, chain_id, link_id, issuer_id))

    listings = [
        row for row in mapping.get("listings") or []
        if isinstance(row, dict) and row.get("listing_id") == listing_id
    ]
    if len(listings) != 1:
        findings.append(
            f"listing_id {listing_id!r} resolves to {len(listings)} mapping listings, "
            "expected exactly 1")
    else:
        listing = listings[0]
        if listing.get("issuer_id") != issuer_id:
            findings.append(
                f"mapping listing {listing_id!r} belongs to "
                f"{listing.get('issuer_id')!r}, not {issuer_id!r}")
        if listing.get("ticker") != ticker:
            findings.append(
                f"dive ticker {ticker!r} does not exactly match mapping listing "
                f"{listing_id!r} ticker {listing.get('ticker')!r}")

    placements = [
        row for row in mapping.get("placements") or []
        if isinstance(row, dict)
        and row.get("chain_id") == chain_id
        and row.get("link_id") == link_id
        and row.get("issuer_id") == issuer_id
    ]
    if len(placements) != 1:
        findings.append(
            "exact (chain_id, link_id, issuer_id) resolves to "
            f"{len(placements)} mapping placements, expected exactly 1")
    else:
        findings.extend(_qualified_placement_failures(placements[0]))

    screen = _screen_for_ref(root, screen_ref)
    if not isinstance(screen, dict):
        findings.append(
            f"screen_ref {screen_ref!r} does not resolve in data/screens/")
        return findings
    if screen.get("chain_id") != chain_id:
        findings.append(
            f"referenced screen chain_id {screen.get('chain_id')!r} does not match "
            f"{chain_id!r}")
    exact_rows = [
        row for row in _screen_rows(screen)
        if row.get("issuer_id") == issuer_id
        and row.get("listing_id") == listing_id
        and row.get("ticker") == ticker
        and row.get("link_id") == link_id
    ]
    if not exact_rows:
        findings.append(
            f"screen_ref {screen_ref!r} has no exact row for issuer_id "
            f"{issuer_id!r}, listing_id {listing_id!r}, ticker {ticker!r}, "
            f"link_id {link_id!r}")
    return findings


def days_between(a: str, b: str):
    try:
        return (datetime.date.fromisoformat(b[:10]) - datetime.date.fromisoformat(a[:10])).days
    except (ValueError, TypeError):
        return None


def check_schema_drift(root: Path) -> None:
    """The one check that binds on every run: the fetch plane and the scoring module
    must name the same fields. When they drifted (2026-08-29, `long_term_debt`), every
    Piotroski and Beneish score silently degraded to PENDING_DATA with all inputs
    apparently present. Cheap to check, invisible to catch any other way."""
    fetch_src = (root / "tools" / "fetch" / "fetch.py").read_text()
    qual_src = (root / "tools" / "acis" / "quality.py").read_text()
    m = re.search(r"QUALITY_FIELDS = \(([^)]*)\)", fetch_src, re.S)
    q = re.search(r"series_fields = \(([^)]*)\)", qual_src, re.S)
    if not m or not q:
        fail("schema drift: could not read QUALITY_FIELDS or series_fields")
        return
    produced = {f"{s.strip().strip(chr(34))}_fy" for s in m.group(1).split(",") if s.strip()}
    consumed = {s.strip().strip(chr(34)) for s in q.group(1).split(",") if s.strip()}
    missing = consumed - produced
    unused = produced - consumed
    if missing:
        fail(f"schema drift: acis.quality reads {sorted(missing)} which fetch.py never writes "
             "— every score needing them silently reports PENDING_DATA")
    if unused:
        fail(f"schema drift: fetch.py writes {sorted(unused)} which acis.quality never reads")
    report(f"schema: {len(consumed)} consumed / {len(produced)} produced fields agree"
           if not (missing or unused) else "schema: DRIFTED")


def collect_quotes(obj) -> list:
    """Every non-empty `quote` string anywhere in the dive, at any depth.

    A dive quotes filings in places a screen row never does — filing_evidence, the
    earnings-quality basis, a bull or bear bullet, a red-team challenge — so the walk is
    over the whole object rather than one known array. Recursing on the value of a `quote`
    key is pointless (it is a string), which is why the else branch skips it.
    """
    found: list = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "quote" and isinstance(v, str) and v.strip():
                found.append(v)
            else:
                found.extend(collect_quotes(v))
    elif isinstance(obj, list):
        for x in obj:
            found.extend(collect_quotes(x))
    return found


def check_dive_quotes(data: Path, n: str, ticker, d) -> None:
    """VERBATIM QUOTES IN THE DIVE (2026-08-29, raised by the first real dive).

    check_screen.py verifies quotes on SCREEN rows. A dive quotes filings too, and no gate
    touched them, so the deepest document in the funnel was the least checked. Same
    normalizer as the screen gate, imported rather than reimplemented so the two can never
    drift into disagreeing about what "verbatim" means.

    Fails CLOSED, exactly as method section 1 demands of the screen: a dive that quotes a
    filing with no document on disk is a failure, not a skip. A dive that quotes nothing
    is not asked for a document, because there is nothing to check against it.
    """
    quotes = collect_quotes(d)
    if not quotes:
        return
    doc = read_json(data / "edgar" / "docs" / f"{str(ticker).replace('.', '-')}.json")
    if not isinstance(doc, dict) or not isinstance(doc.get("text"), str):
        fail(f"{n}: quotes {len(quotes)} filing passage(s) but there is no document at "
             f"data/edgar/docs/{ticker}.json — fail closed (method section 1)")
        return
    body = _normalize(doc["text"])
    missed = [q for q in quotes if _normalize(q) not in body]
    for q in missed:
        fail(f"{n}: quoted passage does NOT appear in {doc.get('form')} "
             f"{doc.get('accession')} on disk: {q.strip()[:90]!r}")
    report(f"{n}: {len(quotes) - len(missed)}/{len(quotes)} quoted passage(s) "
           f"verified verbatim against data/edgar/docs/{ticker}.json")


def check_price_source_note(n: str, ticker, pstatus, d) -> None:
    """The price plane's disclosure, in both directions.

    REQUIRED when the market file is SINGLE_SOURCE or DISPUTED: a dive whose every level
    is a function of one unconfirmed print must say so on its own page. That half shipped
    2026-08-29 and was satisfied by a field the renderer ignored, which is why
    tools/check_render.py now insists every gate-named dive field has a template path.

    ACCURATE whenever it exists, which is the half the first half created. The moment
    price_source_note started rendering (2026-08-30) a stale one stopped being a dead
    field and became a false statement carrying the authority of a disclosure — and it
    went stale the same hour, when the dual-source leg landed and VRT moved SINGLE_SOURCE
    to AGREED while the dive still said one unconfirmed print. So a note must name the
    status the market file holds NOW. Naming the old status as well is fine and often
    right: what is checked is that the current one is present, not that the history is
    absent.
    """
    if d.get("fixture"):
        return
    note = str(d.get("price_source_note") or "").strip()
    if pstatus in {"SINGLE_SOURCE", "DISPUTED"} and not note:
        fail(f"{n}: data/market/{ticker}.json is {pstatus} (the price is not confirmed by a "
             f"second source), so the dive needs a price_source_note saying the levels rest "
             f"on it. See docs/method.md section 1 on the price plane")
        return
    if note and pstatus and pstatus not in note:
        fail(f"{n}: price_source_note does not name {pstatus}, which is what "
             f"data/market/{ticker}.json says today — the note has outlived the condition "
             f"it describes and now renders on the stock page as a disclosure of something "
             f"that is no longer true. Amend it, or drop it if the condition is gone")


def main() -> int:
    argv = sys.argv[1:]
    root = Path(argv[argv.index("--root") + 1]).resolve() if "--root" in argv \
        else Path(__file__).resolve().parent.parent
    today = argv[argv.index("--date") + 1] if "--date" in argv \
        else datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    data = root / "data"

    # The schema-drift guard is an invariant of THIS checkout's code, not of whatever
    # data tree --root points at, so it reads the code beside this script. Before this
    # split, pointing --root at a probe tree crashed on a missing tools/fetch/fetch.py
    # and the data checks below never ran.
    check_schema_drift(Path(__file__).resolve().parent.parent)

    dives, touched = [], []
    for p in sorted((data / "stocks").glob("*.json")):
        if p.name.startswith("_"):
            continue
        d = read_json(p)
        if not isinstance(d, dict) or d.get("fixture"):
            continue
        dives.append((p, d))
        if str(d.get("updated_at", ""))[:10] == today:
            touched.append((p, d))

    # Admission is a corpus invariant, not a campaign-completion calculation and not a
    # FINAL-only check. A bad DRAFT must stop here, before red-team work can bless it.
    admission_checked = legacy = 0
    for p, d in dives:
        if is_committed_legacy_stock(root, p):
            legacy += 1
            report(
                f"WARN {p.name}: exact pre-{STOCKY_ADMISSION_GATE} committed legacy "
                "baseline match; O1 issuer/listing/screen admission is not grandfathered "
                "silently. Amend it onto the campaign schema before relying on it for "
                "campaign coverage.")
            continue
        admission_checked += 1
        for finding in stock_admission_failures(root, d):
            fail(f"{p.name}: {finding}")
    report(
        f"admission: {admission_checked} campaign-era dive(s) checked; "
        f"{legacy} pre-{STOCKY_ADMISSION_GATE} legacy warning(s)")

    if not touched:
        report(f"NOT RUN TODAY ({today}): 0 of {len(dives)} dive(s) updated. "
               "No run-specific checks applied; schema and admission checks still did.")
        print("\n".join(lines))
        if failures:
            print("\nFAILURES:")
            for f_ in failures:
                print(f"  - {f_}")
            return 1
        return 0

    shadow_ids = {r.get("id") for r in
                  (read_json(data / "shadow" / "book.json", {"rows": []}) or {}).get("rows", [])}

    for p, d in touched:
        n = p.name
        verdict = d.get("verdict")
        clock = d.get("clock")
        ticker = d.get("ticker")
        mkt = read_json(market_paths.market_path(data, ticker)) if ticker else None
        quality = mkt.get("quality") if isinstance(mkt, dict) else None

        # 1b. link_id — method §7 requires every dive to name its chain link, or null with
        # a stated basis (the same discipline a screen row already carries). Without this the
        # gate certified dives that method mandates carry an attribution, and Atlas's link
        # yield — the only measure of whether a map was worth building — silently lost them.
        if "link_id" not in d:
            fail(f"{n}: no link_id — method section 7 requires every dive to name its chain "
                 "link, or null with a stated link_id_basis")
        elif d.get("link_id") is None and not str(d.get("link_id_basis") or "").strip():
            fail(f"{n}: link_id is null but link_id_basis is empty — an unattributed dive "
                 "must say why (method section 7)")

        # 1c. PRICE PROVENANCE (2026-08-29 pressure test). Every level a dive states —
        # the entry zone, no_entry_above, the valuation snapshot — is a function of one
        # number: the price it reasoned from. Nothing checked that number against the
        # series it claims to come from, so a remembered or mistyped price produced a
        # perfectly-formed verdict at the wrong level, and the shape checks in
        # validate.py would all have passed.
        series = (mkt or {}).get("series") if isinstance(mkt, dict) else None
        rows = (series or {}).get("rows") or []
        pr = d.get("price_ref")
        if not isinstance(pr, dict) or pr.get("value") is None:
            fail(f"{n}: no price_ref{{value, source, as_of}} — the dive must say which price "
                 f"it reasoned from (method section 1)")
        elif not rows:
            fail(f"{n}: price_ref cites a price but data/market/{ticker}.json has no series "
                 f"— request prices and re-run rather than reasoning from a remembered number")
        else:
            at = str(pr.get("as_of") or "")
            match = next((r for r in reversed(rows) if str(r[0]) == at), None)
            if match is None:
                prior = [r for r in rows if str(r[0]) <= at]
                match = prior[-1] if prior else None
                if match is None:
                    fail(f"{n}: price_ref.as_of {at!r} is before every row in the series")
            if match is not None:
                close = match[1]
                drift = abs(pr["value"] - close) / close if close else 1.0
                if drift > 0.01:
                    fail(f"{n}: price_ref {pr['value']} is {drift * 100:.1f}% away from the "
                         f"{match[0]} close of {close} in data/market/{ticker}.json — a price "
                         f"the dive states must be the price the data holds")
            # Staleness: hard here, unlike validate.py's warning, because a verdict is
            # being written today off this series.
            age = days_between(str((series or {}).get("as_of") or rows[-1][0]), today)
            if age is not None and age > STALE_SERIES_DAYS:
                fail(f"{n}: the price series is {age}d old (cap {STALE_SERIES_DAYS}d) — "
                     f"bridge a prices refresh before closing a verdict on it")
            # Entry zone sanity against spot: a zone this far from the last close is a
            # typo or a units error, not a view.
            last = rows[-1][1]
            ez = d.get("entry_zone") or {}
            for key in ("low", "high"):
                v = ez.get(key)
                if isinstance(v, (int, float)) and last and not (0.3 * last <= v <= 2.0 * last):
                    fail(f"{n}: entry_zone.{key} {v} is outside 0.3x-2x the last close "
                         f"({last}) — check the number, not the thesis")
        pstatus = (mkt or {}).get("price_status") if isinstance(mkt, dict) else None
        check_price_source_note(n, ticker, pstatus, d)

        # 1d. see check_dive_quotes() — lifted to module level so it can be unit-tested
        # without building a whole dive tree, which is how the screen gate's own quote
        # check earned its five tests.
        check_dive_quotes(data, n, ticker, d)

        # 2. bullet counts
        if len(d.get("bull") or []) != 3 or len(d.get("bear") or []) != 3:
            fail(f"{n}: needs exactly 3 bull and 3 bear bullets, has "
                 f"{len(d.get('bull') or [])}/{len(d.get('bear') or [])}")

        # 3. review_by inside the clock
        cap = CLOCK_MAX_DAYS.get(clock)
        if cap is None:
            fail(f"{n}: clock must be COMPOUNDER or EVENT, got {clock!r}")
        elif d.get("review_by"):
            span = days_between(d.get("updated_at") or today, d["review_by"])
            if span is None:
                fail(f"{n}: review_by {d['review_by']!r} is not a date")
            elif span > cap:
                fail(f"{n}: {clock} review_by is {span}d out, cap is {cap}d")
        else:
            fail(f"{n}: no review_by")

        # 4. the expectations gap table
        gap = d.get("expectations_gap")
        if not isinstance(gap, dict) or not isinstance(gap.get("rows"), list):
            fail(f"{n}: expectations_gap{{rows[]}} missing (method section 7)")
        else:
            have = {r.get("driver") for r in gap["rows"] if isinstance(r, dict)}
            if not GAP_ROWS <= have:
                fail(f"{n}: expectations_gap missing rows {sorted(GAP_ROWS - have)}")
            src = (gap.get("market_implied_source") or "")
            if "data/market/" not in src or "quality" not in src:
                fail(f"{n}: expectations_gap.market_implied_source must point at "
                     f"data/market/<T>.json quality.reverse_dcf, got {src!r} "
                     "— the implied column is never solved in-session")
            else:
                # Cass PROP-20260829-02 finding 5: the citation was checked as a STRING, so
                # a market-implied column fabricated in-session passed green while printing
                # as sourced. Verify the cited file actually holds a solved reverse DCF —
                # not by comparing per-driver rows (revenue CAGR is not the FCF CAGR the
                # solve returns), but by confirming the number the source names EXISTS.
                rd = (quality or {}).get("reverse_dcf") if isinstance(quality, dict) else None
                if not isinstance(quality, dict):
                    fail(f"{n}: market_implied_source cites data/market/{ticker}.json quality "
                         "but that file or its quality block could not be read — an empty "
                         "citation is not a source")
                elif not isinstance(rd, dict) or rd.get("state") != "SOLVED" \
                        or rd.get("implied_fcf_cagr") is None:
                    st = (rd or {}).get("state")
                    fail(f"{n}: market_implied_source cites quality.reverse_dcf but it is "
                         f"{st!r}, not SOLVED — the implied column names a number the file "
                         "does not contain")
            for r in gap["rows"]:
                if not isinstance(r, dict):
                    continue
                pct = r.get("percentile")
                if isinstance(pct, (int, float)) and pct > 80 and not (
                        r.get("structural_reason") and r.get("leading_indicator")):
                    fail(f"{n}: gap row {r.get('driver')!r} sits at the {pct}th percentile "
                         "and needs both a structural_reason and a leading_indicator")

        # 5. the earnings-quality veto
        eq = d.get("earnings_quality")
        grade = eq.get("grade") if isinstance(eq, dict) else "MISSING"
        if not isinstance(eq, dict) or "grade" not in eq:
            fail(f"{n}: earnings_quality{{grade, inputs, basis}} missing (method section 7)")
        elif grade not in GRADES:
            fail(f"{n}: earnings_quality.grade must be A-D or null, got {grade!r}")
        else:
            if grade == "D" and verdict == "INVESTABLE":
                fail(f"{n}: earnings grade D forbids INVESTABLE (method section 7)")
            if grade in ("C", None) and verdict == "INVESTABLE":
                why = "grade C" if grade == "C" else "an uncomputable grade"
                fail(f"{n}: {why} caps the verdict at WATCH, found INVESTABLE")
            if not eq.get("basis"):
                fail(f"{n}: earnings_quality.basis missing — a grade with no stated inputs "
                     "is an opinion")
            # Cass PROP-20260829-02 finding 3: the veto fired on whatever grade was written,
            # but the grade was never checked against the mechanical signal it claims to
            # summarize. Method §7 defines a Beneish breach as grade-C territory, so an A/B
            # over a REVIEW-state Beneish contradicts the grade's own basis.
            ben = quality.get("beneish") if isinstance(quality, dict) else None
            if isinstance(ben, dict) and ben.get("state") == "REVIEW" and grade in ("A", "B"):
                fail(f"{n}: earnings grade {grade} sits over a Beneish breach "
                     f"(M={ben.get('score')} > {ben.get('threshold')}) — method section 7 "
                     "defines a Beneish breach as grade-C territory")

        # 6. the independence test
        it = d.get("independence_test")
        answered = isinstance(it, dict) and all(
            str(it.get(k) or "").strip() for k in
            ("largest_disagreement", "why_the_gap_exists", "falsification"))
        if not answered and verdict == "INVESTABLE":
            fail(f"{n}: independence_test unanswered, which caps the verdict at WATCH; "
                 "found INVESTABLE")
        if not isinstance(it, dict):
            fail(f"{n}: independence_test{{largest_disagreement, why_the_gap_exists, "
                 "falsification} missing")

        # 7. FINAL implies a red team
        if d.get("status") == "FINAL":
            rt = d.get("red_team")
            if not isinstance(rt, dict) or not {"attacked_at", "challenges", "verdict_survived",
                                                "surviving_bear_case"} <= set(rt):
                fail(f"{n}: status FINAL requires a complete red_team block")
            elif not rt.get("pre_mortem"):
                fail(f"{n}: red_team.pre_mortem missing (method section 7)")
        elif d.get("status") != "DRAFT":
            fail(f"{n}: status must be DRAFT or FINAL, got {d.get('status')!r}")

        # 8. TOO_LATE writes a shadow row that exists
        if verdict == "TOO_LATE":
            ref = d.get("shadow_ref")
            if not ref:
                fail(f"{n}: TOO_LATE requires shadow_ref")
            elif ref not in shadow_ids:
                fail(f"{n}: shadow_ref {ref!r} is not a row in data/shadow/book.json")

        # 9. confidence_audit honesty
        ca = d.get("confidence_audit")
        if not isinstance(ca, dict):
            fail(f"{n}: confidence_audit missing")
        else:
            blob = json.dumps({k: v for k, v in d.items() if k != "confidence_audit"})
            for tag in TAGS:
                actual = blob.count(tag)
                claimed = ca.get(tag.lower(), 0)
                if actual and not claimed:
                    fail(f"{n}: confidence_audit claims 0 {tag} but the file uses it "
                         f"{actual} time(s)")

    # 10. the ledger line. Matched on the COMMAND field (split on |, field index 2),
    # never on the whole line: a bare substring test treated any NOTE whose prose
    # mentioned "deepdive" as a dive line, then demanded verdict:/clock:/grade: of it,
    # masking a correct dive line earlier the same day (backlog 2026-08-31,
    # unanchored-ledger-matchers, second failure — promoted per the two-failure rule).
    # Every matching line is checked, not just the last, for the same reason.
    ledger = (data / "ledger.md").read_text() if (data / "ledger.md").exists() else ""
    todays = [ln for ln in ledger.splitlines() if ln.startswith(today)]
    dive_lines = []
    for ln in todays:
        parts = ln.split("|")
        cmd = parts[2].strip() if len(parts) > 2 else ""
        if re.match(r"^(run )?(deepdive|redteam)\b", cmd):
            dive_lines.append(ln)
    if not dive_lines:
        fail(f"no ledger line dated {today} whose command field names deepdive or redteam")
    else:
        for line in dive_lines:
            for token, what in (("verdict:", "the verdict"), ("clock:", "the clock"),
                                ("grade:", "the earnings grade")):
                if token not in line:
                    fail(f"ledger line must name {what} as `{token}<value>`: {line[:120]}")

    report(f"dives: {len(touched)} updated today of {len(dives)} total")
    report(f"shadow book: {len(shadow_ids)} row(s) available for TOO_LATE refs")

    print("\n".join(lines))
    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for f_ in failures:
            print(f"  - {f_}")
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
