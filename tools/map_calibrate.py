#!/usr/bin/env python3
"""Atlas's calibration engine. Stdlib only, offline, session venue.

Recomputes the machine-measurable half of `data/chains/_map-log.json` from what is on disk:
link yield, structural quality, amendment history, and archetype recurrence. Judgment fields
(archetypes, spot_tests, repairs, notes) belong to the agent and are preserved verbatim.

Amend, never recreate: an existing log keeps its judgment fields and its changelog, and gains
one AMEND entry per run.

Every metric carries its denominator (Rule 21: a count with no denominator cannot fail).
Link yield is joined on `link_id` only. Ticker back-matching is never used: tested against
the seed screen it misattributes VRT and is ambiguous on any name sitting on two links.

Run: python3 tools/map_calibrate.py [--dry-run]
"""
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "chains" / "_map-log.json"
NOW = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
TODAY = datetime.date.today()
CHANGE_KINDS = ("BUILD", "STAGE", "AMEND", "LINK_ADDED", "LINK_REMOVED", "RESCORE")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


def existing_log():
    return read_json(LOG) if LOG.exists() else None


def load_dir(folder: Path) -> list[dict]:
    """Every JSON object in a folder, skipping underscore-prefixed store files."""
    if not folder.exists():
        return []
    out = []
    for f in sorted(folder.glob("*.json")):
        if f.name.startswith("_"):
            continue
        d = read_json(f)
        if isinstance(d, dict):
            d["_file"] = f.name
            out.append(d)
    return out


def days_since(s) -> int | None:
    if not isinstance(s, str) or len(s) < 10:
        return None
    try:
        return (TODAY - datetime.date.fromisoformat(s[:10])).days
    except ValueError:
        return None


def band_for(impact, crowdedness):
    """method 3 bands, first match wins. Duplicated from validate.py deliberately: this
    script must be able to report a disagreement that the validator would reject."""
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


def structure_findings(c: dict) -> dict:
    """Everything computable about one map's shape. Reported, not enforced; the gate enforces."""
    links = c.get("links") or []
    pos = {l.get("id"): l.get("position") for l in links}
    n = len(links)
    ids = set(pos)

    one_way = []
    for l in links:
        for b in l.get("upstream_of", []):
            other = next((x for x in links if x.get("id") == b), None)
            if other is not None and l["id"] not in (other.get("downstream_of") or []):
                one_way.append(f"{l['id']}->{b}")
        for b in l.get("downstream_of", []):
            other = next((x for x in links if x.get("id") == b), None)
            if other is not None and l["id"] not in (other.get("upstream_of") or []):
                one_way.append(f"{b}<-{l['id']}")

    wrong_dir = [f"{l['id']}({pos[l['id']]})->{b}({pos[b]})"
                 for l in links for b in l.get("upstream_of", [])
                 if b in pos and isinstance(pos.get(l["id"]), int)
                 and isinstance(pos.get(b), int) and pos[l["id"]] >= pos[b]]

    adj = {i: set() for i in ids}
    for l in links:
        for b in list(l.get("upstream_of", [])) + list(l.get("downstream_of", [])):
            if b in adj:
                adj[l["id"]].add(b)
                adj[b].add(l["id"])
    seen, stack = set(), ([links[0]["id"]] if links else [])
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adj.get(cur, set()) - seen)

    tickers = [t for l in links for t in (l.get("example_tickers") or [])]
    non_us = [t for t in tickers if "." in t]
    mismatch = []
    for l in links:
        h = l.get("heat") or {}
        g = lambda k: (h.get(k) or {}).get("score") if isinstance(h.get(k), dict) else None  # noqa: E731
        want = band_for(g("impact"), g("crowdedness"))
        if want and h.get("verdict") and h["verdict"] != want:
            mismatch.append(f"{l['id']}: written {h['verdict']}, computes {want}")

    return {
        "links": n,
        "orphans": [l.get("id") for l in links
                    if not (l.get("upstream_of") or l.get("downstream_of"))],
        "one_way_edges": one_way,
        "wrong_direction_edges": wrong_dir,
        "positions_are_permutation": sorted(v for v in pos.values() if isinstance(v, int)) == list(range(1, n + 1)),
        "connected": len(seen) == len(ids) if ids else True,
        "links_unreachable": sorted(ids - seen) if ids else [],
        "links_without_tickers": [l.get("id") for l in links if not (l.get("example_tickers") or [])],
        "thin_choke_points": [l.get("id") for l in links
                              if (l.get("bottleneck") or {}).get("criticality") == "CHOKE_POINT"
                              and len(l.get("example_tickers") or []) <= 1],
        "non_us_ticker_share": round(len(non_us) / len(tickers), 3) if tickers else None,
        "tickers_examined": len(tickers),
        "links_without_evidence": [l.get("id") for l in links if not l.get("evidence")],
        "verdict_mismatches": mismatch,
        "map_limitation_chars": len((c.get("map_limitation") or "").strip()),
        "heat_as_of_age_days": days_since(c.get("heat_as_of")),
    }


