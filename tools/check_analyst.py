#!/usr/bin/env python3
"""Analyst postlude gate. Stdlib only, offline.

`CLAUDE.md` states what `run deepdive` and `run redteam` must verify before they commit.
That column was prose, so it could only ever be honoured by memory. This is the same list
as an exit code. Sibling of `tools/check_radar.py`; same contract, same shape.

Checks, each reported with the denominator it examined:
  1. every dive touched today: verdict completeness per method section 7
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

Scope: checks 1-10 only bind on a day that actually wrote a dive. On a day with no dive
the gate reports NOT RUN TODAY and exits 0, rather than reporting a clean pass over
nothing. Check 11 always runs: it is a code-level invariant, not a run artifact.

Run: python3 tools/check_analyst.py [--date YYYY-MM-DD] [--root PATH]
Exit 0 clean, 1 on any failure.
"""
import datetime
import json
import re
import sys
from pathlib import Path

CLOCK_MAX_DAYS = {"COMPOUNDER": 90, "EVENT": 21}
GAP_ROWS = {"revenue_cagr_5y", "operating_margin", "reinvestment_return",
            "terminal", "net_gap_direction"}
GRADES = {"A", "B", "C", "D", None}
TAGS = ("VERIFIED", "INFERRED", "SPECULATIVE", "NULL")

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


def main() -> int:
    argv = sys.argv[1:]
    root = Path(argv[argv.index("--root") + 1]).resolve() if "--root" in argv \
        else Path(__file__).resolve().parent.parent
    today = argv[argv.index("--date") + 1] if "--date" in argv \
        else datetime.date.today().isoformat()
    data = root / "data"

    check_schema_drift(root)

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

    if not touched:
        report(f"NOT RUN TODAY ({today}): 0 of {len(dives)} dive(s) updated. "
               "Nothing to gate; schema check above still applied.")
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
        mkt = read_json(data / "market" / f"{str(ticker).replace('.', '-')}.json") \
            if ticker else None
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

    # 10. the ledger line
    ledger = (data / "ledger.md").read_text() if (data / "ledger.md").exists() else ""
    todays = [ln for ln in ledger.splitlines() if ln.startswith(today)]
    dive_lines = [ln for ln in todays if "deepdive" in ln or "redteam" in ln]
    if not dive_lines:
        fail(f"no ledger line dated {today} naming deepdive or redteam")
    else:
        last = dive_lines[-1]
        for token, what in (("verdict:", "the verdict"), ("clock:", "the clock"),
                            ("grade:", "the earnings grade")):
            if token not in last:
                fail(f"ledger line must name {what} as `{token}<value>`: {last[:120]}")

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
