#!/usr/bin/env python3
"""Tally's calibration engine. Stdlib only, offline, session venue.

Recomputes `data/impact/_rank-log.json` from what is already on disk: the ranked queue,
coverage against every occurrence that exists, and the downstream outcome of each appraisal.
Judgment fields (proposed_rules, notes, repairs) are the agent's to write and are preserved
verbatim across runs. Amend, never recreate.

Two things this file exists to make impossible:

  * A hand-ordered queue. The ordering is a function of the appraisals on disk, so a session
    cannot put its favourite occurrence at the top and no session hand-counts coverage. Same
    posture as scout_calibrate.py and campaign_calibrate.py.
  * A stage that grades itself by assertion. `outcomes` records, per appraisal, what the
    occurrence actually went on to reach (chained, a money_corner link, a FINAL verdict). It
    reads zero until enough appraisals exist to compare, and the log SAYS it reads zero rather
    than omitting the field, because a missing column looks like a column that passed.

Every metric carries its denominator (Rule 21: a count with no denominator cannot fail).

Run: python3 tools/impact_calibrate.py [--dry-run]
"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from impact_score import BANDS, compute, rank_key  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "impact" / "_rank-log.json"
NOW = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
TODAY = datetime.datetime.now(datetime.timezone.utc).date()


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


def appraisals() -> list[dict]:
    folder = DATA / "impact"
    if not folder.exists():
        return []
    out = []
    for p in sorted(folder.glob("*.json")):
        if p.name.startswith("_"):
            continue
        obj = read_json(p)
        if isinstance(obj, dict):
            out.append(obj)
    return out


def occurrences() -> dict:
    """Every occurrence that could be appraised: signals plus live ambient candidates.

    Candidates that are DISMISSED or EXPIRED are excluded from the denominator on purpose.
    Counting them would make coverage look permanently broken and, worse, would make the
    number go DOWN when radar does its job, which is the shape of a metric nobody trusts.
    """
    out = {}
    for p in sorted((DATA / "signals").glob("*.json")) if (DATA / "signals").exists() else []:
        s = read_json(p)
        if isinstance(s, dict) and s.get("id"):
            out[s["id"]] = {
                "kind": "signal",
                "status": s.get("status"),
                "title": s.get("title"),
                "unmappedness": (s.get("unmappedness") or {}).get("score"),
                "chain_id": s.get("chain_id"),
            }
    cands = read_json(DATA / "radar" / "candidates.json", {}) or {}
    for c in cands.get("candidates", []):
        if not isinstance(c, dict) or not c.get("id"):
            continue
        if c.get("status") in {"DISMISSED", "EXPIRED"}:
            continue
        out[c["id"]] = {
            "kind": "candidate",
            "status": c.get("status"),
            "title": c.get("title"),
            "unmappedness": None,  # candidates sit below the signal bar and carry no score
            "chain_id": None,
        }
    return out


def downstream(occ_id: str, occ: dict) -> dict:
    """What this occurrence actually reached. The only honest grade of an appraisal.

    Read from the stores, never from the appraisal's own claim about itself.
    """
    chain_id = occ.get("chain_id")
    result = {"chained": bool(chain_id), "chain_id": chain_id,
              "money_corner_links": 0, "final_verdicts": 0}
    if not chain_id:
        return result
    chain = read_json(DATA / "chains" / f"{chain_id}.json", {}) or {}
    for l in chain.get("links", []):
        if isinstance(l, dict) and (l.get("heat") or {}).get("money_corner"):
            result["money_corner_links"] += 1
    for p in sorted((DATA / "stocks").glob("*.json")) if (DATA / "stocks").exists() else []:
        if p.name.startswith("_"):
            continue
        st = read_json(p, {}) or {}
        if st.get("chain_id") == chain_id and st.get("status") == "FINAL":
            result["final_verdicts"] += 1
    return result


def build() -> dict:
    occ = occurrences()
    apps = appraisals()
    by_occ = {a.get("occurrence_id"): a for a in apps if a.get("occurrence_id")}

    queue, band_counts = [], {b: 0 for b in BANDS}
    stale = []
    for a in sorted(apps, key=lambda x: rank_key(x)):
        c = compute(a)
        oid = a.get("occurrence_id")
        meta = occ.get(oid, {})
        band_counts[c["impact_band"]] = band_counts.get(c["impact_band"], 0) + 1
        row = {
            "occurrence_id": oid,
            "appraisal_id": a.get("id"),
            "title": meta.get("title"),
            "impact_score": c["impact_score"],
            "impact_band": c["impact_band"],
            # The pair, never the score alone (method section 0.2). Rendered beside the score
            # so a money number cannot quietly replace section 0's selection rule.
            "unmappedness": meta.get("unmappedness"),
            "null_legs": c["null_legs"],
            "as_of": a.get("as_of"),
            "review_by": a.get("review_by"),
            "outcome": downstream(oid, meta),
        }
        if a.get("review_by") and str(a["review_by"])[:10] < TODAY.isoformat():
            stale.append(oid)
            row["stale"] = True
        queue.append(row)

    unappraised = [oid for oid in occ if oid not in by_occ]
    orphans = [a.get("occurrence_id") for a in apps if a.get("occurrence_id") not in occ]
    graded = [r for r in queue if r["outcome"]["chained"]]

    return {
        "queue": queue,
        "calibration": {
            "as_of": TODAY.isoformat(),
            "denominators": {
                "occurrences_on_disk": len(occ),
                "signals": sum(1 for v in occ.values() if v["kind"] == "signal"),
                "live_candidates": sum(1 for v in occ.values() if v["kind"] == "candidate"),
                "appraised": len(apps),
                "unappraised": len(unappraised),
                "stale_appraisals": len(stale),
            },
            "bands": band_counts,
            "unappraised_ids": sorted(unappraised),
            "stale_ids": sorted(stale),
            # An appraisal pointing at an occurrence that no longer exists. Named rather than
            # dropped: a silently vanishing row is indistinguishable from one never written.
            "orphan_appraisals": sorted(o for o in orphans if o),
            "outcomes": {
                "appraisals_with_a_chain": len(graded),
                "reached_money_corner": sum(1 for r in graded if r["outcome"]["money_corner_links"]),
                "reached_final_verdict": sum(1 for r in graded if r["outcome"]["final_verdicts"]),
                "note": ("This stage is graded by what its appraisals went on to reach. With "
                         f"{len(graded)} of {len(apps)} appraisal(s) chained, these counts do "
                         "not yet say whether the scores are any good. They are reported at "
                         "zero rather than omitted so the gap stays visible."),
            },
        },
    }


def main() -> int:
    dry = "--dry-run" in sys.argv
    fresh = build()
    prior = read_json(LOG) if LOG.exists() else None

    if prior:
        log = dict(prior)
        log.update(fresh)
        log["as_of"] = TODAY.isoformat()
        log.setdefault("changelog", []).append(
            {"ts": NOW, "by": "tally-appraiser",
             "change": f"AMEND: recomputed queue and coverage from disk "
                       f"({fresh['calibration']['denominators']['appraised']} appraisal(s), "
                       f"{fresh['calibration']['denominators']['occurrences_on_disk']} occurrence(s))"})
    else:
        log = {
            "as_of": TODAY.isoformat(),
            "generated_by": "tools/impact_calibrate.py",
            "owner": "tally-appraiser",
            **fresh,
            "proposed_rules": [],
            "repairs": [],
            "notes": [],
            "changelog": [{"ts": NOW, "by": "tally-appraiser", "change": "log created"}],
        }

    d = fresh["calibration"]["denominators"]
    print(f"impact_calibrate: {d['appraised']} of {d['occurrences_on_disk']} occurrence(s) "
          f"appraised ({d['signals']} signal(s), {d['live_candidates']} live candidate(s)); "
          f"{d['unappraised']} unappraised, {d['stale_appraisals']} past review_by")
    print(f"impact_calibrate: bands {fresh['calibration']['bands']}")
    o = fresh["calibration"]["outcomes"]
    print(f"impact_calibrate: graded {o['appraisals_with_a_chain']} chained, "
          f"{o['reached_money_corner']} reached a money corner, "
          f"{o['reached_final_verdict']} reached a FINAL verdict")
    if dry:
        print("impact_calibrate: --dry-run, nothing written")
        return 0
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps(log, indent=2) + "\n")
    print(f"impact_calibrate: wrote {LOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