def build_calibration() -> dict:
    chains = load_dir(DATA / "chains")
    screens = load_dir(DATA / "screens")
    stocks = load_dir(DATA / "stocks")

    # ---- link yield, joined on link_id ONLY.
    rows_by_link: dict[tuple, int] = {}
    unattributed = []
    for s in screens:
        cid = s.get("chain_id")
        for bucket, rows in (s.get("buckets") or {}).items():
            for r in rows or []:
                lid = r.get("link_id")
                if lid:
                    rows_by_link[(cid, lid)] = rows_by_link.get((cid, lid), 0) + 1
                else:
                    unattributed.append({"screen": s.get("id"), "ticker": r.get("ticker"),
                                         "basis": r.get("link_id_basis")})
    dives_by_link: dict[tuple, list] = {}
    for st in stocks:
        if st.get("fixture"):
            continue
        lid = st.get("link_id")
        if lid:
            dives_by_link.setdefault((st.get("chain_id"), lid), []).append(st.get("ticker"))

    per_chain, depth_totals = {}, {"MAPPED": 0, "SCORED": 0, "SCREENED": 0, "DIVED": 0}
    money_links = 0
    for c in chains:
        cid = c.get("id")
        links = c.get("links") or []
        scored = [l for l in links if (l.get("heat") or {}).get("verdict")]
        screened = [l for l in links if rows_by_link.get((cid, l.get("id")))]
        dived = [l for l in links if dives_by_link.get((cid, l.get("id")))]
        money = [l for l in links if (l.get("heat") or {}).get("money_corner")]
        money_links += len(money)
        for l in links:
            lid = l.get("id")
            if dives_by_link.get((cid, lid)):
                depth_totals["DIVED"] += 1
            elif rows_by_link.get((cid, lid)):
                depth_totals["SCREENED"] += 1
            elif (l.get("heat") or {}).get("verdict"):
                depth_totals["SCORED"] += 1
            else:
                depth_totals["MAPPED"] += 1
        kinds = {}
        for e in c.get("changelog") or []:
            kinds[e.get("kind") or "UNTYPED"] = kinds.get(e.get("kind") or "UNTYPED", 0) + 1
        per_chain[cid] = {
            "links": len(links),
            "scored": len(scored),
            "yielded_a_name": len(screened),
            "yielded_a_dive": len(dived),
            "money_corner_links": [l.get("id") for l in money],
            "dead_links": [l.get("id") for l in links if not rows_by_link.get((cid, l.get("id")))],
            "changelog_kinds": kinds,
            "structure": structure_findings(c),
        }

    all_links = sum(len(c.get("links") or []) for c in chains)
    yielded = sum(v["yielded_a_name"] for v in per_chain.values())

    # ---- amendment history: my own misses
    amendments = []
    for c in chains:
        for e in c.get("changelog") or []:
            if e.get("kind") in ("AMEND", "LINK_ADDED", "LINK_REMOVED", "RESCORE"):
                amendments.append({"chain": c.get("id"), "ts": e.get("ts"),
                                   "kind": e.get("kind"), "change": (e.get("change") or "")[:200]})
    typed = sum(1 for c in chains for e in (c.get("changelog") or []) if e.get("kind"))
    total_entries = sum(len(c.get("changelog") or []) for c in chains)

    return {
        "generated_at": NOW,
        "link_yield": {
            "links_total": all_links,
            "yielded_a_name": yielded,
            "yield_rate": round(yielded / all_links, 3) if all_links else None,
            "depth": depth_totals,
            "money_corner_links": money_links,
            "unattributed_screen_rows": unattributed,
            "note": ("Joined on link_id only. A screen row with no link_id cannot be counted "
                     "against any link; ticker back-matching is not a substitute."),
        },
        "per_chain": per_chain,
        "amendment_history": {
            "entries_typed": typed,
            "entries_total": total_entries,
            "amendments": amendments,
            "note": ("A link added on a later pass is a link the first map missed. Untyped "
                     "changelog entries cannot be classified, so they are reported, not assumed clean."),
        },
        "denominators": {
            "chains_examined": len(chains),
            "links_examined": all_links,
            "edges_examined": sum(len(l.get("upstream_of") or []) for c in chains for l in c.get("links") or []),
            "screens_examined": len(screens),
            "screen_rows_examined": sum(len(r or []) for s in screens for r in (s.get("buckets") or {}).values()),
            "dives_examined": len([s for s in stocks if not s.get("fixture")]),
        },
    }


