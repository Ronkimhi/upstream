#!/usr/bin/env python3
"""Recompute Ember's middle-funnel coverage log from chain files. Stdlib only."""
import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from heat_score import heat_bucket, instrument_bucket, scenario_bucket  # noqa: E402


def main() -> int:
    argv = sys.argv[1:]
    root = (Path(argv[argv.index("--root") + 1]).resolve()
            if "--root" in argv else Path(__file__).resolve().parent.parent)
    chains = []
    for path in sorted((root / "data" / "chains").glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            chains.append(json.loads(path.read_text()))
        except Exception:  # calibration reports readable records only
            continue
    links = [link for chain in chains for link in (chain.get("links") or [])]
    scenarios = [s for chain in chains for s in (chain.get("scenarios") or [])]
    totals = {"scored": 0, "pending": 0, "errors": 0}
    scenario_totals = {"scored": 0, "pending": 0, "errors": 0}
    instrument_totals = {"examined": 0, "scored": 0, "pending": 0, "errors": 0}
    verdict_totals = {}
    shadow_totals = {"over_crowded_links": 0, "links_with_rows": 0, "links_without_rows": 0}
    shadow_rows = []
    try:
        shadow_rows = json.loads((root / "data" / "shadow" / "book.json").read_text()).get("rows") or []
    except Exception:  # an absent or unreadable book reads as no rows, and the count says so
        shadow_rows = []
    per_chain = {}
    for chain in chains:
        heat = {"scored": 0, "pending": 0, "errors": 0}
        scen = {"scored": 0, "pending": 0, "errors": 0}
        inst = {"examined": 0, "scored": 0, "pending": 0, "errors": 0}
        by_verdict = {}
        shadow = {"over_crowded_links": 0, "links_with_rows": 0, "links_without_rows": 0}
        link_ids = {link.get("id") for link in chain.get("links") or []}
        for link in chain.get("links") or []:
            heat[heat_bucket(link.get("heat"))] += 1
            h = link.get("heat") if isinstance(link.get("heat"), dict) else {}
            verdict = h.get("verdict")
            by_verdict[str(verdict)] = by_verdict.get(str(verdict), 0) + 1
            if link.get("price_instruments"):
                inst["examined"] += 1
                inst[instrument_bucket(h)] += 1
            if verdict == "OVER_CROWDED":
                # The graded no (method section 8, 2026-09-13): does this call have shadow rows?
                shadow["over_crowded_links"] += 1
                has_rows = any(isinstance(r, dict) and r.get("origin") == "HEAT_OVER_CROWDED"
                               and r.get("chain_id") == chain.get("id") and r.get("link_id") == link.get("id")
                               and str(r.get("verdict_date") or "") == str(chain.get("heat_as_of") or "")
                               for r in shadow_rows)
                shadow["links_with_rows" if has_rows else "links_without_rows"] += 1
        for scenario in chain.get("scenarios") or []:
            scen[scenario_bucket(scenario, link_ids)] += 1
        for key in totals:
            totals[key] += heat[key]
            scenario_totals[key] += scen[key]
        for key in instrument_totals:
            instrument_totals[key] += inst[key]
        for key, value in by_verdict.items():
            verdict_totals[key] = verdict_totals.get(key, 0) + value
        for key in shadow_totals:
            shadow_totals[key] += shadow[key]
        stored_health = chain.get("heat_health") if isinstance(chain.get("heat_health"), dict) else {}
        want_health = {"examined": len(chain.get("links") or []), **heat}
        per_chain[chain.get("id")] = {
            "heat": want_health,
            "instruments": inst,
            "by_verdict": by_verdict,
            "shadow": shadow,
            "scenarios": {"examined": len(chain.get("scenarios") or []), **scen},
            "stored_heat_health_matches": all(stored_health.get(k) == v for k, v in want_health.items()),
            "stored_instrument_health_matches": (stored_health.get("instruments") == inst if inst["examined"]
                                                 else stored_health.get("instruments") in (None, inst)),
            "stored_scenario_health_matches": chain.get("scenario_health") == {"examined": len(chain.get("scenarios") or []), **scen},
        }
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    log = {
        "id": "ember-log",
        "generated_by": "ember-scenario-analyst",
        "calibration": {
            "generated_at": now,
            "chains_examined": len(chains),
            "links_examined": len(links),
            "heat": {"examined": len(links), **totals},
            "instruments": instrument_totals,
            "by_verdict": verdict_totals,
            "shadow": shadow_totals,
            "scenarios_examined": len(scenarios),
            "scenarios": {"examined": len(scenarios), **scenario_totals},
        },
        "per_chain": per_chain,
    }
    (root / "data" / "chains" / "_ember-log.json").write_text(
        json.dumps(log, indent=2) + "\n"
    )
    print("ember_calibrate: "
          f"{len(chains)} chains, {len(links)} links, {totals['scored']} heat-scored, "
          f"{len(scenarios)} scenarios; instruments {instrument_totals['examined']} examined, "
          f"{instrument_totals['scored']} scored; OVER_CROWDED links {shadow_totals['over_crowded_links']}, "
          f"{shadow_totals['links_with_rows']} with shadow rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
