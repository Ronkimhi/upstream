#!/usr/bin/env python3
"""Upstream data validator. Stdlib only.

Checks every file under data/ against the schema contract in docs/method.md:
JSON well-formedness, required keys, closed enums, referential integrity,
verdict completeness. Loud per-file report; exit 1 on any error.

Run: python3 tools/validate.py            (from repo root or anywhere)
     python3 tools/validate.py --root PATH  (validate another tree, e.g. a probe clone)
"""
import datetime
import json
import re
import sys
from pathlib import Path

from check_analyst import WOULD_BUY_GATE, latest_changelog_date, would_buy_failures
from check_campaign import validate_campaign
from check_harness import validate_report as harness_report_errors
from check_map import corpus_identity_failures, validate_mapping
from check_profile import validate_profile
from market_paths import market_path
from heat_score import band_for, money_corner, score_from
from impact_score import BANDS as IMPACT_BANDS, LEGS as IMPACT_LEGS, MONEY_BANDS, compute as impact_compute

# --root brings this in line with check_radar / check_chain / check_analyst / check_screen,
# which all accept one. Without it the validator could only ever be pointed at its own
# repo, so testing what it does to a deliberately corrupted file meant corrupting live data.
_argv = sys.argv[1:]
ROOT = (Path(_argv[_argv.index("--root") + 1]).resolve() if "--root" in _argv
        else Path(__file__).resolve().parent.parent)
DATA = ROOT / "data"
# UTC, not local. Ledger lines are stamped in UTC (the `Z` in every line) and these
# gates look for "a line dated today", so a session running between local midnight
# and UTC midnight would search for a date the ledger will never carry and fail a
# gate over a timezone. Cloud, local and Actions venues all agree on UTC.
TODAY = datetime.datetime.now(datetime.timezone.utc).date().isoformat()

LANES = {"MACRO", "INDUSTRY", "USE_CASE"}
SIGNAL_STATUS = {"NEW", "CHAINED", "DISMISSED", "EXPIRED"}
CLOCKS = {"COMPOUNDER", "EVENT"}
TAGS = {"VERIFIED", "INFERRED", "SPECULATIVE", "NULL"}
INVESTABILITY = {"PURE_PLAYS_EXIST", "PARTIAL", "MOSTLY_PRIVATE", "UNINVESTABLE"}
BOTTLENECK = {"LOW", "MEDIUM", "HIGH", "CHOKE_POINT"}
HEAT_VERDICT = {"QUIET", "UNDISCOVERED", "EMERGING", "CROWDED", "OVER_CROWDED"}
SCENARIO_STATUS = {"OPEN", "SCREENED", "INVALIDATED", "PLAYED_OUT"}
CHAIN_STATUS = {"BUILT", "ARCHIVED"}
SCREEN_ROW_STATUS = {"CANDIDATE", "DIVED", "REJECTED", "SHADOWED"}
TIERS = {"T1", "T2", "T3"}
DIVE_STATUS = {"DRAFT", "FINAL"}
VERDICTS = {"INVESTABLE", "WATCH", "TOO_LATE"}
REVIEW_VERDICTS = {"ADOPT", "ADOPT_NARROWED", "REJECT"}
PRICE_STATUS = {"AGREED", "SINGLE_SOURCE", "DISPUTED", "NO_DATA", "VERIFIED_ZERO"}
REQ_KINDS = {"prices", "fundamentals", "pcs", "edgar_fts", "edgar_doc",
             "quality", "insider",   # quality/insider added 2026-08-29 (the analyst)
             "web_doc"}              # web evidence store, 2026-09-01 (tools/evidence_store.py)