def main() -> int:
    dry = "--dry-run" in sys.argv
    cal = build_calibration()
    log = existing_log()
    if isinstance(log, dict):
        prior = (log.get("calibration") or {}).get("generated_at")
        log.setdefault("changelog", []).append({
            "ts": NOW, "by": "map_calibrate", "kind": "AMEND",
            "change": "recomputed calibration from data/ (amend)", "prior": prior})
    else:
        log = {
            "id": "map-log", "created_at": NOW, "generated_by": "atlas-cartographer",
            "archetypes": [], "spot_tests": [], "repairs": [], "notes": [],
            "confidence_audit": {"verified": 0, "inferred": 0, "speculative": 0, "null": 0},
            "changelog": [{"ts": NOW, "by": "map_calibrate", "kind": "BUILD",
                           "change": "created map log"}],
        }
    log["as_of"] = NOW[:10]
    log["updated_at"] = NOW
    log["calibration"] = cal

    d, ly = cal["denominators"], cal["link_yield"]
    print(f"map_calibrate: {d['chains_examined']} chains, {d['links_examined']} links, "
          f"{d['edges_examined']} edges, {d['screen_rows_examined']} screen rows, "
          f"{d['dives_examined']} dives examined")
    print(f"  link yield: {ly['yielded_a_name']}/{ly['links_total']} links ever produced a name · "
          f"depth {ly['depth']} · {ly['money_corner_links']} money-corner links")
    ah = cal["amendment_history"]
    print(f"  amendments: {len(ah['amendments'])} across {ah['entries_total']} changelog entries "
          f"({ah['entries_typed']} typed)")
    for cid, v in cal["per_chain"].items():
        st = v["structure"]
        flags = []
        if st["orphans"]:
            flags.append(f"{len(st['orphans'])} orphan")
        if st["one_way_edges"]:
            flags.append(f"{len(st['one_way_edges'])} one-way edge")
        if st["wrong_direction_edges"]:
            flags.append(f"{len(st['wrong_direction_edges'])} wrong-direction")
        if st["verdict_mismatches"]:
            flags.append(f"{len(st['verdict_mismatches'])} verdict mismatch")
        if st["thin_choke_points"]:
            flags.append(f"thin choke: {','.join(st['thin_choke_points'])}")
        if st["links_without_evidence"]:
            flags.append(f"{len(st['links_without_evidence'])}/{st['links']} uncited")
        print(f"  {cid}: {v['yielded_a_name']}/{v['links']} yielded, "
              f"{v['scored']}/{v['links']} scored, non-US {st['non_us_ticker_share']}"
              + (" · " + "; ".join(flags) if flags else " · clean"))
    if dry:
        print("map_calibrate: --dry-run, nothing written")
        return 0
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n")
    print(f"map_calibrate: wrote {LOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
