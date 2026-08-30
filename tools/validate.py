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
PRICE_STATUS = {"AGREED", "SINGLE_SOURCE", "DISPUTED", "NO_DATA", "VERIFIED_ZERO"}
REQ_KINDS = {"prices", "fundamentals", "pcs", "edgar_fts", "edgar_doc",
             "quality", "insider"}   # quality/insider added 2026-08-29 (the analyst)
PROP_VERDICTS = {"ADOPT", "ADOPT_NARROWED", "REJECT"}
RULING_DECISIONS = {"KEEP", "REVERT", "NARROW"}

# Who may appear in a `by` field. Humans and roles, then the agents and scripts that write on
# their behalf. Shipped as a WARNING behind --strict-actors, following the --strict-citations
# precedent in check_chain.py: closing this enum today fails the repo, because ~25 rows in
# data/ already say atlas-cartographer, nell-scanner, scout_calibrate or map_calibrate. It
# hardens once those are backfilled to `<actor>/<agent-or-script>`.
ACTORS = {"ron", "yotam", "routine", "click"}
AGENTS = {"nell-scanner", "atlas-cartographer", "stocky", "cass-adversary",
          "scout_calibrate", "map_calibrate", "dive_calibrate", "fetch", "actions"}
_ACTOR_RE = re.compile(
    r"^(?P<actor>[a-z0-9_-]+)(?:/(?P<agent>[a-z0-9_-]+))?(?: \(session [A-Za-z0-9._-]{1,32}\))?$"
)
STRICT_ACTORS = "--strict-actors" in sys.argv
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
        else:
            check_actor(f, c.get("by"), f"{ctx}.changelog[{i}]")


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


def _score(heat: dict, key: str):
    """The numeric score under heat[key], or None when absent or unscored."""
    blk = (heat or {}).get(key)
    return blk.get("score") if isinstance(blk, dict) else None


def band_for(impact, crowdedness):
    """method 3 attention bands, first match wins. None when crowdedness is unscored."""
    if crowdedness is None:
        return None
    if crowdedness > 80:
        return "OVER_CROWDED"
    if crowdedness > 60:
        return "CROWDED"
    if crowdedness > 40:
        return "EMERGING"
    if impact is None:
        return None
    return "UNDISCOVERED" if impact >= 60 else "QUIET"


def check_common(f: Path, obj: dict) -> None:
    for k in ("confidence_audit", "changelog"):
        if k not in obj:
            err(f, f"missing {k}")
    if isinstance(obj.get("changelog"), list):
        for i, c in enumerate(obj["changelog"]):
            if not isinstance(c, dict) or not {"ts", "by", "change"} <= set(c):
                err(f, f"changelog[{i}] needs ts, by, change")
            else:
                check_actor(f, c.get("by"), f"changelog[{i}]")
    if isinstance(obj.get("notes"), list):
        for i, n in enumerate(obj["notes"]):
            if not isinstance(n, dict) or not {"ts", "by", "text"} <= set(n):
                err(f, f"notes[{i}] needs ts, by, text")
            else:
                check_actor(f, n.get("by"), f"notes[{i}]")


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
            imp, crd, cap = (_score(h, k) for k in ("impact", "crowdedness", "capture"))
            want = band_for(imp, crd)
            if want and h.get("verdict") is not None and h["verdict"] != want:
                err(f, f"{ctx}.heat.verdict is {h['verdict']!r} but impact {imp} / "
                       f"crowdedness {crd} computes {want} (method 3)")
            if None not in (imp, crd, cap):
                want_mc = imp >= 60 and crd <= 40 and cap >= 60
                if bool(h.get("money_corner")) != want_mc:
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


def check_actor(f: Path, value, ctx: str) -> None:
    """`by` must name a known actor, optionally the agent or script that acted for them.

    Warning by default, error under --strict-actors. See the ACTORS comment above for why.
    """
    if not isinstance(value, str) or not value.strip():
        err(f, f"{ctx}.by is empty")
        return
    m = _ACTOR_RE.match(value.strip())
    if not m:
        (err if STRICT_ACTORS else warn)(
            f, f"{ctx}.by = {value!r} is not `<actor>` or `<actor>/<agent>` "
               f"(optionally ` (session <id>)`)")
        return
    actor, agent = m.group("actor"), m.group("agent")
    if actor not in ACTORS:
        if actor in AGENTS and agent is None:
            # Legacy shape: the agent wrote it and nobody recorded whose session it was.
            (err if STRICT_ACTORS else warn)(
                f, f"{ctx}.by = {value!r} names an agent with no actor. Write "
                   f"`ron/{actor}` or `yotam/{actor}` so a change has a person behind it")
            return
        (err if STRICT_ACTORS else warn)(
            f, f"{ctx}.by = {value!r}: unknown actor {actor!r}. Known: "
               f"{', '.join(sorted(ACTORS))}")
        return
    if agent is not None and agent not in AGENTS:
        (err if STRICT_ACTORS else warn)(
            f, f"{ctx}.by = {value!r}: unknown agent {agent!r}")


