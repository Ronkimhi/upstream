#!/usr/bin/env python3
"""Scenario gate. Legacy seed scenarios warn; today's campaign-era writes fail closed."""
import datetime
import json
import math
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from heat_score import scenario_bucket  # noqa: E402

FETCH_OPS = {">", ">=", "<", "<="}
CHECK_TYPES = {"price"}
TICKER = re.compile(r"^[A-Z0-9.\-]{1,10}$")


def load(path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def same_day(value, today):
    return str(value or "").startswith(today)


def campaign_era(root, chain_id):
    if (root / "data" / "mappings" / f"{chain_id}.json").exists():
        return True
    return any(chain_id in p.read_text(errors="ignore")
               for p in (root / "data" / "campaigns").glob("CAMP-*.json")) if (root / "data" / "campaigns").exists() else False


def head_scenario_ids(root, rel):
    try:
        out = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{rel}"],
                             capture_output=True, text=True, timeout=10)
        return {s.get("id") for s in json.loads(out.stdout).get("scenarios", [])} if out.returncode == 0 else set()
    except Exception:
        return set()


def main():
    argv = sys.argv[1:]
    root = Path(argv[argv.index("--root") + 1]).resolve() if "--root" in argv else Path(__file__).resolve().parent.parent
    today = argv[argv.index("--date") + 1] if "--date" in argv else datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    chains = []
    for path in sorted((root / "data" / "chains").glob("*.json")):
        if path.name.startswith("_"):
            continue
        chain = load(path)
        if isinstance(chain, dict):
            chain["_rel"] = str(path.relative_to(root))
            chains.append(chain)
    touched = [c for c in chains if same_day(c.get("scenarios_as_of"), today)]
    if not touched:
        print(f"check_scenarios: NOT RUN TODAY ({today}). 0 scenario runs, {len(chains)} chain(s) examined.")
        return 0
    ledger = (root / "data" / "ledger.md").read_text() if (root / "data" / "ledger.md").exists() else ""
    ledger_lines = [line for line in ledger.splitlines() if line.startswith(today) and "run scenarios " in line and re.search(r"\|\s*(RUN|AMEND)\s*\|", line)]
    calibration = load(root / "data" / "chains" / "_ember-log.json", {}) or {}
    calibrated = same_day((calibration.get("calibration") or {}).get("generated_at"), today)
    failures, notes, total = [], [], 0
    for chain in touched:
        cid, scenarios, links = chain.get("id", "<unknown>"), chain.get("scenarios") or [], {l.get("id") for l in chain.get("links") or []}
        strict, bad = campaign_era(root, cid), []
        buckets = {"scored": 0, "pending": 0, "errors": 0}
        total += len(scenarios)
        ids = [s.get("id") for s in scenarios if isinstance(s, dict)]
        if not 3 <= len(scenarios) <= 6: bad.append(f"scenario count {len(scenarios)} outside 3-6")
        if len(ids) != len(set(ids)) or any(not isinstance(i, str) or not i for i in ids): bad.append("scenario ids must be unique non-empty strings")
        probability = 0
        for scenario in scenarios:
            before = len(bad)
            sid = scenario.get("id", "<unknown>") if isinstance(scenario, dict) else "<invalid>"
            if not isinstance(scenario, dict):
                bad.append("scenario is not an object")
                buckets["errors"] += 1
                continue
            p = scenario.get("probability_pct")
            if not isinstance(p, (int, float)) or isinstance(p, bool): bad.append(f"{sid}: probability_pct is not numeric")
            else: probability += p
            if not str(scenario.get("narrative") or "").strip(): bad.append(f"{sid}: missing narrative")
            moved = scenario.get("links_moved") or []
            if not moved: bad.append(f"{sid}: moves no links")
            for move in moved:
                if not isinstance(move, dict) or move.get("link_id") not in links: bad.append(f"{sid}: move references no real link")
                elif move.get("direction") not in {"UP", "DOWN"} or move.get("magnitude") not in {"SMALL", "MEDIUM", "LARGE"} or not str(move.get("why") or "").strip(): bad.append(f"{sid}: every move needs direction, magnitude, and why")
            indicators = scenario.get("leading_indicators") or []
            if len(indicators) < 2: bad.append(f"{sid}: fewer than two leading indicators")
            for indicator in indicators:
                if not isinstance(indicator, dict) or not str(indicator.get("signal") or indicator.get("name") or "").strip(): bad.append(f"{sid}: malformed leading indicator")
                check = indicator.get("check") if isinstance(indicator, dict) else None
                if check is not None:
                    valid_check = (
                        isinstance(check, dict)
                        and check.get("type") in CHECK_TYPES
                        and isinstance(check.get("ticker"), str) and TICKER.fullmatch(check["ticker"])
                        and check.get("op") in FETCH_OPS
                        and isinstance(check.get("level"), (int, float))
                        and not isinstance(check.get("level"), bool)
                        and math.isfinite(check["level"])
                    )
                    if indicator.get("armed") is not True:
                        bad.append(f"{sid}: check exists but armed is not true")
                    if not valid_check:
                        bad.append(f"{sid}: check needs supported type, ticker, op, and finite numeric level")
                if isinstance(indicator, dict) and indicator.get("armed") is True and check is None:
                    bad.append(f"{sid}: armed indicator lacks check")
            if not scenario.get("invalidation_signs"): bad.append(f"{sid}: no invalidation sign")
            status, ref = scenario.get("status"), scenario.get("screen_ref")
            if status not in {"OPEN", "SCREENED", "INVALIDATED", "PLAYED_OUT"}: bad.append(f"{sid}: invalid scenario status")
            if status == "SCREENED" and (not isinstance(ref, str) or not (root / "data" / "screens" / Path(ref).name).exists()): bad.append(f"{sid}: SCREENED needs existing screen_ref")
            if status != "SCREENED" and ref: bad.append(f"{sid}: screen_ref requires SCREENED status")
            bucket = scenario_bucket(scenario, links)
            buckets["errors" if len(bad) > before or bucket == "errors" else bucket] += 1
        if not 90 <= probability <= 110: bad.append(f"probability total {probability} outside 90-110")
        gone = head_scenario_ids(root, chain["_rel"]) - set(ids)
        if gone: bad.append(f"rerun lost prior scenario ids: {sorted(gone)}")
        health = chain.get("scenario_health") or {}
        valid_health = all(isinstance(health.get(k), int) and not isinstance(health.get(k), bool) and health[k] >= 0 for k in ("examined", "scored", "pending", "errors"))
        if (not valid_health or health.get("examined") != len(scenarios)
                or valid_health and any(health[key] != value for key, value in buckets.items())):
            bad.append(f"{cid}: scenario_health must exactly match content buckets {buckets} across {len(scenarios)} scenarios")
        if strict:
            if not ledger_lines: bad.append(f"{cid}: no same-day RUN/AMEND ledger line for run scenarios")
            if not calibrated: bad.append(f"{cid}: Ember calibration is not same-day")
        (failures if strict else notes).extend(f"{cid}: {item}" for item in bad)
        print(f"  {cid}: {len(scenarios)} scenarios examined, {'strict' if strict else 'legacy warning'} mode, {len(bad)} finding(s)")
    if notes: print(f"  WARNING legacy seed scenarios: {len(notes)} finding(s) do not fail until a campaign-era scenario write")
    if failures:
        print(*[f"  FAIL {item}" for item in failures], sep="\n")
        return 1
    print(f"check_scenarios: OK. {total} scenarios examined across {len(touched)} scenario run(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
