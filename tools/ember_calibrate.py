#!/usr/bin/env python3
"""Recompute Ember's middle-funnel coverage log from chain files. Stdlib only."""
import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from heat_score import heat_bucket, scenario_bucket  # noqa: E402


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
    per_chain = {}
    for chain in chains:
        heat = {"scored": 0, "pending": 0, "errors": 0}
        scen = {"scored": 0, "pending": 0, "errors": 0}
        link_ids = {link.get("id") for link in chain.get("links") or []}
        for link in chain.get("links") or []:
            heat[heat_bucket(link.get("heat"))] += 1
        for scenario in chain.get("scenarios") or []:
            scen[scenario_bucket(scenario, link_ids)] += 1
        for key in totals:
            totals[key] += heat[key]
            scenario_totals[key] += scen[key]
        per_chain[chain.get("id")] = {
            "heat": {"examined": len(chain.get("links") or []), **heat},
            "scenarios": {"examined": len(chain.get("scenarios") or []), **scen},
            "stored_heat_health_matches": chain.get("heat_health") == {"examined": len(chain.get("links") or []), **heat},
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
          f"{len(scenarios)} scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