def v_proposal(f: Path) -> None:
    """An adversary review of a change to the machine itself (`run devil`).

    A PROP is the receipt that an instruction change was attacked before it landed. The
    verdict is the adversary's; the `ruling` is Ron's and stays null until he makes it.
    """
    obj = load(f)
    if obj is None:
        return
    if not isinstance(obj, dict):
        err(f, "proposal must be a JSON object")
        return
    # Every one of these is an attack the contract mandates. CLAUDE.md says this stage is
    # machine-checked, so the four attack fields are REQUIRED, not decorative: without them a
    # PROP carrying a verdict and three placeholder challenges validated clean and satisfied
    # both the postlude gate and CI.
    for k in ("id", "as_of", "actor", "reviewed_by", "files", "verdict", "challenges",
              "behavior_change", "checks_touched", "cost_if_wrong", "how_you_would_know",
              "surviving_objection"):
        if k not in obj:
            err(f, f"missing required key: {k}")
    for k in ("behavior_change", "cost_if_wrong", "how_you_would_know"):
        if k in obj and not str(obj.get(k) or "").strip():
            err(f, f"{k} is empty. The contract's four attacks are the review; a blank one "
                   f"means the attack was not made")
    if "checks_touched" in obj and not isinstance(obj.get("checks_touched"), list):
        err(f, "checks_touched must be a list (empty is a legitimate answer, absent is not)")
    if obj.get("reviewed_by") != "cass-adversary":
        err(f, f"reviewed_by = {obj.get('reviewed_by')!r}: a proposal is a record that the "
               f"adversary reviewed the change, so this is always 'cass-adversary'")
    if not re.match(r"^PROP-\d{8}-\d{2}$", str(obj.get("id") or "")):
        err(f, f"id {obj.get('id')!r} is not PROP-YYYYMMDD-NN")
    check_date(f, obj.get("as_of"), "as_of")
    check_actor(f, obj.get("actor"), "proposal")

    files = obj.get("files")
    if not isinstance(files, list) or not files or not all(isinstance(x, str) and x for x in files):
        err(f, "files[] must be a non-empty list of repo-relative paths")

    if obj.get("verdict") not in PROP_VERDICTS:
        err(f, f"verdict {obj.get('verdict')!r} not in {sorted(PROP_VERDICTS)}")

    ch = obj.get("challenges")
    if not isinstance(ch, list) or len(ch) < 3:
        err(f, f"challenges[] needs >= 3 entries, has {len(ch) if isinstance(ch, list) else 0}. "
               f"A review that raised fewer than three objections did not attack anything")
    else:
        for i, c in enumerate(ch):
            if not isinstance(c, dict) or not {"claim", "attack", "survives"} <= set(c):
                err(f, f"challenges[{i}] needs claim, attack, survives")
                continue
            if not isinstance(c.get("survives"), bool):
                err(f, f"challenges[{i}].survives must be true/false, not {c.get('survives')!r}")
            for k in ("claim", "attack"):
                if not str(c.get(k) or "").strip():
                    err(f, f"challenges[{i}].{k} is empty")
        # Three copies of one objection is one objection. Caught because a PROP with three
        # identical placeholder challenges was demonstrated to validate clean.
        claims = [str(c.get("claim") or "").strip().lower() for c in ch if isinstance(c, dict)]
        distinct = {c for c in claims if c}
        if claims and len(distinct) < 3:
            err(f, f"challenges[] has {len(distinct)} distinct claim(s) across {len(claims)} "
                   f"entries. Repeating one objection is not three objections")

    if not str(obj.get("surviving_objection") or "").strip():
        err(f, "surviving_objection is empty. Even an ADOPT states what would make it wrong")

    ruling = obj.get("ruling")
    if ruling is not None:
        if not isinstance(ruling, dict) or not {"by", "ts", "decision"} <= set(ruling):
            err(f, "ruling needs by, ts, decision")
        else:
            if ruling.get("decision") not in RULING_DECISIONS:
                err(f, f"ruling.decision {ruling.get('decision')!r} not in "
                       f"{sorted(RULING_DECISIONS)}")
            if str(ruling.get("by") or "").split("/")[0] != "ron":
                err(f, f"ruling.by = {ruling.get('by')!r}: only ron rules on a proposal")


# ---------------------------------------------------------------- EDGAR + misc stores
# Everything below was written by the fetch plane and read by the UI or the analyst while
# being validated by nothing at all. data/edgar/docs/ is the sharpest case: it is the
# evidence base the verbatim-quote rule depends on, and until now a truncated, empty or
# mis-tickered document would have been discovered only by a quote failing to match it.
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
    # generated_by is how every other log in the repo says who wrote it (scout-log says
    # nell-scanner, map-log says atlas-cartographer). The digest says nothing.
    if not d.get("generated_by"):
        warn(f, "no generated_by: every other generated store names its writer")


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
        else:
            check_actor(f, t.get("by"), f"line {i + 1}")
            if t["action"] not in TRADE_ACTIONS:
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
        # underscore-prefixed files are agent stores (_map-log.json), never analysis objects
        files = [f for f in files if not f.name.startswith("_")]
        counts[name] = len(files)
        for f in files:
            fn(f)
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
    if (DATA / "radar" / "scout-log.json").exists():
        counts["scout-log"] = 1
        v_scout_log(DATA / "radar" / "scout-log.json")
    if (DATA / "feeds" / "latest.json").exists():
        counts["feeds"] = 1
        v_feeds(DATA / "feeds" / "latest.json")
    props = sorted((DATA / "proposals").glob("PROP-*.json")) if (DATA / "proposals").is_dir() else []
    counts["proposals"] = len(props)
    for f in props:
        v_proposal(f)
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
