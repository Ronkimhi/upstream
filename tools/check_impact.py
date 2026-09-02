#!/usr/bin/env python3
"""Impact appraisal postlude gate. Stdlib only, offline.

`CLAUDE.md` states what a `run impact` run must verify before it commits. That column is
prose, so it can only ever be honoured by memory. This is the same list as an exit code.

Corpus integrity (every invocation, whenever appraisals exist on disk):
  - every evidence item on a scored leg carries a verbatim `source_excerpt`, and every
    number the item's `claim` asserts appears in that excerpt (method section 1)
  - exactly one permanent appraisal per occurrence_id; id must match filename
  - every occurrence reference resolves and anchor_date agrees
  - review_by is exactly as_of + 90 calendar days
  - the venue rule: every ticker-level numeric in the tree resolves to an exact value in
    data/market/<T>.json, or is NULL with a basis and no numeric value
  - rank log queue holds every on-disk appraisal exactly once in deterministic rank_key
    order; each row's impact_score, impact_band and unmappedness match disk

Run-day only (appraisals touched today or an IMPACT ledger line dated today):
  - leg discipline, computed fields, UNRANKED reasons on touched appraisals
  - rank log calibration regenerated today
  - IMPACT ledger line naming ranked: and band:
  - refuse an IMPACT ledger line with no touched appraisal

Run: python3 tools/check_impact.py [--date YYYY-MM-DD] [--root PATH]
Exit 0 clean, 1 on any failure.
"""
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_store  # noqa: E402
import market_paths  # noqa: E402
from impact_score import (  # noqa: E402
    LEGS,
    MONEY_BANDS,
    audit_excerpt,
    compute,
    expected_queue_ids,
    expected_review_by,
    resolve_market_value,
    scan_ticker_venue,
    scores_match,
)

