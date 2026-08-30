#!/usr/bin/env python3
"""Recompute campaign completion and advance evidence-backed stages. Stdlib only.

Campaign selection, alternates, exclusions, targets, blockers and judgment fields are
preserved. Theme stages may advance to their computed depth but never regress.

Run: python3 tools/campaign_calibrate.py [--root PATH] [--dry-run]
"""
import argparse
import datetime
import json
from pathlib import Path

from check_campaign import STAGE_ORDER, compute_campaign_completion
from check_map import read_json


def _campaigns(root: Path) -> list[tuple[Path, dict]]:
    folder = root / "data" / "campaigns"
    out = []
    for path in sorted(folder.glob("CAMP-*.json")) if folder.is_dir() else []:
        campaign = read_json(path)
        if isinstance(campaign, dict):
            out.append((path, campaign))
    return out


def calibrate_campaign(root: Path, campaign: dict) -> dict:
    """Return an amended copy with fresh computed counts and monotonic stages."""
    out = json.loads(json.dumps(campaign))
    completion = compute_campaign_completion(root, out)
    computed_stages = {row.get("theme_id"): row.get("stage_computed")
                       for row in completion.get("per_theme") or []}
    advanced = []
    for theme in out.get("themes") or []:
        if not isinstance(theme, dict):
            continue
        before = theme.get("stage")
        actual = computed_stages.get(theme.get("theme_id"))
        if actual in STAGE_ORDER and STAGE_ORDER.get(before, -1) < STAGE_ORDER[actual]:
            theme["stage"] = actual
            advanced.append(f"{theme.get('theme_id')}:{before}->{actual}")
    # Recompute after the stage update so stage_computed rows and manifest remain one
    # coherent snapshot.
    out["completion"] = compute_campaign_completion(root, out)
    out["_stage_advances"] = advanced
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    campaigns = _campaigns(root)
    if not campaigns:
        print("campaign_calibrate: 0 campaigns examined; no campaign data, nothing written")
        return 0

    now = datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    for path, campaign in campaigns:
        amended = calibrate_campaign(root, campaign)
        advances = amended.pop("_stage_advances")
        completion = amended["completion"]
        print(f"campaign_calibrate: {path.name}: "
              f"{completion['themes_complete']}/{completion['themes_selected']} themes, "
              f"{completion['completed_profiles']} completed profiles, "
              f"{completion['o1_final']}/{completion['opportunity_tiers']['O1']} O1 FINAL")
        print(f"  stages advanced: {', '.join(advances) if advances else 'none'}")
        if args.dry_run:
            continue
        prior = (campaign.get("completion") or {}).get("completed_profiles")
        amended["as_of"] = now[:10]
        amended.setdefault("changelog", []).append({
            "ts": now,
            "by": "campaign_calibrate",
            "kind": "AMEND",
            "change": "recomputed campaign completion and advanced proven stages",
            "prior_completed_profiles": prior,
        })
        path.write_text(json.dumps(amended, indent=1, ensure_ascii=False) + "\n")
        print(f"campaign_calibrate: wrote {path.relative_to(root)}")
    if args.dry_run:
        print("campaign_calibrate: --dry-run, nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
