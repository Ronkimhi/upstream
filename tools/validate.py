#!/usr/bin/env python3
"""Upstream data validator. Stdlib only.

Checks every file under data/ against the schema contract in docs/method.md:
JSON well-formedness, required keys, closed enums, referential integrity,
verdict completeness. Loud per-file report; exit 1 on any error.

Run: python3 tools/validate.py            (from repo root or anywhere)
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

LANES = {"MACRO", "INDUSTRY", "USE_CASE"}
SIGNAL_STATUS = {"NEW", "CHAINED", "DISMISSED", "EXPIRED"}
CLOCKS = {"COMPOUNDER", "EVENT"}
TAGS = {"VERIFIED", "INFERRED", "SPECULATIVE", "NULL"}
INVESTABILITY = {"PURE_PLAYS_EXIST", "PARTIAL", "MOSTLY_PRIVATE", "UNINVESTABLE"}
BOTTLENECK = {"LOW", "MEDIUM", "HIGH", "CHOKE_POINT"}
HEAT_VERDICT = {"UNDISCOVERED", "EMERGING", "CROWDED", "OVER_CROWDED"}
SCENARIO_STATUS = {"OPEN", "SCREENED", "INVALIDATED", "PLAYED_OUT"}
CHAIN_STATUS = {"BUILT", "ARCHIVED"}
SCREEN_ROW_STATUS = {"CANDIDATE", "DIVED", "REJECTED", "SHADOWED"}
TIERS = {"T1", "T2", "T3"}
DIVE_STATUS = {"DRAFT", "FINAL"}
VERDICTS = {"INVESTABLE", "WATCH", "TOO_LATE"}
PRICE_STATUS = {"AGREED", "SINGLE_SOURCE", "DISPUTED", "NO_DATA", "VERIFIED_ZERO"}
REQ_KINDS = {"prices", "fundamentals", "pcs", "edgar_fts", "edgar_doc"}
REQ_STATUS = {"PENDING", "FULFILLED", "FAILED"}
BUCKETS = ("pure_play", "picks_and_shovels", "second_order", "hedge")
TRADE_ACTIONS = {"bought", "sold", "trimmed", "added"}

errors: list[str] = []
warnings: list[str] = []


def err(f: Path, msg: str) -> None:
    errors.append(f"{f.relative_to(ROOT)}: {msg}")


def warn(f: Path, msg: str) -> None:
    warnings.append(f"{f.relative_to(ROOT)}: {msg}")


def load(f: Path):
    try:
        return json.loads(f.read_text())
    except Exception as e:  # noqa: BLE001
        err(f, f"unreadable JSON: {e}")
        return None


def need(f: Path, obj: dict, keys: list[str], ctx: str = "") -> bool:
    missing = [k for k in keys if k not in obj]
    if missing:
        err(f, f"{ctx or 'object'} missing keys: {', '.join(missing)}")
        return False
    return True


def check_enum(f: Path, val, allowed: set, ctx: str) -> None:
    if val not in allowed:
        err(f, f"{ctx}: {val!r} not in {sorted(allowed)}")


def check_evidence(f: Path, items, ctx: str) -> None:
    if not isinstance(items, list):
        err(f, f"{ctx}: evidence must be a list")
        return
    for i, e in enumerate(items):
        if not isinstance(e, dict) or "claim" not in e or "tag" not in e:
            err(f, f"{ctx}[{i}]: evidence needs claim + tag")
            continue
        check_enum(f, e["tag"], TAGS, f"{ctx}[{i}].tag")
        if e["tag"] in {"VERIFIED", "INFERRED"} and not (e.get("source_name") or e.get("source")):
            err(f, f"{ctx}[{i}]: {e['tag']} evidence needs a source")


def check_common(f: Path, obj: dict) -> None:
    for k in ("confidence_audit", "changelog"):
        if k not in obj:
            err(f, f"missing {k}")
    if isinstance(obj.get("changelog"), list):
        for i, c in enumerate(obj["changelog"]):
            if not isinstance(c, dict) or not {"ts", "by", "change"} <= set(c):
                err(f, f"changelog[{i}] needs ts, by, change")
    if isinstance(obj.get("notes"), list):
        for i, n in enumerate(obj["notes"]):
            if not isinstance(n, dict) or not {"ts", "by", "text"} <= set(n):
                err(f, f"notes[{i}] needs ts, by, text")


# ---------------------------------------------------------------- signals
def v_signal(f: Path) -> None:
    s = load(f)
    if s is None:
        return
    if not need(f, s, ["id", "lane", "title", "thesis", "why_now", "retail_gap",
                       "unmappedness", "suggested_clock", "horizon_years",
                       "evidence", "status", "chain_id", "review_by"]):
        return
    check_enum(f, s["lane"], LANES, "lane")
    check_enum(f, s["status"], SIGNAL_STATUS, "status")
    check_enum(f, s["suggested_clock"], CLOCKS, "suggested_clock")
    check_evidence(f, s["evidence"], "evidence")
    if s["status"] == "NEW" and len([e for e in s.get("evidence", []) if isinstance(e, dict)]) < 2:
        err(f, "a NEW signal needs >= 2 evidence items")
    hy = s["horizon_years"]
    if not (isinstance(hy, list) and len(hy) == 2 and 0 < hy[0] <= hy[1] <= 10):
        err(f, f"horizon_years malformed: {hy}")
    if s["status"] == "CHAINED":
        if not s["chain_id"]:
            err(f, "status CHAINED but chain_id is null")
        elif not (DATA / "chains" / f"{s['chain_id']}.json").exists():
            err(f, f"chain_id {s['chain_id']} has no chain file")
    check_common(f, s)


# ---------------------------------------------------------------- chains
def v_chain(f: Path) -> None:
    c = load(f)
    if c is None:
        return
    if not need(f, c, ["id", "signal_id", "clock", "status", "links", "scenarios"]):
        return
    check_enum(f, c["status"], CHAIN_STATUS, "status")
    check_enum(f, c["clock"], CLOCKS, "clock")
    if c["signal_id"] and not (DATA / "signals" / f"{c['signal_id']}.json").exists():
        err(f, f"signal_id {c['signal_id']} has no signal file")
    links = c["links"]
    if not (isinstance(links, list) and 8 <= len(links) <= 15):
        err(f, f"links count {len(links) if isinstance(links, list) else '?'} outside 8..15")
        links = links if isinstance(links, list) else []
    ids = [l.get("id") for l in links]
    if len(ids) != len(set(ids)):
        err(f, "duplicate link ids")
    idset = set(ids)
    for l in links:
        ctx = f"link {l.get('id')}"
        if not need(f, l, ["id", "position", "name", "role", "upstream_of",
                           "downstream_of", "investability", "bottleneck"], ctx):
            continue
        check_enum(f, l["investability"], INVESTABILITY, f"{ctx}.investability")
        bn = l["bottleneck"]
        if not isinstance(bn, dict) or "criticality" not in bn:
            err(f, f"{ctx}.bottleneck needs criticality")
        else:
            check_enum(f, bn["criticality"], BOTTLENECK, f"{ctx}.bottleneck")
        for side in ("upstream_of", "downstream_of"):
            for ref in l[side]:
                if ref not in idset:
                    err(f, f"{ctx}.{side} references unknown link {ref!r}")
        # edge reciprocity: A.upstream_of B  <=>  B.downstream_of A
        for ref in l["upstream_of"]:
            other = next((x for x in links if x.get("id") == ref), None)
            if other is not None and l["id"] not in other.get("downstream_of", []):
                err(f, f"edge not reciprocal: {l['id']} upstream_of {ref} but not mirrored")
        h = l.get("heat")
        if h is not None:
            for score in ("impact", "crowdedness", "capture"):
                sc = h.get(score)
                if sc is None:
                    continue
                if not isinstance(sc, dict) or "score" not in sc or "rationale" not in sc:
                    err(f, f"{ctx}.heat.{score} needs score + rationale")
                elif sc["score"] is not None and not (0 <= sc["score"] <= 100):
                    err(f, f"{ctx}.heat.{score}.score out of 0..100")
                elif sc.get("score") is not None and not sc.get("evidence"):
                    err(f, f"{ctx}.heat.{score} scored without evidence")
            if h.get("verdict") is not None:
                check_enum(f, h["verdict"], HEAT_VERDICT, f"{ctx}.heat.verdict")
            if "money_corner" not in h:
                err(f, f"{ctx}.heat missing money_corner")
    scens = c["scenarios"]
    if scens:
        if not 3 <= len(scens) <= 6:
            err(f, f"scenarios count {len(scens)} outside 3..6")
        total_p = 0
        for s in scens:
            ctx = f"scenario {s.get('id')}"
            if not need(f, s, ["id", "title", "narrative", "probability_pct",
                               "links_moved", "leading_indicators",
                               "invalidation_signs", "status"], ctx):
                continue
            check_enum(f, s["status"], SCENARIO_STATUS, f"{ctx}.status")
            total_p += s["probability_pct"] or 0
            if len(s["leading_indicators"]) < 2:
                err(f, f"{ctx}: needs >= 2 leading indicators")
            if len(s["invalidation_signs"]) < 1:
                err(f, f"{ctx}: needs >= 1 invalidation sign")
            for m in s["links_moved"]:
                if m.get("link_id") not in idset:
                    err(f, f"{ctx}.links_moved references unknown link {m.get('link_id')!r}")
                if m.get("direction") not in {"UP", "DOWN"}:
                    err(f, f"{ctx}.links_moved.direction must be UP|DOWN")
                if m.get("magnitude") not in {"SMALL", "MEDIUM", "LARGE"}:
                    err(f, f"{ctx}.links_moved.magnitude must be SMALL|MEDIUM|LARGE")
            for ind in s["leading_indicators"]:
                chk = ind.get("check")
                if chk is not None and not {"type", "ticker", "op", "level"} <= set(chk):
                    err(f, f"{ctx}: indicator check needs type, ticker, op, level")
            if s["status"] == "SCREENED" and s.get("screen_ref"):
                if not (ROOT / "data" / Path(s["screen_ref"]).name).exists() and \
                   not (DATA / "screens" / Path(s["screen_ref"]).name).exists():
                    err(f, f"{ctx}.screen_ref {s['screen_ref']} not found")
        if not 90 <= total_p <= 110:
            err(f, f"scenario probabilities sum to {total_p}, outside 90..110")
    check_common(f, c)


# ---------------------------------------------------------------- screens
def v_screen(f: Path) -> None:
    s = load(f)
    if s is None:
        return
    if not need(f, s, ["id", "chain_id", "scenario_id", "as_of", "universe_note",
                       "queries_run", "buckets", "health"]):
        return
    if not (DATA / "chains" / f"{s['chain_id']}.json").exists():
        err(f, f"chain_id {s['chain_id']} has no chain file")
    b = s["buckets"]
    for name in BUCKETS:
        if name not in b:
            err(f, f"buckets missing {name}")
            continue
        for row in b[name]:
            ctx = f"{name}/{row.get('ticker')}"
            if not need(f, row, ["ticker", "tier", "thesis_1line", "status"], ctx):
                continue
            check_enum(f, row["tier"], TIERS, f"{ctx}.tier")
            check_enum(f, row["status"], SCREEN_ROW_STATUS, f"{ctx}.status")
            for ng in row.get("earnings_nuggets", []):
                if not {"quote", "accession", "url"} <= set(ng):
                    err(f, f"{ctx}: earnings nugget needs quote, accession, url")
    check_common(f, s)


# ---------------------------------------------------------------- stocks
def v_stock(f: Path) -> None:
    d = load(f)
    if d is None:
        return
    if not need(f, d, ["ticker", "chain_id", "tier", "clock", "status", "verdict",
                       "what_is_priced_in", "bull", "bear", "events",
                       "valuation_snapshot", "price_ref", "review_by"]):
        return
    check_enum(f, d["tier"], TIERS, "tier")
    check_enum(f, d["clock"], CLOCKS, "clock")
    check_enum(f, d["status"], DIVE_STATUS, "status")
    check_enum(f, d["verdict"], VERDICTS, "verdict")
    if d["verdict"] == "WATCH" and not d.get("watch_triggers"):
        err(f, "verdict WATCH requires non-empty watch_triggers")
    if d["verdict"] == "INVESTABLE":
        ez = d.get("entry_zone")
        if not (isinstance(ez, dict) and {"low", "high", "basis"} <= set(ez)):
            err(f, "verdict INVESTABLE requires entry_zone{low, high, basis}")
    if d["verdict"] == "TOO_LATE" and not d.get("shadow_ref"):
        err(f, "verdict TOO_LATE requires shadow_ref (shadow row must exist)")
    if len(d["bull"]) != 3 or len(d["bear"]) != 3:
        err(f, "bull and bear must each have exactly 3 bullets")
    if d["status"] == "FINAL":
        rt = d.get("red_team")
        if not (isinstance(rt, dict) and {"attacked_at", "challenges",
                                          "verdict_survived", "surviving_bear_case"} <= set(rt)):
            err(f, "status FINAL requires a complete red_team block")
    if not (DATA / "chains" / f"{d['chain_id']}.json").exists():
        err(f, f"chain_id {d['chain_id']} has no chain file")
    mk = DATA / "market" / f"{d['ticker'].replace('.', '-')}.json"
    if not mk.exists() and not d.get("fixture"):
        warn(f, f"no market file for {d['ticker']} (chart will show request state)")
    check_common(f, d)


# ---------------------------------------------------------------- market / misc
def v_market(f: Path) -> None:
    m = load(f)
    if m is None:
        return
    if f.name == "_meta.json":
        return
    if not need(f, m, ["ticker", "fetched_at", "price_status"]):
        return
    check_enum(f, m["price_status"], PRICE_STATUS, "price_status")
    if m["price_status"] == "VERIFIED_ZERO" and not m.get("probe"):
        err(f, "VERIFIED_ZERO requires probe evidence")
    series = m.get("series")
    if series and series.get("rows"):
        rows = series["rows"]
        if not all(isinstance(r, list) and len(r) == 2 for r in rows[:5]):
            err(f, "series.rows must be [date, close] pairs")


def v_requests(f: Path) -> None:
    r = load(f)
    if r is None:
        return
    ids = set()
    for req in r.get("requests", []):
        rid = req.get("id", "?")
        if rid in ids:
            err(f, f"duplicate request id {rid}")
        ids.add(rid)
        check_enum(f, req.get("kind"), REQ_KINDS, f"{rid}.kind")
        check_enum(f, req.get("status"), REQ_STATUS, f"{rid}.status")
        if req.get("kind") in {"prices", "fundamentals", "pcs", "edgar_doc"} and not req.get("ticker"):
            err(f, f"{rid}: kind {req.get('kind')} needs a ticker")
        if req.get("kind") == "edgar_fts" and not req.get("query"):
            err(f, f"{rid}: edgar_fts needs a query")


def v_shadow(f: Path) -> None:
    s = load(f)
    if s is None:
        return
    if f.name == "book.json":
        for row in s.get("rows", []):
            if not {"id", "ticker", "origin", "verdict_date", "spot", "review_at"} <= set(row):
                err(f, f"shadow row {row.get('id', '?')} incomplete")


def v_trades(f: Path) -> None:
    for i, line in enumerate(f.read_text().splitlines()):
        if not line.strip():
            continue
        try:
            t = json.loads(line)
        except Exception:  # noqa: BLE001
            err(f, f"line {i + 1}: not valid JSON")
            continue
        if not {"ts", "by", "ticker", "action", "price"} <= set(t):
            err(f, f"line {i + 1}: needs ts, by, ticker, action, price")
        elif t["action"] not in TRADE_ACTIONS:
            err(f, f"line {i + 1}: action {t['action']!r} not in {sorted(TRADE_ACTIONS)}")


def main() -> int:
    counts = {}
    plans = [
        ("signals", DATA / "signals", "*.json", v_signal),
        ("chains", DATA / "chains", "*.json", v_chain),
        ("screens", DATA / "screens", "*.json", v_screen),
        ("stocks", DATA / "stocks", "*.json", v_stock),
        ("market", DATA / "market", "*.json", v_market),
        ("shadow", DATA / "shadow", "*.json", v_shadow),
    ]
    for name, folder, glob, fn in plans:
        files = sorted(folder.glob(glob)) if folder.exists() else []
        counts[name] = len(files)
        for f in files:
            fn(f)
    if (DATA / "requests.json").exists():
        v_requests(DATA / "requests.json")
    if (DATA / "trades.jsonl").exists():
        v_trades(DATA / "trades.jsonl")

    summary = " · ".join(f"{k}: {v}" for k, v in counts.items())
    print(f"validate: {summary}")
    for w in warnings:
        print(f"  WARN  {w}")
    if errors:
        for e in errors:
            print(f"  ERROR {e}")
        print(f"validate: FAILED with {len(errors)} error(s)")
        return 1
    print("validate: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