# The source_excerpt bar landed on this date. It is NOT a cutoff: it is enforced on the whole
# corpus, because the defect it closes was live the same day. A cutoff dated tomorrow would
# have exempted the entire 36-file wave that motivated the rule, which is the one outcome
# that makes the gate worthless.
EXCERPT_GATE = "2026-08-30"
# The sole appraisal committed in HEAD when the bar landed, exempted by exact repo-relative
# path plus committed byte content. Same shape and same reason as
# check_analyst.STOCKY_LEGACY_BASELINE: a writer cannot grandfather a new file by backdating
# as_of, and AMENDING this one drops the exemption, so the debt cannot be parked forever. It
# prints a WARNING naming the file on every single run until it is repaired.
EXCERPT_LEGACY_BASELINE = {
    "data/impact/IMP-20260830-01.json":
        "8fcf86c51466113d0b1185a9a4fdc01b959497aa9ab0917b51db09429c788a31",
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


def load_appraisals(data: Path) -> list[dict]:
    apps = []
    folder = data / "impact"
    if folder.exists():
        for f in sorted(folder.glob("*.json")):
            if f.name.startswith("_"):
                continue
            a = read_json(f)
            if isinstance(a, dict):
                a["_file"] = f.name
                apps.append(a)
    return apps


def is_committed_legacy_appraisal(root: Path, path: Path) -> bool:
    """True only for an exact path-and-content match to the pre-gate HEAD baseline."""
    try:
        rel = path.relative_to(root).as_posix()
        content = path.read_bytes()
    except (OSError, ValueError):
        return False
    return EXCERPT_LEGACY_BASELINE.get(rel) == hashlib.sha256(content).hexdigest()


def _leg_is_null(obj: dict) -> bool:
    return obj.get("score", "x") is None or obj.get("band", "x") is None


def check_corpus_excerpts(apps: list[dict], data: Path, root: Path) -> None:
    """method section 1: an evidence item without a verbatim source span is not evidence.

    Why this is a gate and not a paragraph: 36 appraisals were written on 2026-08-30 and
    adversarial verifiers that fetched every cited URL found roughly 85% carrying at least
    one evidence item whose source does not contain the claim. A throughput figure cited to
    an article containing none of its digits. A EUR 4 billion valuation cited to a release
    saying the terms are confidential. 200 GW cited to a page saying 474 GW. Every one had a
    real, relevant URL attached and the page was never opened. Method section 1 already said
    a number exists only if a named, dated, fetchable source states it, and nothing enforced
    it, so it regressed at scale on the first day it was used at scale.
    """
    items = present = numerics = matched = derived = idents = legacy = 0
    unmatched_items = 0
    for a in apps:
        aid = a.get("id", a["_file"])
        path = data / "impact" / a["_file"]
        if is_committed_legacy_appraisal(root, path):
            legacy += 1
            report(f"WARNING {a['_file']}: the one appraisal committed before the "
                   f"{EXCERPT_GATE} source_excerpt bar, exempt by exact committed content. "
                   f"Its evidence carries no verbatim spans and is owed a repair; amending "
                   f"the file drops the exemption")
            continue
        for leg in LEGS:
            obj = a.get(leg)
            if not isinstance(obj, dict) or _leg_is_null(obj):
                continue
            for i, e in enumerate(obj.get("evidence") or []):
                if not isinstance(e, dict) or e.get("tag") == "NULL":
                    continue
                items += 1
                res = audit_excerpt(e)
                present += 1 if res["has_excerpt"] else 0
                numerics += res["numerics"]
                matched += res["matched"]
                derived += res["derived"]
                idents += res["identifiers"]
                if res["unmatched"]:
                    unmatched_items += 1
                for f_ in res["findings"]:
                    fail(f"{aid}: {leg}.evidence[{i}] {f_}")
    report(f"excerpts: {items} evidence item(s) on scored legs examined across "
           f"{len(apps) - legacy} appraisal(s), {present} carry a source_excerpt, "
           f"{numerics} claim numeric(s) cross-checked, {numerics - matched} not found in "
           f"their excerpt across {unmatched_items} item(s), {derived} declared derived, "
           f"{idents} identifier token(s) not numerically checkable, "
           f"{legacy} dated legacy exemption(s)"
           + ("  <- no scored evidence on disk, so the excerpt bar passed over nothing"
              if not items else ""))


# From this date an appraisal written or amended without its web sources in data/web/ fails:
# one dated week for sessions to learn to queue `web_doc` rows (pull-data skill) before
# citing. Before it, an unfetched citation is counted and named, never failed.
WEB_STORE_REQUIRED_FROM = "2026-09-08"


def check_corpus_web_store(apps: list[dict], data: Path) -> None:
    """Every cited web page that the fetch plane has stored must contain its excerpt.

    The excerpt bar above proves the claim's numbers sit in the excerpt; this proves the
    excerpt sits in the page. Together they close the gap adversarial verifiers found on
    2026-08-30. Stored contradictions (MISMATCH, HTTP_403, EMPTY) fail on every appraisal;
    an UNFETCHED source fails only for appraisals dated from WEB_STORE_REQUIRED_FROM.
    """
    items, unfetched_late = [], []
    for a in apps:
        aid = a.get("id", a["_file"])
        late = str(a.get("as_of") or "") >= WEB_STORE_REQUIRED_FROM
        for leg in LEGS:
            obj = a.get(leg)
            if not isinstance(obj, dict) or _leg_is_null(obj):
                continue
            for i, e in enumerate(obj.get("evidence") or []):
                if not isinstance(e, dict) or e.get("tag") == "NULL":
                    continue
                where = f"{aid}: {leg}.evidence[{i}]"
                items.append((where, e))
                if late and evidence_store.verify(data, e)["state"] == "UNFETCHED":
                    unfetched_late.append(where)
    findings, counts = evidence_store.corpus_web_findings(data, items, None)
    for f_ in findings:
        fail(f_)
    for where in unfetched_late:
        fail(f"{where}: cites a web page with no stored fetch in data/web/ (appraisal dated "
             f"on or after {WEB_STORE_REQUIRED_FROM}); queue a web_doc request and cite from "
             "the stored text")
    stored = sum(v for k, v in counts.items() if k not in ("UNFETCHED", "NO_URL"))
    report(f"web store: {len(items)} web citation(s) examined, {stored} with a stored fetch "
           f"({counts.get('MATCH', 0)} match, {counts.get('MISMATCH', 0)} mismatch, "
           f"{sum(v for k, v in counts.items() if k.startswith('HTTP_'))} non-200, "
           f"{counts.get('EMPTY', 0)} empty, {counts.get('NO_EXCERPT', 0)} without excerpt), "
           f"{counts.get('UNFETCHED', 0)} unfetched"
           + ("  <- nothing stored yet: the web bar passed over nothing" if not stored else ""))


def check_corpus_references(apps: list[dict], data: Path) -> None:
    cands = read_json(data / "radar" / "candidates.json", {}) or {}
    cand_ids = {c.get("id") for c in cands.get("candidates", []) if isinstance(c, dict)}
    sig_count = len(list((data / "signals").glob("*.json"))
                   if (data / "signals").exists() else [])
    for a in apps:
        aid = a.get("id", a["_file"])
        oid = a.get("occurrence_id")
        if isinstance(oid, str) and oid.startswith("SIG-"):
            sig = data / "signals" / f"{oid}.json"
            if not sig.exists():
                fail(f"{aid}: occurrence_id {oid} has no signal file. An appraisal of a thesis "
                     f"nobody wrote down is a number with no subject")
                continue
            s = read_json(sig, {}) or {}
            anchor = (s.get("occurrence") or {}).get("anchor_date")
            if anchor and anchor != a.get("anchor_date"):
                fail(f"{aid}: anchor_date {a.get('anchor_date')!r} disagrees with {oid} "
                     f"occurrence.anchor_date {anchor!r}")
        elif isinstance(oid, str) and oid.startswith("CAND-"):
            if oid not in cand_ids:
                fail(f"{aid}: occurrence_id {oid} is not in data/radar/candidates.json")
        else:
            fail(f"{aid}: occurrence_id {oid!r} is neither a SIG- nor a CAND- id")
    report(f"references: {len(apps)} appraisal(s) resolved against {sig_count} signal(s) "
           f"and {len(cand_ids)} candidate(s)")


def check_corpus_venue(apps: list[dict], data: Path) -> None:
    checked_metrics = 0
    for a in apps:
        aid = a.get("id", a["_file"])
        scan = scan_ticker_venue(a)
        for key in scan["undeclared_top_keys"]:
            fail(f"{aid}: undeclared top-level key {key!r}. Ticker-level facts belong in "
                 f"ticker_refs and must resolve to data/market/<T>.json")
        for path in scan["undeclared_containers"]:
            fail(f"{aid}: undeclared ticker container at {path}. Hidden ticker_facts blocks "
                 f"are refused even when ticker_refs is empty")
        for t in scan["undeclared_tickers"]:
            fail(f"{aid}: ticker {t} carries structured market facts but is not listed in "
                 f"ticker_refs")
        for t in a.get("ticker_refs") or []:
            if not isinstance(t, str):
                fail(f"{aid}: ticker_refs entry {t!r} is not a ticker string")
                continue
            checked_metrics += 1
            market_path = market_paths.market_path(data, t)
            if not market_path.exists():
                fail(f"{aid}: ticker_refs cites {t} with no {market_path.relative_to(data.parent)}. A session "
                     f"never invents a price: request it and mark the appraisal PENDING_DATA, "
                     f"or write NULL with a basis and no numeric value")
        for row in scan["ticker_metrics"]:
            checked_metrics += 1
            t = row["ticker"]
            market_path = market_paths.market_path(data, t)
            if not market_path.exists():
                fail(f"{aid}: {row['path']} cites {t} with no {market_path.relative_to(data.parent)}. A session "
                     f"never invents a price: request it and mark the appraisal PENDING_DATA, "
                     f"or write NULL with a basis and no numeric value")
                continue
            market = read_json(market_path, {}) or {}
            src = resolve_market_value(t, row["value"], market)
            if not src:
                fail(f"{aid}: {row['path']} cites {row['value']!r} for {t}, but that value is "
                     f"not in data/market/{t}.json. A remembered number is a defect")
            elif row.get("metric") not in ("score",):
                # Report path binding for examined facts (Rule 21 denominator).
                row["market_path"] = src
    report(f"venue rule: {checked_metrics} ticker-level numeric fact(s) examined across "
           f"{len(apps)} appraisal(s)"
           + ("  <- none cited, so numeric facts passed over nothing"
              if not checked_metrics else ""))


def check_corpus_uniqueness(apps: list[dict]) -> None:
    by_occ: dict[str, list[str]] = {}
    by_id: dict[str, str] = {}
    for a in apps:
        aid = a.get("id", a["_file"])
        fname = a["_file"]
        stem = fname[:-5] if fname.endswith(".json") else fname
        if aid != stem:
            fail(f"{aid}: id disagrees with filename {fname}. Permanent identity is the pair")
        if aid in by_id:
            fail(f"{aid}: duplicate appraisal id across {by_id[aid]} and {fname}")
        by_id[aid] = fname
        oid = a.get("occurrence_id")
        if oid:
            by_occ.setdefault(str(oid), []).append(aid)
    dup_occ = {oid: ids for oid, ids in by_occ.items() if len(ids) > 1}
    for oid, ids in sorted(dup_occ.items()):
        fail(f"occurrence_id {oid} has {len(ids)} appraisal(s): {ids}. Exactly one permanent "
             f"appraisal per occurrence is allowed")
    report(f"uniqueness: {len(apps)} appraisal(s), {len(by_occ)} distinct occurrence(s), "
           f"{len(dup_occ)} duplicate occurrence(s)")


def check_corpus_review_by(apps: list[dict]) -> None:
    review_bad = 0
    for a in apps:
        aid = a.get("id", a["_file"])
        as_of = str(a.get("as_of") or "")[:10]
        review = str(a.get("review_by") or "")[:10]
        if not as_of or not review:
            review_bad += 1
            fail(f"{aid}: needs as_of and review_by dates")
            continue
        want = expected_review_by(as_of)
        if review != want:
            review_bad += 1
            fail(f"{aid}: review_by {review!r} is not exactly as_of + 90 days ({want!r})")
    report(f"review_by: {len(apps)} appraisal(s) examined, {review_bad} off-schedule")


def load_occurrences(data: Path) -> dict:
    """Occurrence metadata for queue unmappedness, mirroring impact_calibrate.py."""
    out = {}
    for p in sorted((data / "signals").glob("*.json")) if (data / "signals").exists() else []:
        s = read_json(p)
        if isinstance(s, dict) and s.get("id"):
            out[s["id"]] = {
                "unmappedness": (s.get("unmappedness") or {}).get("score"),
            }
    cands = read_json(data / "radar" / "candidates.json", {}) or {}
    for c in cands.get("candidates", []):
        if not isinstance(c, dict) or not c.get("id"):
            continue
        if c.get("status") in {"DISMISSED", "EXPIRED"}:
            continue
        out[c["id"]] = {"unmappedness": None}
    return out


def check_queue_integrity(apps: list[dict], log: dict, data: Path, today: str,
                          run_day: bool) -> None:
    if not log and apps:
        fail("rank log data/impact/_rank-log.json is missing but appraisals exist on disk")
        report("queue: 0 row(s), missing rank log")
        return

    cal_as_of = (log.get("calibration") or {}).get("as_of") or log.get("as_of")
    if run_day and cal_as_of != today:
        fail(f"rank log calibration as_of is {cal_as_of!r}, not {today}. Run "
             f"tools/impact_calibrate.py: the queue is derived from disk, never hand-ordered")

    queue = log.get("queue") if isinstance(log.get("queue"), list) else []
    apps_by_id = {a.get("id"): a for a in apps if a.get("id")}
    apps_by_occ = {a.get("occurrence_id"): a for a in apps if a.get("occurrence_id")}
    occ_meta = load_occurrences(data)
    expected_ids = set(apps_by_id)
    seen_occ, seen_app, queue_bad = set(), set(), 0

    if apps and not queue:
        queue_bad += 1
        fail(f"rank log queue is empty but {len(apps)} appraisal(s) exist on disk")

    for i, row in enumerate(queue):
        if not isinstance(row, dict):
            queue_bad += 1
            fail(f"rank-log.queue[{i}] is not an object")
            continue
        oid = row.get("occurrence_id")
        rid = row.get("appraisal_id")
        if oid in seen_occ:
            queue_bad += 1
            fail(f"rank-log.queue[{i}] duplicates occurrence_id {oid!r}")
        if rid in seen_app:
            queue_bad += 1
            fail(f"rank-log.queue[{i}] duplicates appraisal_id {rid!r}")
        seen_occ.add(oid)
        seen_app.add(rid)
        app = apps_by_id.get(rid)
        if not app:
            queue_bad += 1
            fail(f"rank-log.queue[{i}] appraisal_id {rid!r} has no on-disk appraisal")
            continue
        if app.get("occurrence_id") != oid:
            queue_bad += 1
            fail(f"rank-log.queue[{i}] pairs occurrence_id {oid!r} with appraisal_id {rid!r}, "
                 f"but {rid} appraises {app.get('occurrence_id')!r}")
        elif apps_by_occ.get(oid) and apps_by_occ[oid].get("id") != rid:
            queue_bad += 1
            fail(f"rank-log.queue[{i}] occurrence_id {oid!r} is appraised by "
                 f"{apps_by_occ[oid].get('id')}, not {rid!r}")
            continue
        c = compute(app)
        want_score, want_band = c["impact_score"], c["impact_band"]
        got_score, got_band = row.get("impact_score"), row.get("impact_band")
        if got_band != want_band:
            queue_bad += 1
            fail(f"rank-log.queue[{i}] impact_band {got_band!r} disagrees with appraisal "
                 f"{rid} (computed {want_band!r})")
        if not scores_match(want_score, got_score):
            queue_bad += 1
            fail(f"rank-log.queue[{i}] impact_score {got_score!r} disagrees with appraisal "
                 f"{rid} (computed {want_score!r})")
        want_unmap = (occ_meta.get(oid) or {}).get("unmappedness")
        got_unmap = row.get("unmappedness")
        if got_unmap != want_unmap:
            queue_bad += 1
            fail(f"rank-log.queue[{i}] unmappedness {got_unmap!r} disagrees with occurrence "
                 f"{oid} (on disk {want_unmap!r})")

    missing = sorted(expected_ids - seen_app)
    for rid in missing:
        queue_bad += 1
        fail(f"rank log queue missing appraisal_id {rid!r}")

    if apps:
        want_order = expected_queue_ids(apps)
        got_order = [row.get("appraisal_id") for row in queue if isinstance(row, dict)]
        if got_order != want_order:
            queue_bad += 1
            fail(f"rank log queue order {got_order!r} disagrees with deterministic rank_key "
                 f"order {want_order!r}. The queue is derived from disk, never hand-ordered")

    denoms = (log.get("calibration") or {}).get("denominators") or {}
    report(f"queue: {len(queue)} row(s) for {len(apps)} appraisal(s), {queue_bad} integrity "
           f"defect(s), calibration as_of {cal_as_of!r}"
           + ("" if run_day else " (freshness not required off run day)")
           + f", {denoms.get('unappraised', '?')} of {denoms.get('occurrences_on_disk', '?')} "
           f"occurrence(s) still unappraised")


def main() -> int:
    argv = sys.argv[1:]
    root = Path(argv[argv.index("--root") + 1]).resolve() if "--root" in argv \
        else Path(__file__).resolve().parent.parent
    today = argv[argv.index("--date") + 1] if "--date" in argv \
        else datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    data = root / "data"

    apps = load_appraisals(data)
    touched = [a for a in apps if today in (str(a.get("as_of") or "")
                                            + json.dumps(a.get("changelog") or []))]
    ledger = (data / "ledger.md").read_text() if (data / "ledger.md").exists() else ""
    impact_lines = [ln for ln in ledger.splitlines()
                    if ln.startswith(today) and re.search(r"\|\s*IMPACT\s*\|", ln)]
    run_day = bool(touched or impact_lines)
    log = read_json(data / "impact" / "_rank-log.json", {}) or {}

    if apps:
        check_corpus_excerpts(apps, data, root)
        check_corpus_web_store(apps, data)
        check_corpus_references(apps, data)
        check_corpus_venue(apps, data)
        check_corpus_uniqueness(apps)
        check_corpus_review_by(apps)
    check_queue_integrity(apps, log, data, today, run_day)

    if not run_day:
        print(f"check_impact: NOT RUN TODAY ({today}). 0 appraisals touched, 0 IMPACT ledger "
              f"lines, {len(apps)} appraisal(s) on disk. Corpus integrity still examined.")
        for ln in lines:
            print(f"  {ln}")
        if failures:
            for f_ in failures:
                print(f"  FAIL  {f_}")
            print(f"check_impact: FAILED with {len(failures)} finding(s)")
            return 1
        print("check_impact: OK")
        return 0

    # ---- run-day: legs on touched appraisals
    for a in touched:
        aid = a.get("id", a["_file"])
        for leg in LEGS:
            obj = a.get(leg)
            if not isinstance(obj, dict):
                fail(f"{aid}: {leg} is not an object")
                continue
            is_null = obj.get("score", "x") is None or obj.get("band", "x") is None
            if is_null:
                if not obj.get("basis"):
                    fail(f"{aid}: {leg} is NULL with no basis. What was looked for is the "
                         f"whole content of a NULL leg")
                continue
            if not obj.get("rationale"):
                fail(f"{aid}: {leg} has no written rationale")
            ev = obj.get("evidence") or []
            dated = [e for e in ev if isinstance(e, dict) and e.get("source_date")]
            if not dated:
                fail(f"{aid}: {leg} has {len(ev)} evidence item(s), 0 dated. A claim with no "
                     f"dated source does not exist (method section 1)")
            if leg == "money_at_stake":
                spec = [i for i, e in enumerate(ev)
                        if isinstance(e, dict) and e.get("tag") == "SPECULATIVE"]
                if spec:
                    fail(f"{aid}: money_at_stake evidence {spec} tagged SPECULATIVE. This leg "
                         f"is cited or NULL (method section 0.2): an unsourced market size "
                         f"sits at the head of the funnel and every later stage inherits it")
                if obj.get("band") not in MONEY_BANDS:
                    fail(f"{aid}: money_at_stake.band {obj.get('band')!r} is not one of "
                         f"{sorted(MONEY_BANDS)}")
    report(f"legs: {len(touched)} of {len(apps)} appraisal(s) examined, "
           f"{len(LEGS)} legs each")

    disagreed = 0
    for a in touched:
        aid = a.get("id", a["_file"])
        c = compute(a)
        if a.get("impact_band") != c["impact_band"]:
            disagreed += 1
            fail(f"{aid}: impact_band {a.get('impact_band')!r} disagrees with its own legs "
                 f"(computed {c['impact_band']!r}). A verdict that disagrees with its own "
                 f"scores is an error, not a style choice (method section 3, 2026-08-29)")
        if c["impact_score"] is None:
            if a.get("impact_score") is not None:
                disagreed += 1
                fail(f"{aid}: impact_score is {a.get('impact_score')!r} while legs "
                     f"{c['null_legs']} are NULL. A partial average is still a number, and a "
                     f"number gets sorted and quoted")
        elif not (isinstance(a.get("impact_score"), (int, float))
                  and abs(float(a["impact_score"]) - c["impact_score"]) < 0.05):
            disagreed += 1
            fail(f"{aid}: impact_score {a.get('impact_score')!r} disagrees with its own legs "
                 f"(computed {c['impact_score']})")
    report(f"computed fields: {len(touched)} appraisal(s) recomputed, {disagreed} disagreement(s)")

    if not impact_lines:
        fail(f"appraisals touched today with no IMPACT ledger line dated {today}")
    else:
        for ln in impact_lines:
            if "ranked:" not in ln:
                fail(f"IMPACT ledger line names no ranked: count -> {ln[:110]}")
            if "band:" not in ln:
                fail(f"IMPACT ledger line names no band: -> {ln[:110]}")
    report(f"ledger: {len(impact_lines)} IMPACT line(s) dated {today}")

    unranked = [a for a in touched if compute(a)["impact_band"] == "UNRANKED"]
    for a in unranked:
        aid = a.get("id", a["_file"])
        if not a.get("unranked_reason"):
            fail(f"{aid}: UNRANKED with no unranked_reason. 'Nobody has published a number' is "
                 f"a finding and it has to be written down to be one")
    report(f"unranked: {len(unranked)} of {len(touched)} touched appraisal(s), each naming its "
           f"missing leg")

    if impact_lines and not touched:
        fail(f"an IMPACT ledger line was written on {today} but no appraisal was touched. "
             f"Every check above passed over an empty set, which is the failure Rule 21 "
             f"exists to catch. Either the run wrote nothing and the ledger line is wrong, or "
             f"the appraisal was not saved")

    print(f"check_impact: {today}")
    for ln in lines:
        print(f"  {ln}")
    if failures:
        for f_ in failures:
            print(f"  FAIL  {f_}")
        print(f"check_impact: FAILED with {len(failures)} finding(s)")
        return 1
    print("check_impact: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