REQ_STATUS = {"PENDING", "FULFILLED", "FAILED"}
BUCKETS = ("pure_play", "picks_and_shovels", "second_order", "hedge")
TRADE_ACTIONS = {"bought", "sold", "trimmed", "added"}
OCCURRENCE_KINDS = {"HAPPENED", "UNDERWAY", "SCHEDULED"}
OCCURRENCE_GATE = "2026-08-30"  # signals created on/after this date must carry an occurrence block
CAL_KINDS = {"POLICY", "CORPORATE", "MACRO", "TECH", "LEGAL"}
CAL_STATUS = {"WATCHING", "PROMOTED", "PASSED", "DROPPED"}
CAND_FAMILIES = {"POLICY", "CORPORATE", "TECH", "PHYSICAL", "GEO"}
CAND_STATUS = {"AMBIENT", "PROMOTED", "DISMISSED", "EXPIRED"}
RULE_ORIGINS = {"METHOD", "PREFERENCE"}
RULE_STATUS = {"PROPOSED", "HARDENED", "REJECTED"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TODAY = datetime.datetime.now(datetime.timezone.utc).date().isoformat()

# Evidence written on or after this date must be dated, and VERIFIED evidence must carry a
# url. Same shape and same reason as OCCURRENCE_GATE above: 88 legacy evidence items carry
# no url and 70 carry no date, so an immediate hard error would fail every postlude in the
# repo and the check would be reverted within the hour. Legacy items warn and are counted
# in the citation-debt line printed once per run, so the debt is visible while it is paid.
EVIDENCE_GATE = "2026-08-30"

# Staleness thresholds, method section 9. WARN only: the market moves on weekends and the
# fetch plane runs weekdays, so an error here would brick a Saturday postlude for a
# condition nobody can fix until Monday. The hard staleness gate binds where it matters —
# on the dive being written today, in check_analyst.py.
STALE_MARKET_DAYS = 7
STALE_PCS_DAYS = 30
STALE_FUNDAMENTALS_DAYS = 100

errors: list[str] = []
warnings: list[str] = []
_debt = {"no_date": 0, "no_url": 0}


def err(f: Path, msg: str) -> None:
    errors.append(f"{f.relative_to(ROOT)}: {msg}")


def warn(f: Path, msg: str) -> None:
    warnings.append(f"{f.relative_to(ROOT)}: {msg}")


def _age_days(when):
    """Days between an ISO date/timestamp and today, or None if unparseable."""
    try:
        return (datetime.datetime.now(datetime.timezone.utc).date()
                - datetime.date.fromisoformat(str(when)[:10])).days
    except (ValueError, TypeError):
        return None


def _citation_shortfall(f: Path, msg: str, kind: str, gated: bool) -> None:
    """A missing or malformed citation: an error past EVIDENCE_GATE, counted debt before it."""
    if gated:
        err(f, msg)
    else:
        _debt[kind] += 1
        warn(f, msg)


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


def check_date(f: Path, val, ctx: str) -> bool:
    if not (isinstance(val, str) and DATE_RE.match(val)):
        err(f, f"{ctx}: {val!r} is not YYYY-MM-DD")
        return False
    return True


def check_mini_changelog(f: Path, entries, ctx: str) -> None:
    """Changelog check for objects without the full check_common contract."""
    if not isinstance(entries, list):
        err(f, f"{ctx}: changelog must be a list")
        return
    for i, c in enumerate(entries):
        if not isinstance(c, dict) or not {"ts", "by", "change"} <= set(c):
            err(f, f"{ctx}.changelog[{i}] needs ts, by, change")


def check_evidence(f: Path, items, ctx: str, touched_on: str | None = None) -> None:
    """method section 1 as a check: `value [source, as of YYYY-MM-DD]`.

    "A number without both source and date does not exist for decision purposes" was
    written in the constitution and enforced on exactly one surface (check_radar, on
    signals, and there only as a truthiness test). Every other evidence item in the repo
    could carry a source name and no date, or no locator of any kind, and pass. On
    2026-08-29 all 88 evidence items in data/ carried zero URLs, and the 70 chain heat
    items carried no source_date field at all.

    `touched_on` is the parent object's own date (created_at / heat_as_of / the last
    changelog stamp). Items on an object touched on or after EVIDENCE_GATE must be dated,
    and a VERIFIED item must carry the locator that makes VERIFIED mean anything. Older
    items warn and are counted as citation debt, printed once per run. The gate is dated
    rather than immediate for the reason the OCCURRENCE_GATE was: closing it on legacy
    data would fail every postlude in the repo, and a check that cannot be run is not a
    check. Note the consequence, which is intended: amending a legacy card moves it past
    the gate, so touching a card means dating its evidence.
    """
    if not isinstance(items, list):
        err(f, f"{ctx}: evidence must be a list")
        return
    gated = bool(touched_on) and str(touched_on)[:10] >= EVIDENCE_GATE
    for i, e in enumerate(items):
        if not isinstance(e, dict) or "claim" not in e or "tag" not in e:
            err(f, f"{ctx}[{i}]: evidence needs claim + tag")
            continue
        check_enum(f, e["tag"], TAGS, f"{ctx}[{i}].tag")
        if e["tag"] in {"VERIFIED", "INFERRED"} and not (e.get("source_name") or e.get("source")):
            err(f, f"{ctx}[{i}]: {e['tag']} evidence needs a source")
        if e["tag"] == "NULL":
            continue  # a NULL is a recorded absence; it has nothing to cite
        date = e.get("source_date")
        url = e.get("url") or e.get("source_url")
        # Same gate governs presence AND format. Several seed items carry month precision
        # ("2026-07"), which is honest imprecision about a monthly source rather than a
        # fabrication, and erroring on it would fail every postlude over data written
        # before the rule existed. New evidence snaps to a day, the way method section 0
        # already makes occurrence anchors snap.
        if not date:
            _citation_shortfall(
                f, f"{ctx}[{i}]: evidence needs source_date (method section 1)"
                   + (f" — the parent was touched {str(touched_on)[:10]}, on or after the "
                      f"{EVIDENCE_GATE} gate" if gated else ""), "no_date", gated)
        elif not DATE_RE.match(str(date)):
            _citation_shortfall(
                f, f"{ctx}[{i}].source_date: {date!r} is not YYYY-MM-DD (snap an imprecise "
                   f"source to a day, as method section 0 does for anchors)", "no_date", gated)
        elif str(date) > TODAY:
            # Always an error, gate or no gate: a source dated in the future is not
            # imprecision, it is a claim about a document that does not exist yet.
            err(f, f"{ctx}[{i}].source_date: {date} is in the future")
        if not url:
            if gated and e["tag"] == "VERIFIED":
                err(f, f"{ctx}[{i}]: VERIFIED evidence needs a url — VERIFIED means "
                       f"fetched from a primary source this run, which is a claim about a "
                       f"document somebody else can open")
            else:
                _debt["no_url"] += 1
        elif not str(url).startswith(("http://", "https://")):
            err(f, f"{ctx}[{i}].url: {str(url)[:60]!r} is not a fetchable http(s) URL")


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
    check_evidence(f, s["evidence"], "evidence",
                   touched_on=s.get("updated_at") or s.get("created_at"))
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
    occ = s.get("occurrence")
    if occ is None:
        if s.get("created_at", "") >= OCCURRENCE_GATE:
            err(f, "occurrence block required for signals created >= " + OCCURRENCE_GATE)
        else:
            warn(f, "occurrence missing (pre-gate card; backfill via AMEND)")
    elif not isinstance(occ, dict):
        err(f, "occurrence must be an object")
    elif need(f, occ, ["kind", "anchor_date", "label"], "occurrence"):
        check_enum(f, occ["kind"], OCCURRENCE_KINDS, "occurrence.kind")
        if check_date(f, occ["anchor_date"], "occurrence.anchor_date"):
            # time-direction sanity is advisory only: validation must not rot with the calendar
            if occ["kind"] in {"HAPPENED", "UNDERWAY"} and occ["anchor_date"] > TODAY:
                warn(f, f"occurrence {occ['kind']} anchored in the future ({occ['anchor_date']})")
            if occ["kind"] == "SCHEDULED" and occ["anchor_date"] < TODAY:
                warn(f, f"SCHEDULED occurrence date passed ({occ['anchor_date']}); amend kind or dismiss")
        if not occ.get("label"):
            err(f, "occurrence.label must be non-empty")
    check_common(f, s)


# ---------------------------------------------------------------- impact appraisals
def v_impact(f: Path) -> None:
    """method section 0.2: four legs, cited-or-NULL money, a band computed from the legs."""
    a = load(f)
    if a is None:
        return
    if not need(f, a, ["id", "occurrence_id", "as_of", "anchor_date", "money_at_stake",
                       "public_reach", "capture_odds", "timing_fit", "impact_score",
                       "impact_band", "review_by", "confidence_audit"]):
        return
    check_date(f, a["as_of"], "as_of")
    check_date(f, a["review_by"], "review_by")
    check_date(f, a["anchor_date"], "anchor_date")

    # The occurrence must exist. An appraisal of a thesis nobody wrote down is a number with
    # no subject, and it would still sort into the queue.
    oid = a["occurrence_id"]
    if isinstance(oid, str) and oid.startswith("SIG-"):
        sig = DATA / "signals" / f"{oid}.json"
        if not sig.exists():
            err(f, f"occurrence_id {oid} has no signal file")
        else:
            s = load(sig) or {}
            occ_anchor = (s.get("occurrence") or {}).get("anchor_date")
            if occ_anchor and occ_anchor != a["anchor_date"]:
                err(f, f"anchor_date {a['anchor_date']} disagrees with {oid} "
                       f"occurrence.anchor_date {occ_anchor}")
    elif isinstance(oid, str) and oid.startswith("CAND-"):
        cands = load(DATA / "radar" / "candidates.json") if (DATA / "radar" / "candidates.json").exists() else None
        ids = {c.get("id") for c in (cands or {}).get("candidates", []) if isinstance(c, dict)}
        if oid not in ids:
            err(f, f"occurrence_id {oid} is not in data/radar/candidates.json")
    else:
        err(f, f"occurrence_id {oid!r} must be a SIG- or CAND- id")

    for leg in IMPACT_LEGS:
        obj = a.get(leg)
        if not isinstance(obj, dict):
            err(f, f"{leg} must be an object (score/band + rationale + evidence)")
            continue
        is_null = obj.get("score", "missing") is None or obj.get("band", "missing") is None
        if is_null:
            # A NULL leg is a first-class result and the only thing it owes is why.
            if not obj.get("basis"):
                err(f, f"{leg} is NULL and needs a stated basis (what was looked for)")
            continue
        if not obj.get("rationale"):
            err(f, f"{leg} needs a written rationale (method section 3 discipline)")
        ev = obj.get("evidence")
        if not isinstance(ev, list) or not ev:
            err(f, f"{leg} needs >= 1 dated cited evidence item, or NULL with a basis")
        else:
            check_evidence(f, ev, leg, touched_on=a.get("as_of"))
            if leg == "money_at_stake":
                # The one leg where a reasoned guess is refused outright (method section 0.2):
                # an unsourced market size sits at the head of the funnel and every later
                # stage inherits it.
                for i, e in enumerate(ev):
                    if isinstance(e, dict) and e.get("tag") == "SPECULATIVE":
                        err(f, f"money_at_stake.evidence[{i}] is SPECULATIVE; this leg is "
                               f"cited or NULL (method section 0.2)")
        if leg == "money_at_stake":
            if obj.get("band") not in MONEY_BANDS:
                err(f, f"money_at_stake.band: {obj.get('band')!r} not in {sorted(MONEY_BANDS)}")
        else:
            v = obj.get("score")
            if not (isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100):
                err(f, f"{leg}.score must be 0-100 or null, got {v!r}")

    # The band is computed, never written. Same rule as the 2026-08-29 section 3 amendment:
    # a verdict that disagrees with its own scores is an error, not a style choice.
    check_enum(f, a["impact_band"], set(IMPACT_BANDS), "impact_band")
    c = impact_compute(a)
    if a["impact_band"] != c["impact_band"]:
        err(f, f"impact_band {a['impact_band']!r} disagrees with its own legs "
               f"(computed {c['impact_band']!r}; null legs: {c['null_legs'] or 'none'})")
    if c["impact_score"] is None:
        if a["impact_score"] is not None:
            err(f, f"impact_score must be null while legs are NULL ({c['null_legs']})")
        if not a.get("unranked_reason"):
            err(f, "UNRANKED appraisal needs an unranked_reason naming the missing leg")
    elif not (isinstance(a["impact_score"], (int, float))
              and abs(float(a["impact_score"]) - c["impact_score"]) < 0.05):
        err(f, f"impact_score {a['impact_score']!r} disagrees with its own legs "
               f"(computed {c['impact_score']})")
    check_common(f, a)


def v_rank_log(f: Path) -> None:
    log = load(f)
    if log is None:
        return
    if not need(f, log, ["as_of", "calibration", "queue"], "rank-log"):
        return
    check_date(f, log["as_of"], "rank-log.as_of")
    cal = log.get("calibration")
    if not isinstance(cal, dict) or "denominators" not in cal:
        err(f, "rank-log.calibration needs a denominators block (method section 9, Rule 21)")
    if not isinstance(log.get("queue"), list):
        err(f, "rank-log.queue must be a list")
        return
    for i, row in enumerate(log["queue"]):
        if not isinstance(row, dict) or not {"occurrence_id", "impact_band"} <= set(row):
            err(f, f"rank-log.queue[{i}] needs occurrence_id and impact_band")
            continue
        # The pair, never the score alone (method section 0.2). A queue that stopped carrying
        # unmappedness is a queue that quietly replaced section 0's selection rule.
        if "unmappedness" not in row:
            err(f, f"rank-log.queue[{i}] must carry unmappedness beside impact_score")


# ---------------------------------------------------------------- chains
def v_chain(f: Path) -> None:
    c = load(f)
    if c is None:
        return
    if not need(f, c, ["id", "signal_id", "clock", "status", "links", "scenarios",
                       "map_limitation"]):
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
    by_id = {x.get("id"): x for x in links if isinstance(x, dict)}
    for l in links:
        ctx = f"link {l.get('id')}"
        if not need(f, l, ["id", "position", "name", "role", "upstream_of",
                           "downstream_of", "investability", "bottleneck",
                           "example_tickers"], ctx):
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
        # edge reciprocity, BOTH directions. Until 2026-08-29 only the upstream_of leg was
        # walked while the comment claimed <=>, so a stray downstream_of entry pointing at a
        # real link passed silently: exactly what editing one side of an edge produces.
        for ref in l["upstream_of"]:
            other = by_id.get(ref)
            if other is not None and l["id"] not in other.get("downstream_of", []):
                err(f, f"edge not reciprocal: {l['id']} upstream_of {ref} but not mirrored")
        for ref in l["downstream_of"]:
            other = by_id.get(ref)
            if other is not None and l["id"] not in other.get("upstream_of", []):
                err(f, f"edge not reciprocal: {l['id']} downstream_of {ref} but not mirrored")
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
                elif sc.get("evidence"):
                    # Heat evidence was only ever checked for existence, so all 70 items
                    # in the seed corpus carry no source_date field at all while their
                    # sibling signal evidence does. A score is a number; method section 1
                    # binds it like any other.
                    check_evidence(f, sc["evidence"], f"{ctx}.heat.{score}.evidence",
                                   touched_on=h.get("as_of") or c.get("heat_as_of"))
            if h.get("verdict") is not None:
                check_enum(f, h["verdict"], HEAT_VERDICT, f"{ctx}.heat.verdict")
            if "money_corner" not in h:
                err(f, f"{ctx}.heat missing money_corner")
            # method 3: bands partition the space and are COMPUTED, never written by hand.
            imp, crd, cap = (score_from(h, k) for k in ("impact", "crowdedness", "capture"))
            want = band_for(imp, crd)
            if want and h.get("verdict") is not None and h["verdict"] != want:
                err(f, f"{ctx}.heat.verdict is {h['verdict']!r} but impact {imp} / "
                       f"crowdedness {crd} computes {want} (method 3)")
            if None not in (imp, crd, cap):
                want_mc = money_corner(imp, crd, cap)
                if h.get("money_corner") != want_mc:
                    err(f, f"{ctx}.heat.money_corner is {h.get('money_corner')!r} but "
                           f"i{imp}/c{crd}/v{cap} computes {want_mc} (method 3)")
    # ---- structure: position, topology, coverage (method 4, amended 2026-08-29)
    pos = {l.get("id"): l.get("position") for l in links if isinstance(l, dict)}
    n = len(links)
    if sorted(v for v in pos.values() if isinstance(v, int)) != list(range(1, n + 1)):
        err(f, f"positions are not a permutation of 1..{n}: {sorted(pos.values(), key=str)}")
    else:
        # upstream first: raw input at 1, demand anchor last
        for l in links:
            for b in l.get("upstream_of", []):
                if b in pos and pos[l["id"]] >= pos[b]:
                    err(f, f"direction: {l['id']} (pos {pos[l['id']]}) is upstream_of {b} "
                           f"(pos {pos[b]}) but does not precede it. method 4: position 1 is "
                           f"the raw input, the last position is the demand anchor")
    for l in links:
        if not (l.get("upstream_of") or l.get("downstream_of")):
            err(f, f"link {l.get('id')} is an orphan: no edge either way")
    # one connected component, over the undirected edge set
    if links:
        adj = {l["id"]: set() for l in links if isinstance(l, dict) and l.get("id")}
        for l in links:
            for b in list(l.get("upstream_of", [])) + list(l.get("downstream_of", [])):
                if b in adj and l.get("id") in adj:
                    adj[l["id"]].add(b); adj[b].add(l["id"])
        seen, stack = set(), [links[0].get("id")]
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in adj:
                continue
            seen.add(cur); stack.extend(adj[cur] - seen)
        if len(seen) != len(adj):
            err(f, f"chain is not one connected component: {len(seen)} of {len(adj)} links "
                   f"reachable; stranded {sorted(set(adj) - seen)}")
        # acyclic over the directed upstream_of edges
        colour = {}

        def cyclic(node):
            colour[node] = 1
            for nxt in by_id.get(node, {}).get("upstream_of", []):
                if colour.get(nxt) == 1:
                    return nxt
                if nxt in by_id and colour.get(nxt) is None and cyclic(nxt):
                    return nxt
            colour[node] = 2
            return None
        for l in links:
            if colour.get(l.get("id")) is None:
                hit = cyclic(l["id"])
                if hit:
                    err(f, f"chain has a cycle through link {hit!r}; a chain is a process, "
                           f"one thing leads to another")
                    break
    if not (c.get("map_limitation") or "").strip():
        err(f, "map_limitation is empty: method 4 requires every map to state what it "
               "structurally cannot see")
    # coverage findings, reported with denominators rather than failing the build
    empty_tk = [l.get("id") for l in links if not (l.get("example_tickers") or [])]
    if empty_tk:
        warn(f, f"{len(empty_tk)} of {n} links carry no example_tickers: {', '.join(map(str, empty_tk))}")
    thin_choke = [l.get("id") for l in links
                  if (l.get("bottleneck") or {}).get("criticality") == "CHOKE_POINT"
                  and len(l.get("example_tickers") or []) <= 1]
    if thin_choke:
        warn(f, f"{len(thin_choke)} CHOKE_POINT link(s) with one ticker or none: "
                f"{', '.join(map(str, thin_choke))}. Thin coverage at a choke point is the "
                f"map's real hard-to-reach problem")
    # the citation bar (method 4, amended 2026-08-29): WARNING while the seed corpus is
    # backfilled, hardens to an error once clean. Dated in the ledger when it does.
    uncited = [l.get("id") for l in links if not l.get("evidence")]
    if uncited:
        warn(f, f"{len(uncited)} of {n} links carry no evidence[]: a link is a claim that a "
                f"stage exists and an edge is a claim that one stage feeds another. "
                f"{', '.join(map(str, uncited))}")
    chain_touched = c.get("updated_at") or (c.get("changelog") or [{}])[-1].get("ts")
    for l in links:
        if l.get("evidence"):
            check_evidence(f, l["evidence"], f"link {l.get('id')}.evidence",
                           touched_on=chain_touched)
            supported = {e.get("supports") for e in l["evidence"] if isinstance(e, dict)}
            missing = [b for b in l.get("upstream_of", []) if b not in supported]
            if missing and "role" not in supported:
                warn(f, f"link {l.get('id')}: edges {missing} are not named by any evidence "
                        f"item's supports field")

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
    chain_file = DATA / "chains" / f"{s['chain_id']}.json"
    if not chain_file.exists():
        err(f, f"chain_id {s['chain_id']} has no chain file")
        chain = None
    else:
        try:
            chain = json.loads(chain_file.read_text())
        except Exception:  # noqa: BLE001
            chain = None
    link_ids = {l.get("id") for l in (chain or {}).get("links", [])}
    scen_ids = {sc.get("id") for sc in (chain or {}).get("scenarios", [])}
    # scenario_id null = chain-level screen (universe built per link, method §6).
    chain_level = s["scenario_id"] is None
    if not chain_level and chain is not None and s["scenario_id"] not in scen_ids:
        err(f, f"scenario_id {s['scenario_id']} is not a scenario of chain {s['chain_id']}")
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
            # link_id on EVERY row (method 6, amended 2026-08-29), scenario screens included:
            # a name that cannot be attributed to the link that surfaced it cannot be counted
            # against that link, and link yield is the only measure of whether a map was worth
            # building. Null is allowed with a stated basis, the same discipline as an
            # undisclosed exposure percentage.
            if "link_id" not in row:
                err(f, f"{ctx}: every screen row needs link_id (null with link_id_basis when "
                       f"the name genuinely cannot be attributed to one link)")
            else:
                lid = row.get("link_id")
                if lid is None:
                    if not row.get("link_id_basis"):
                        err(f, f"{ctx}: link_id is null and needs a link_id_basis saying why")
                elif chain is not None and lid not in link_ids:
                    err(f, f"{ctx}: link_id {lid!r} is not a link of chain {s['chain_id']}")
            if chain_level and not row.get("link_id"):
                err(f, f"{ctx}: a chain-level screen universe is built per link, so its rows "
                       f"cannot be unattributed")
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
    if d["verdict"] == "WATCH":
        # Ron, 2026-09-03: a WATCH names the price at which this file's own thesis would
        # have been INVESTABLE (`would_buy_zone{low, high, basis}` below price_ref), or
        # null with `would_buy_basis` naming the cap that binds at any price. The shape
        # rule lives in tools/check_analyst.would_buy_failures; this is the same rule at
        # validate time. Dives last changed before the gate warn, so the tree validates
        # until the red-team re-runs fill the field; after that date it is an error.
        pre_gate = latest_changelog_date(d) < WOULD_BUY_GATE
        for msg in would_buy_failures(d):
            (warn if pre_gate else err)(f, msg)
    if d["verdict"] == "INVESTABLE":
        ez = d.get("entry_zone")
        if not (isinstance(ez, dict) and {"low", "high", "basis"} <= set(ez)):
            err(f, "verdict INVESTABLE requires entry_zone{low, high, basis}")
        else:
            lo, hi = ez.get("low"), ez.get("high")
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo >= hi:
                err(f, f"entry_zone low {lo} is not below high {hi}")
            if not str(ez.get("basis") or "").strip():
                err(f, "entry_zone.basis is empty: method section 7 wants the analytical "
                       "reason for the band in one line, not just the numbers")
        # no_entry_above is required by CLAUDE.md for every INVESTABLE dive and was
        # checked by nothing anywhere in the repo. It is the level that makes a verdict
        # falsifiable: without it "INVESTABLE" has no price at which it stops being true.
        nea = d.get("no_entry_above")
        if not isinstance(nea, (int, float)):
            err(f, "verdict INVESTABLE requires a numeric no_entry_above (the level at "
                   "which the verdict stops holding)")
        elif isinstance(ez, dict) and isinstance(ez.get("high"), (int, float)) \
                and nea < ez["high"]:
            err(f, f"no_entry_above {nea} sits below the top of the entry zone "
                   f"{ez['high']}: the dive would forbid its own entry")
    if d["verdict"] == "TOO_LATE" and not d.get("shadow_ref"):
        err(f, "verdict TOO_LATE requires shadow_ref (shadow row must exist)")
    if len(d["bull"]) != 3 or len(d["bear"]) != 3:
        err(f, "bull and bear must each have exactly 3 bullets")
    # price_ref was a required KEY whose shape nobody checked, so it could be a bare
    # number, a string, or a path with no date — none of which lets a reader ask "as of
    # when, from where". method section 1 shape: {value, source, as_of}.
    pr = d.get("price_ref")
    if pr is not None:
        if not isinstance(pr, dict) or not {"value", "source", "as_of"} <= set(pr):
            err(f, "price_ref must be {value, source, as_of} — a price with no date and "
                   "no source does not exist for decision purposes (method section 1)")
        else:
            if not isinstance(pr.get("value"), (int, float)):
                err(f, f"price_ref.value {pr.get('value')!r} is not a number")
            check_date(f, pr.get("as_of"), "price_ref.as_of")
    if d["status"] == "FINAL":
        rt = d.get("red_team")
        if not (isinstance(rt, dict) and {"attacked_at", "challenges",
                                          "verdict_survived", "surviving_bear_case"} <= set(rt)):
            err(f, "status FINAL requires a complete red_team block")
    # --- analyst blocks (added 2026-08-29, method section 7). Optional here so the UI
    # fixture and pre-amendment dives still validate; `tools/check_analyst.py` is the
    # gate that REQUIRES them on any dive written from now on. Shape and the veto are
    # enforced here whenever the blocks are present, because a malformed grade that
    # validates is worse than an absent one.
    eq = d.get("earnings_quality")
    if eq is not None:
        if not isinstance(eq, dict) or "grade" not in eq:
            err(f, "earnings_quality requires a grade (A-D or null)")
        else:
            if eq["grade"] not in {"A", "B", "C", "D", None}:
                err(f, f"earnings_quality.grade {eq['grade']!r} not in A-D or null")
            if eq["grade"] == "D" and d["verdict"] == "INVESTABLE":
                err(f, "earnings grade D forbids INVESTABLE (method section 7)")
            if eq["grade"] in {"C", None} and d["verdict"] == "INVESTABLE":
                err(f, "earnings grade C (or an uncomputable grade) caps the verdict "
                       "at WATCH, found INVESTABLE")
    gap = d.get("expectations_gap")
    if gap is not None:
        if not isinstance(gap, dict) or not isinstance(gap.get("rows"), list):
            err(f, "expectations_gap requires rows[]")
        elif not str(gap.get("market_implied_source") or "").strip():
            err(f, "expectations_gap.market_implied_source missing — the implied column "
                   "is read from data/market/<T>.json quality.reverse_dcf, never authored")
    it = d.get("independence_test")
    if it is not None and not (isinstance(it, dict) and {
            "largest_disagreement", "why_the_gap_exists", "falsification"} <= set(it)):
        err(f, "independence_test requires largest_disagreement, why_the_gap_exists "
               "and falsification")
    if not (DATA / "chains" / f"{d['chain_id']}.json").exists():
        err(f, f"chain_id {d['chain_id']} has no chain file")
    mk = market_path(DATA, d["ticker"])
    if not mk.exists() and not d.get("fixture"):
        warn(f, f"no market file for {d['ticker']} (chart will show request state)")
    check_common(f, d)


# ---------------------------------------------------------------- campaign foundation
def v_mapping(f: Path) -> None:
    """Normalized issuer census and many-to-many chain placements."""
    obj = load(f)
    if obj is None:
        return
    for finding in validate_mapping(ROOT, f, obj):
        err(f, finding)


def v_company(f: Path) -> None:
    """Reusable medium-depth issuer profile. Never a Stocky verdict."""
    obj = load(f)
    if obj is None:
        return
    for finding in validate_profile(ROOT, f, obj):
        err(f, finding)


def v_campaign(f: Path) -> None:
    """Ten-theme campaign manifest and its computed completion counts."""
    obj = load(f)
    if obj is None:
        return
    for finding in validate_campaign(ROOT, f, obj):
        err(f, finding)


def v_review(f: Path) -> None:
    """Cass's advisory review of a change to the research machine."""
    obj = load(f)
    if obj is None:
        return
    if not isinstance(obj, dict):
        err(f, "review must be a JSON object")
        return

    required = (
        "id", "as_of", "target", "reviewed_by", "behavior_change", "checks_touched",
        "cost_if_wrong", "how_you_would_know", "challenges", "verdict",
        "surviving_objection", "resolution", "changelog",
    )
    if not need(f, obj, list(required)):
        return

    for removed in ("actor", "ruling"):
        if removed in obj:
            err(f, f"{removed} is not part of an advisory review")

    review_id = obj.get("id")
    id_valid = bool(re.fullmatch(r"REV-\d{8}-\d{2}", str(review_id or "")))
    if not id_valid:
        err(f, f"id {review_id!r} is not REV-YYYYMMDD-NN")
    elif f.stem != review_id:
        err(f, f"filename {f.stem!r} must match id {review_id!r}")

    if check_date(f, obj.get("as_of"), "as_of") and id_valid:
        id_date = f"{review_id[4:8]}-{review_id[8:10]}-{review_id[10:12]}"
        if obj["as_of"] != id_date:
            err(f, f"as_of {obj['as_of']!r} does not match id date {id_date}")

    if obj.get("reviewed_by") != "cass-adversary":
        err(f, f"reviewed_by must be 'cass-adversary', found {obj.get('reviewed_by')!r}")

    for key in ("target", "behavior_change", "cost_if_wrong",
                "how_you_would_know", "surviving_objection"):
        if not isinstance(obj.get(key), str) or not obj[key].strip():
            err(f, f"{key} must be a non-empty string")

    checks = obj.get("checks_touched")
    if not isinstance(checks, list) or not all(
            isinstance(item, str) and item.strip() for item in checks):
        err(f, "checks_touched must be a list of non-empty strings; an empty list is allowed")

    check_enum(f, obj.get("verdict"), REVIEW_VERDICTS, "verdict")

    challenges = obj.get("challenges")
    if not isinstance(challenges, list) or len(challenges) < 3:
        err(f, "challenges must contain at least 3 distinct entries")
    else:
        normalized_claims = []
        for i, challenge in enumerate(challenges):
            ctx = f"challenges[{i}]"
            if not isinstance(challenge, dict) or not {
                    "claim", "attack", "survives"} <= set(challenge):
                err(f, f"{ctx} needs claim, attack, survives")
                continue
            for key in ("claim", "attack"):
                if not isinstance(challenge.get(key), str) or not challenge[key].strip():
                    err(f, f"{ctx}.{key} must be a non-empty string")
            if not isinstance(challenge.get("survives"), bool):
                err(f, f"{ctx}.survives must be true or false")
            if isinstance(challenge.get("claim"), str) and challenge["claim"].strip():
                normalized_claims.append(
                    re.sub(r"\s+", " ", challenge["claim"].strip()).casefold())
        if len(set(normalized_claims)) < 3:
            err(f, "challenges must contain at least 3 distinct claims")

    resolution = obj.get("resolution")
    if resolution is not None and (
            not isinstance(resolution, str) or not resolution.strip()):
        err(f, "resolution must be null or a non-empty string")

    changelog = obj.get("changelog")
    if not isinstance(changelog, list) or not changelog:
        err(f, "changelog must be a non-empty list")
    else:
        for i, entry in enumerate(changelog):
            if not isinstance(entry, dict) or not {"ts", "change"} <= set(entry):
                err(f, f"changelog[{i}] needs ts and change")
                continue
            for key in ("ts", "change"):
                if not isinstance(entry.get(key), str) or not entry[key].strip():
                    err(f, f"changelog[{i}].{key} must be a non-empty string")


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
        # The whole series, not rows[:5]. This file is the price plane every chart, entry
        # zone and reverse-DCF reads from; a malformed or out-of-order row at index 400 is
        # exactly the kind of thing that renders as a plausible line on a chart. 553 rows
        # is nothing to scan.
        bad_shape = [i for i, r in enumerate(rows)
                     if not (isinstance(r, list) and len(r) == 2)]
        if bad_shape:
            err(f, f"series.rows must be [date, close] pairs; {len(bad_shape)} malformed, "
                   f"first at index {bad_shape[0]}")
        else:
            bad_date = [i for i, r in enumerate(rows) if not DATE_RE.match(str(r[0]))]
            if bad_date:
                err(f, f"series.rows: {len(bad_date)} row(s) have a non-YYYY-MM-DD date, "
                       f"first at index {bad_date[0]} ({rows[bad_date[0]][0]!r})")
            bad_close = [i for i, r in enumerate(rows)
                         if not isinstance(r[1], (int, float)) or r[1] <= 0
                         or r[1] != r[1]]  # NaN is the only value unequal to itself
            if bad_close:
                err(f, f"series.rows: {len(bad_close)} close(s) are not a positive number, "
                       f"first at index {bad_close[0]} ({rows[bad_close[0]][1]!r})")
            unordered = [i for i in range(1, len(rows)) if str(rows[i][0]) <= str(rows[i - 1][0])]
            if unordered:
                err(f, f"series.rows are not strictly ascending by date: {len(unordered)} "
                       f"break(s), first at index {unordered[0]} "
                       f"({rows[unordered[0] - 1][0]} then {rows[unordered[0]][0]})")
    # Staleness, method section 9. WARN by design: the fetch plane runs weekdays and the
    # market is shut at weekends, so an error here would brick a Saturday postlude over a
    # condition no session can fix. The hard gate binds on the dive being written, in
    # check_analyst.py, where a stale price actually changes a verdict.
    for label, when, limit in (
            ("fetched_at", m.get("fetched_at"), STALE_MARKET_DAYS),
            ("series.as_of", (series or {}).get("as_of"), STALE_MARKET_DAYS),
            ("pcs.as_of", (m.get("pcs") or {}).get("as_of"), STALE_PCS_DAYS),
            ("fundamentals.as_of", (m.get("fundamentals") or {}).get("as_of"),
             STALE_FUNDAMENTALS_DAYS)):
        age = _age_days(when)
        if age is not None and age > limit:
            warn(f, f"{label} is {age}d old (>{limit}d): refresh before it is used in a "
                    f"verdict (method section 9)")
    if m["price_status"] == "DISPUTED":
        warn(f, "price_status DISPUTED: the two sources disagree beyond tolerance, so no "
                "number here may be used in a verdict without saying so")


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
        if req.get("kind") in {"prices", "fundamentals", "pcs", "edgar_doc",
                               "quality", "insider"} and not req.get("ticker"):
            err(f, f"{rid}: kind {req.get('kind')} needs a ticker")
        if req.get("kind") == "edgar_fts" and not req.get("query"):
            err(f, f"{rid}: edgar_fts needs a query")
        if req.get("kind") == "web_doc" and not str(req.get("url") or "").startswith(("http://", "https://")):
            err(f, f"{rid}: web_doc needs an http(s) url")


def v_shadow(f: Path) -> None:
    s = load(f)
    if s is None:
        return
    if f.name == "book.json":
        for row in s.get("rows", []):
            if not {"id", "ticker", "origin", "verdict_date", "spot", "review_at"} <= set(row):
                err(f, f"shadow row {row.get('id', '?')} incomplete")


def v_calendar(f: Path) -> None:
    c = load(f)
    if c is None:
        return
    if not need(f, c, ["as_of", "events"]):
        return
    events = c["events"]
    if not isinstance(events, list):
        err(f, "events must be a list")
        return
    if len([e for e in events if isinstance(e, dict) and e.get("status") == "WATCHING"]) > 60:
        warn(f, "more than 60 WATCHING events; sweep should prune (attention + page-size hygiene)")
    ids = set()
    for e in events:
        eid = e.get("id", "?") if isinstance(e, dict) else "?"
        ctx = f"event {eid}"
        if not isinstance(e, dict) or not need(f, e, ["id", "date", "title", "kind",
                                                      "why_it_matters", "source_name",
                                                      "source_date", "status", "added_by",
                                                      "changelog"], ctx):
            continue
        if not re.match(r"^EVT-\d{8}-\d{2}$", e["id"]):
            err(f, f"{ctx}: id must match EVT-YYYYMMDD-NN")
        if e["id"] in ids:
            err(f, f"duplicate event id {e['id']}")
        ids.add(e["id"])
        check_date(f, e["date"], f"{ctx}.date")
        check_enum(f, e["kind"], CAL_KINDS, f"{ctx}.kind")
        check_enum(f, e["status"], CAL_STATUS, f"{ctx}.status")
        if not e["why_it_matters"] or not e["source_name"]:
            err(f, f"{ctx}: why_it_matters and source_name must be non-empty")
        if e["status"] == "PROMOTED":
            sid = e.get("promoted_signal_id")
            if not sid:
                err(f, f"{ctx}: PROMOTED requires promoted_signal_id")
            elif not (DATA / "signals" / f"{sid}.json").exists():
                err(f, f"{ctx}: promoted_signal_id {sid} has no signal file")
        if e["status"] == "WATCHING" and isinstance(e.get("date"), str) and e["date"] < TODAY:
            warn(f, f"{ctx}: WATCHING but date passed; sweep should mark PASSED")
        check_mini_changelog(f, e["changelog"], ctx)


def v_candidates(f: Path) -> None:
    c = load(f)
    if c is None:
        return
    if not need(f, c, ["as_of", "candidates"]):
        return
    cands = c["candidates"]
    if not isinstance(cands, list):
        err(f, "candidates must be a list")
        return
    if len([x for x in cands if isinstance(x, dict) and x.get("status") == "AMBIENT"]) > 40:
        warn(f, "more than 40 AMBIENT candidates; radar should dismiss or expire (attention hygiene)")
    ids = set()
    for x in cands:
        cid = x.get("id", "?") if isinstance(x, dict) else "?"
        ctx = f"candidate {cid}"
        if not isinstance(x, dict) or not need(f, x, ["id", "date", "title", "family",
                                                      "source_name", "why", "status",
                                                      "added_by", "changelog"], ctx):
            continue
        if not re.match(r"^CAND-\d{8}-\d{2}$", x["id"]):
            err(f, f"{ctx}: id must match CAND-YYYYMMDD-NN")
        if x["id"] in ids:
            err(f, f"duplicate candidate id {x['id']}")
        ids.add(x["id"])
        check_date(f, x["date"], f"{ctx}.date")
        check_enum(f, x["family"], CAND_FAMILIES, f"{ctx}.family")
        check_enum(f, x["status"], CAND_STATUS, f"{ctx}.status")
        if not x["why"] or not x["source_name"]:
            err(f, f"{ctx}: why and source_name must be non-empty")
        if x["status"] == "PROMOTED":
            sid = x.get("promoted_signal_id")
            if not sid:
                err(f, f"{ctx}: PROMOTED requires promoted_signal_id")
            elif not (DATA / "signals" / f"{sid}.json").exists():
                err(f, f"{ctx}: promoted_signal_id {sid} has no signal file")
        # Optional join key back to the feed store. It is snapshotted rather than looked up
        # because the feed store prunes at 500 items and the id alone would dangle.
        if "first_feed_ts" in x:
            check_date(f, x["first_feed_ts"], f"{ctx}.first_feed_ts")
        if "feed_source" in x and not x["feed_source"]:
            err(f, f"{ctx}: feed_source present but empty")
        check_mini_changelog(f, x["changelog"], ctx)


def v_scout_log(f: Path) -> None:
    """Nell's log: her calibration plus her judgment fields.

    The calibration half is written by tools/scout_calibrate.py; the judgment half by the
    agent. The denominators check is the point of this validator: a calibration block with
    no denominators is a report that cannot fail (Rule 21).
    """
    d = load(f)
    if d is None:
        return
    if not need(f, d, ["as_of", "calibration", "proposed_rules", "spot_tests",
                       "repairs", "changelog"]):
        return
    check_date(f, d["as_of"], "as_of")
    check_common(f, d)

    cal = d["calibration"]
    if not isinstance(cal, dict):
        err(f, "calibration must be an object")
    elif not need(f, cal, ["generated_at", "conversion", "denominators"], "calibration"):
        pass
    else:
        dens = cal["denominators"]
        if not isinstance(dens, dict) or not dens:
            err(f, "calibration.denominators missing: a count with no denominator cannot fail")
        else:
            for k in ("signals_examined", "candidates_examined"):
                if k not in dens:
                    err(f, f"calibration.denominators missing {k}")
        for i, lat in enumerate(cal.get("latency") or []):
            if not isinstance(lat, dict) or "days_late" not in lat or "signal_id" not in lat:
                err(f, f"calibration.latency[{i}] needs signal_id + days_late")
        for i, u in enumerate(cal.get("latency_unmatched") or []):
            if not isinstance(u, dict) or not u.get("reason"):
                err(f, f"calibration.latency_unmatched[{i}] must name its reason")

    for i, r in enumerate(d["proposed_rules"]):
        ctx = f"proposed_rules[{i}]"
        if not isinstance(r, dict) or not need(f, r, ["id", "pattern", "origin", "status",
                                                      "occurrences", "evidence"], ctx):
            continue
        check_enum(f, r["origin"], RULE_ORIGINS, f"{ctx}.origin")
        check_enum(f, r["status"], RULE_STATUS, f"{ctx}.status")
        if not isinstance(r["evidence"], list) or not r["evidence"]:
            err(f, f"{ctx}: a rule needs at least one evidence item with a quote and a date")
        # The two hardening bars, enforced not remembered (data/taste.md).
        if r["status"] == "HARDENED":
            if r["origin"] == "PREFERENCE" and (r.get("occurrences") or 0) < 2:
                err(f, f"{ctx}: PREFERENCE-origin rules need 2 occurrences to harden, has "
                       f"{r.get('occurrences')}")
            if not r.get("taste_ref"):
                err(f, f"{ctx}: HARDENED requires taste_ref naming its data/taste.md rule")

    for i, t in enumerate(d["spot_tests"]):
        ctx = f"spot_tests[{i}]"
        if not isinstance(t, dict) or not need(f, t, ["ts", "rule", "verdict"], ctx):
            continue
        check_enum(f, t["verdict"], {"PASS", "FAIL"}, f"{ctx}.verdict")

    for i, r in enumerate(d["repairs"]):
        ctx = f"repairs[{i}]"
        if not isinstance(r, dict) or not need(f, r, ["ts", "finding", "action",
                                                      "prevention", "escalated"], ctx):
            continue
        if not r["prevention"]:
            err(f, f"{ctx}: a repair with no prevention is a backfill, not a fix")

    # Two consecutive FAILs on one rule mean the rule is in the wrong place (v8 lesson).
    seq: dict[str, int] = {}
    for t in d["spot_tests"]:
        if not isinstance(t, dict):
            continue
        rule = t.get("rule")
        seq[rule] = seq.get(rule, 0) + 1 if t.get("verdict") == "FAIL" else 0
        if seq[rule] >= 2:
            warn(f, f"rule {rule!r} has failed 2 consecutive spot tests: relocate it or gate "
                    f"it deterministically, do not re-teach it")


def v_dive_log(f: Path) -> None:
    """Stocky's log: his four calibration channels. Same point as Nell's and Atlas's,
    aimed at the one thing only he produces: a verdict that can turn out to be wrong."""
    d = load(f)
    if d is None:
        return
    if not need(f, d, ["as_of", "calibration", "repairs", "changelog"]):
        return
    check_date(f, d["as_of"], "as_of")
    cal = d.get("calibration")
    if isinstance(cal, dict):
        for ch in ("shadow_book", "entry_zones", "red_team_amendments", "implied_growth"):
            if ch not in cal:
                err(f, f"calibration is missing the {ch} channel (method section 8)")
            elif not isinstance(cal[ch], dict) or "denominator" not in cal[ch]:
                err(f, f"calibration.{ch} needs a denominator — a rate with no "
                       "denominator is a report that cannot fail")
    check_common(f, d)


def v_map_log(f: Path) -> None:
    """Atlas's log: his calibration plus his judgment fields. Same split as v_scout_log,
    and the same point: a calibration with no denominators is a report that cannot fail."""
    d = load(f)
    if d is None:
        return
    if not need(f, d, ["as_of", "calibration", "archetypes", "spot_tests", "repairs",
                       "changelog"]):
        return
    check_date(f, d["as_of"], "as_of")
    check_common(f, d)

    cal = d["calibration"]
    if not isinstance(cal, dict):
        err(f, "calibration must be an object")
    elif need(f, cal, ["generated_at", "link_yield", "denominators"], "calibration"):
        dens = cal["denominators"]
        if not isinstance(dens, dict) or not dens:
            err(f, "calibration.denominators missing: a count with no denominator cannot fail")
        else:
            for k in ("chains_examined", "links_examined"):
                if k not in dens:
                    err(f, f"calibration.denominators missing {k}")
        ly = cal["link_yield"]
        if isinstance(ly, dict) and "links_total" not in ly:
            err(f, "calibration.link_yield missing links_total: yield without its denominator "
                   "is unreadable")

    for i, a in enumerate(d["archetypes"]):
        ctx = f"archetypes[{i}]"
        if not isinstance(a, dict) or not need(f, a, ["id", "pattern", "origin", "status",
                                                      "occurrences", "evidence"], ctx):
            continue
        check_enum(f, a["origin"], RULE_ORIGINS, f"{ctx}.origin")
        check_enum(f, a["status"], RULE_STATUS, f"{ctx}.status")
        if not isinstance(a["evidence"], list) or not a["evidence"]:
            err(f, f"{ctx}: an archetype needs at least one evidence item naming the links "
                   f"that established it")
        # the same two hardening bars as data/taste.md, enforced not remembered
        if a["status"] == "HARDENED":
            if a["origin"] == "PREFERENCE" and (a.get("occurrences") or 0) < 2:
                err(f, f"{ctx}: PREFERENCE-origin archetypes need 2 occurrences to harden, "
                       f"has {a.get('occurrences')}")
            if not a.get("taste_ref"):
                err(f, f"{ctx}: HARDENED requires taste_ref naming its _archetypes.md entry")

    for i, s in enumerate(d["spot_tests"]):
        ctx = f"spot_tests[{i}]"
        if isinstance(s, dict) and need(f, s, ["ts", "rule", "verdict"], ctx):
            check_enum(f, s["verdict"], {"PASS", "FAIL"}, f"{ctx}.verdict")
    for i, r in enumerate(d["repairs"]):
        ctx = f"repairs[{i}]"
        if not isinstance(r, dict) or not need(f, r, ["ts", "finding", "action", "prevention",
                                                      "escalated"], ctx):
            continue
        if not r["prevention"]:
            err(f, f"{ctx}: a repair with no prevention is a backfill, not a fix")

    seq: dict = {}
    for s in d["spot_tests"]:
        if not isinstance(s, dict):
            continue
        rule = s.get("rule")
        seq[rule] = seq.get(rule, 0) + 1 if s.get("verdict") == "FAIL" else 0
        if seq[rule] >= 2:
            warn(f, f"archetype {rule!r} has failed 2 consecutive spot tests: relocate it or "
                    f"gate it deterministically, do not re-teach it")


def v_feeds(f: Path) -> None:
    """Actions-owned feed store: lenient by design. A feed hiccup must never fail the repo."""
    d = load(f)
    if d is None:
        return
    if "as_of" not in d or "items" not in d:
        warn(f, "feed store missing as_of/items (Actions writer will overwrite)")
        return
    items = d["items"]
    if not isinstance(items, list):
        warn(f, "feed items not a list")
        return
    if len(items) > 500:
        warn(f, f"feed store holds {len(items)} items, above the 500 cap; fetcher should prune")
    for i, it in enumerate(items[:20]):
        if not isinstance(it, dict) or not {"id", "source", "family", "ts", "title"} <= set(it):
            warn(f, f"items[{i}] malformed (needs id, source, family, ts, title)")
            break


def v_edgar_doc(f: Path) -> None:
    d = load(f)
    if d is None:
        return
    if not need(f, d, ["ticker", "cik", "accession", "url", "filing_date", "form", "text"]):
        return
    check_date(f, d.get("filing_date"), "filing_date")
    if not str(d.get("url") or "").startswith("https://www.sec.gov/"):
        err(f, f"url {str(d.get('url'))[:60]!r} is not an sec.gov Archives URL")
    if not re.match(r"^\d{10}-\d{2}-\d{6}$", str(d.get("accession") or "")):
        err(f, f"accession {d.get('accession')!r} is not in EDGAR 0000000000-00-000000 form")
    text = d.get("text")
    if not isinstance(text, str) or len(text) < 500:
        err(f, f"text is {len(text) if isinstance(text, str) else 'not a string'}: too short "
               f"to verify a quote against — refetch rather than quote from it")
    elif d.get("chars") is not None and d["chars"] != len(text):
        err(f, f"chars says {d['chars']} but text is {len(text)} long")
    if f.stem != str(d.get("ticker")).replace(".", "-"):
        err(f, f"filename {f.stem} does not match ticker {d.get('ticker')!r}: a quote "
               f"verifier looks this file up BY ticker and would check the wrong document")



def v_edgar_fts(f: Path) -> None:
    d = load(f)
    if d is None:
        return
    if not need(f, d, ["query", "hits"]):
        return
    hits = d.get("hits")
    if not isinstance(hits, list):
        err(f, "hits must be a list")
        return
    nourl = 0
    for i, h in enumerate(hits):
        if not isinstance(h, dict):
            err(f, f"hits[{i}] is not an object")
            continue
        if not h.get("accession"):
            err(f, f"hits[{i}] carries no accession")
        if not h.get("url"):
            nourl += 1
    if nourl:
        # WARN, not error: the seed file was fetched before hits carried a url. New files
        # get one from the fetch plane (2026-08-29), so this number should go to zero and
        # stay there.
        warn(f, f"{nourl} of {len(hits)} FTS hit(s) carry no url: a hit with no link is a "
                f"claim nobody can open. Refetch to backfill")



def v_indicators(f: Path) -> None:
    d = load(f)
    if d is None:
        return
    trips = d.get("trips")
    if not isinstance(trips, list):
        err(f, "indicators.json needs a trips list")
        return
    for i, t in enumerate(trips):
        if not isinstance(t, dict) or not {"chain", "scenario", "indicator"} <= set(t):
            err(f, f"trips[{i}] needs chain, scenario, indicator")



def v_shadow_results(f: Path) -> None:
    d = load(f)
    if d is None:
        return
    if not isinstance(d, dict):
        err(f, "shadow results must be an object keyed by shadow row id")
        return
    book = load(DATA / "shadow" / "book.json") if (DATA / "shadow" / "book.json").exists() else None
    ids = {r.get("id") for r in (book or {}).get("rows", [])} if isinstance(book, dict) else set()
    for k, v in d.items():
        if ids and k not in ids:
            err(f, f"result {k!r} grades a shadow row that does not exist in book.json")
        if not isinstance(v, dict):
            err(f, f"result {k!r} is not an object")



def v_digest(f: Path) -> None:
    d = load(f)
    if d is None:
        return
    if not need(f, d, ["week", "summary", "ranked"]):
        return
    if not isinstance(d.get("ranked"), list):
        err(f, "ranked must be a list")
    # Ron, 2026-09-01: the digest answers "where should I invest" first. A `verdicts`
    # section leads: every FINAL dive with its verdict, clock and entry zone or triggers,
    # then the O1 queue, then what blocks the rest, one line each. Warned for the first
    # Saturday it applies to, refused from the second, so the routine has one fire to adapt.
    week = str(d.get("week") or "")
    if "verdicts" not in d:
        (err if week >= "2026-37" else warn)(
            f, "no verdicts section: a digest that ranks mid-funnel objects and never says "
               "which names carry a verdict does not answer the question the machine exists "
               "for (required from ISO week 2026-37)")
    elif not isinstance(d.get("verdicts"), dict):
        err(f, "verdicts must be an object with named, o1_queue and blocked lists")
    else:
        for k in ("named", "o1_queue", "blocked"):
            if not isinstance(d["verdicts"].get(k), list):
                err(f, f"verdicts.{k} must be a list (empty is a real answer, absent is not)")
    # Campaign-era digests must name their writer and carry Adam's machine sweep. Warn-only
    # let a digest omit both and still exit zero, which reads as a healthy machine.
    if not d.get("generated_by"):
        err(f, "no generated_by: every generated store names its writer")
    m = d.get("machine")
    if m is None:
        err(f, "no machine section: Adam's sweep rides in the digest, and a digest without "
               "it is an unaudited machine that reads like a healthy one")
    elif not isinstance(m, dict):
        err(f, "machine must be an object")
    else:
        for k in ("as_of", "examined", "findings_by_category", "promise_ledger_backlog",
                  "changed_since_last_week", "next_action", "v8_reachable"):
            if k not in m:
                err(f, f"machine missing {k}")
        if isinstance(m.get("examined"), dict) and not m["examined"]:
            err(f, "machine.examined must be a non-empty object of denominators")
        if isinstance(m.get("findings_by_category"), dict) and not m["findings_by_category"]:
            err(f, "machine.findings_by_category must be a non-empty object")
        if isinstance(m.get("promise_ledger_backlog"), str) and not m["promise_ledger_backlog"].strip():
            err(f, "machine.promise_ledger_backlog must be a non-empty string")
        if isinstance(m.get("changed_since_last_week"), str) and not m["changed_since_last_week"].strip():
            err(f, "machine.changed_since_last_week must be a non-empty string")
        if isinstance(m.get("next_action"), str) and not m["next_action"].strip():
            err(f, "machine.next_action must name one next action")
        if "v8_reachable" in m and not isinstance(m["v8_reachable"], bool):
            err(f, "machine.v8_reachable must be a boolean: whether the v8 tree could be read "
                   "is a scope boundary and scope boundaries are findings, not footnotes")
        # The deferred-work backlog triage rides in the machine block. Additive and optional while
        # it is young (gates-not-promises), but shape-checked when present so a malformed block
        # cannot read as a groomed backlog.
        db = m.get("deferred_backlog")
        if db is not None:
            if not isinstance(db, dict):
                err(f, "machine.deferred_backlog must be an object with an open count and buckets")
            else:
                op = db.get("open")
                if not isinstance(op, int) or isinstance(op, bool) or op < 0:
                    err(f, "machine.deferred_backlog.open must be a non-negative integer: the "
                           "count of OPEN rows in tasks/backlog.md, a denominator not a footnote")
                if "buckets" not in db or not isinstance(db["buckets"], (list, dict)):
                    err(f, "machine.deferred_backlog.buckets must be a list or object (empty is "
                           "allowed, absence is not): the OPEN rows grouped by concept")
        # Legacy total-only field is allowed beside findings_by_category but not instead of it.
        if isinstance(m.get("findings"), int) and not isinstance(m.get("findings_by_category"), dict):
            err(f, "machine.findings is a bare count with no machine.findings_by_category "
                   "breakdown beside it")
        if isinstance(m.get("findings_by_category"), dict) and isinstance(m.get("examined"), dict):
            total = sum(v for v in m["findings_by_category"].values() if isinstance(v, int))
            if isinstance(m.get("findings"), int) and total != m["findings"]:
                err(f, "machine.findings disagrees with the sum of machine.findings_by_category")



def v_cross_file_ciks() -> None:
    """One CIK per ticker across market, edgar docs and screens, int-normalized.

    A string CIK and an int CIK are the same filer; transposed digits are a different
    filer, or none. VRT carried 1674910 in a screen row against 1674101 in every other
    file, and nothing compared them, so an EDGAR request keyed on that row would have
    resolved to the wrong company or silently to nothing.
    """
    seen: dict[str, list[tuple[int, Path]]] = {}

    def note(ticker, cik, path):
        if ticker in (None, "") or cik in (None, ""):
            return
        try:
            seen.setdefault(str(ticker), []).append((int(str(cik).strip()), path))
        except (TypeError, ValueError):
            err(path, f"cik {cik!r} for {ticker} is not a number")

    for p in sorted((DATA / "market").glob("*.json")):
        m = load(p)
        if isinstance(m, dict):
            note(m.get("ticker"), m.get("cik"), p)
    for p in sorted((DATA / "edgar" / "docs").glob("*.json")) if (DATA / "edgar" / "docs").is_dir() else []:
        d = load(p)
        if isinstance(d, dict):
            note(d.get("ticker"), d.get("cik"), p)
    for p in sorted((DATA / "screens").glob("*.json")):
        s = load(p)
        if not isinstance(s, dict):
            continue
        for rows in (s.get("buckets") or {}).values():
            for r in rows or []:
                if isinstance(r, dict):
                    note(r.get("ticker"), r.get("cik"), p)
    for ticker, entries in seen.items():
        distinct = {c for c, _ in entries}
        if len(distinct) > 1:
            where = ", ".join(f"{c} in {p.relative_to(ROOT)}" for c, p in entries)
            err(entries[0][1], f"{ticker} has {len(distinct)} different CIKs across files: "
                               f"{where}. One of them points at the wrong filer")



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


# ---- themes: the occurrence log and its clustering

OCCURRENCE_ORIGINS = {"feed", "candidate", "signal", "calendar"}


def v_occurrence_log(f: Path) -> None:
    """data/themes/occurrences.json. Append-only, one row per occurrence ever seen.

    Deliberately does NOT require origin_ref to resolve. The whole reason this store
    exists is that the things it references go away: the feed store prunes at 500 items
    over 14 days, candidates get dismissed, ids are content hashes. A log of what the
    machine saw whose rows vanished when the referent did would be no log at all.
    """
    obj = load(f)
    if obj is None:
        return
    need(f, obj, ["as_of", "occurrences", "changelog"], "occurrence log")
    check_date(f, obj.get("as_of"), "as_of")
    rows = obj.get("occurrences")
    if not isinstance(rows, list):
        err(f, "occurrences must be a list")
        return
    ids, keys = set(), set()
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            err(f, f"occurrences[{i}] is not an object")
            continue
        rid = r.get("id")
        ctx = f"occurrences[{i}] ({rid})"
        need(f, r, ["id", "origin", "origin_ref", "title", "first_seen"], ctx)
        if not isinstance(rid, str) or not re.fullmatch(r"OCC-\d{8}-\d{3,}", str(rid or "")):
            err(f, f"{ctx}: id must look like OCC-YYYYMMDD-NNN")
        elif rid in ids:
            err(f, f"{ctx}: duplicate occurrence id")
        else:
            ids.add(rid)
        check_enum(f, r.get("origin"), OCCURRENCE_ORIGINS, f"{ctx} origin")
        key = (r.get("origin"), r.get("origin_ref"))
        if key in keys:
            err(f, f"{ctx}: a second row for {key}. One occurrence is one row, or every "
                   f"count over this store double-counts")
        keys.add(key)
        check_date(f, r.get("first_seen"), f"{ctx} first_seen")
        if r.get("ts"):
            check_date(f, r.get("ts"), f"{ctx} ts")
        if r.get("theme_id") and not str(r.get("theme_basis") or "").strip():
            err(f, f"{ctx}: assigned to {r['theme_id']} with no theme_basis. A tag with no "
                   f"stated basis is the same defect class as an invented number")
    check_mini_changelog(f, obj.get("changelog"), "changelog")


def v_themes(f: Path) -> None:
    """data/themes/themes.json. Tally's clusters and the machine half beneath them."""
    obj = load(f)
    if obj is None:
        return
    need(f, obj, ["as_of", "themes", "calibration", "changelog"], "themes")
    check_date(f, obj.get("as_of"), "as_of")
    themes = obj.get("themes")
    if not isinstance(themes, list):
        err(f, "themes must be a list")
        return
    seen = set()
    for i, t in enumerate(themes):
        if not isinstance(t, dict):
            err(f, f"themes[{i}] is not an object")
            continue
        tid = t.get("id")
        ctx = f"themes[{i}] ({tid})"
        need(f, t, ["id", "label", "definition", "created_at", "match", "changelog"], ctx)
        if not re.fullmatch(r"THM-\d{2,}", str(tid or "")):
            err(f, f"{ctx}: id must look like THM-NN")
        elif tid in seen:
            err(f, f"{ctx}: duplicate theme id")
        else:
            seen.add(tid)
        if not str(t.get("definition") or "").strip():
            err(f, f"{ctx}: empty definition. Without one nobody can say whether the next "
                   f"occurrence belongs in it, and the cluster stops being falsifiable")
        m = t.get("match")
        if not isinstance(m, dict):
            err(f, f"{ctx}: match must be an object of term lists")
        else:
            for k in ("any", "all", "not"):
                if k in m and not isinstance(m[k], list):
                    err(f, f"{ctx}: match.{k} must be a list")
        check_mini_changelog(f, t.get("changelog"), f"{ctx} changelog")
    cal = obj.get("calibration")
    if not isinstance(cal, dict):
        err(f, "calibration must be an object written by tools/theme_calibrate.py")
        return
    need(f, cal, ["generated_at", "denominators", "per_theme", "surges", "weeks"],
         "calibration")
    for tid in (cal.get("per_theme") or {}):
        if tid not in seen:
            err(f, f"calibration.per_theme names {tid}, which is not a theme in this file")
    check_mini_changelog(f, obj.get("changelog"), "changelog")


def v_harness(f: Path) -> None:
    """Hitch's weekly harness report (`run harness`). Its rules live in check_harness.py, a
    file a `run harness fix` may not edit."""
    obj = load(f)
    if obj is None:
        return
    for msg in harness_report_errors(obj, f.stem):
        err(f, msg)


def main() -> int:
    counts = {}
    plans = [
        ("signals", DATA / "signals", "*.json", v_signal),
        ("impact", DATA / "impact", "*.json", v_impact),
        ("chains", DATA / "chains", "*.json", v_chain),
        ("mappings", DATA / "mappings", "*.json", v_mapping),
        ("companies", DATA / "companies", "*.json", v_company),
        ("campaigns", DATA / "campaigns", "CAMP-*.json", v_campaign),
        ("reviews", DATA / "reviews", "REV-*.json", v_review),
        ("harness", DATA / "harness", "20*-W*.json", v_harness),
        ("screens", DATA / "screens", "*.json", v_screen),
        ("stocks", DATA / "stocks", "*.json", v_stock),
        ("market", DATA / "market", "*.json", v_market),
        ("shadow", DATA / "shadow", "*.json", v_shadow),
    ]
    for name, folder, glob, fn in plans:
        files = sorted(folder.glob(glob)) if folder.exists() else []
        # underscore-prefixed files are agent stores (_map-log.json), never analysis objects
        files = [f for f in files if not f.name.startswith("_")]
        counts[name] = len(files)
        for f in files:
            fn(f)
    mapping_records = []
    mapping_dir = DATA / "mappings"
    for path in sorted(mapping_dir.glob("*.json")) if mapping_dir.is_dir() else []:
        if path.name.startswith("_"):
            continue
        obj = load(path)
        if isinstance(obj, dict):
            mapping_records.append((path, obj))
    for finding in corpus_identity_failures(mapping_records):
        err(mapping_records[0][0], finding)
    if (DATA / "requests.json").exists():
        v_requests(DATA / "requests.json")
    if (DATA / "trades.jsonl").exists():
        v_trades(DATA / "trades.jsonl")
    if (DATA / "calendar" / "events.json").exists():
        counts["calendar"] = 1
        v_calendar(DATA / "calendar" / "events.json")
    if (DATA / "radar" / "candidates.json").exists():
        counts["candidates"] = 1
        v_candidates(DATA / "radar" / "candidates.json")
    if (DATA / "stocks" / "_dive-log.json").exists():
        counts["dive-log"] = 1
        v_dive_log(DATA / "stocks" / "_dive-log.json")
    if (DATA / "chains" / "_map-log.json").exists():
        counts["map-log"] = 1
        v_map_log(DATA / "chains" / "_map-log.json")
    if (DATA / "impact" / "_rank-log.json").exists():
        counts["rank-log"] = 1
        v_rank_log(DATA / "impact" / "_rank-log.json")
    if (DATA / "themes" / "occurrences.json").exists():
        counts["occurrences"] = 1
        v_occurrence_log(DATA / "themes" / "occurrences.json")
    if (DATA / "themes" / "themes.json").exists():
        counts["themes"] = 1
        v_themes(DATA / "themes" / "themes.json")
    if (DATA / "radar" / "scout-log.json").exists():
        counts["scout-log"] = 1
        v_scout_log(DATA / "radar" / "scout-log.json")
    if (DATA / "feeds" / "latest.json").exists():
        counts["feeds"] = 1
        v_feeds(DATA / "feeds" / "latest.json")
    # Stores that the fetch plane writes and the analyst or the UI reads. Validated from
    # 2026-08-29; before that every one of them could hold anything at all.
    for name, folder, fn in (("edgar-docs", DATA / "edgar" / "docs", v_edgar_doc),
                             ("edgar-fts", DATA / "edgar" / "fts", v_edgar_fts)):
        files = sorted(folder.glob("*.json")) if folder.is_dir() else []
        counts[name] = len(files)
        for f in files:
            fn(f)
    for path, fn in ((DATA / "indicators.json", v_indicators),
                     (DATA / "shadow" / "results.json", v_shadow_results)):
        if path.exists():
            fn(path)
    digests = sorted((DATA / "digest").glob("*.json")) if (DATA / "digest").is_dir() else []
    counts["digest"] = len(digests)
    for f in digests:
        v_digest(f)
    v_cross_file_ciks()

    summary = " · ".join(f"{k}: {v}" for k, v in counts.items())
    print(f"validate: {summary}")
    if _debt["no_date"] or _debt["no_url"]:
        # One line, every run, naming what is owed. Legacy evidence predating EVIDENCE_GATE
        # warns rather than failing, and a warning nobody counts is a warning nobody pays:
        # this number is the backlog, and it should only ever go down.
        print(f"validate: citation debt (pre-{EVIDENCE_GATE} evidence): "
              f"{_debt['no_date']} item(s) with no source_date, "
              f"{_debt['no_url']} with no url")
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
