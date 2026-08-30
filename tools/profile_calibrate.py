#!/usr/bin/env python3
"""Recompute issuer-profile coverage with explicit denominators. Stdlib only.

Writes data/companies/_profile-log.json only after at least one real profile exists.
Judgment fields and changelog history are preserved on every amendment.

Run: python3 tools/profile_calibrate.py [--root PATH] [--dry-run]
"""
import argparse
import datetime
import json
from pathlib import Path

from check_map import read_json
from check_profile import completeness, numeric_source_failures


def _profiles(root: Path) -> list[tuple[Path, dict]]:
    folder = root / "data" / "companies"
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if path.name.startswith("_"):
            continue
        profile = read_json(path)
        if isinstance(profile, dict):
            out.append((path, profile))
    return out


def build_calibration(root: Path) -> dict:
    profiles = _profiles(root)
    statuses = {}
    data_tiers = {}
    opportunity_tiers = {}
    per_profile = []
    numeric_fields = inferred_fields = placement_refs = 0
    for path, profile in profiles:
        status = profile.get("status", "?")
        data_tier = profile.get("data_tier", "?")
        opportunity = profile.get("opportunity_tier", "?")
        statuses[status] = statuses.get(status, 0) + 1
        data_tiers[data_tier] = data_tiers.get(data_tier, 0) + 1
        opportunity_tiers[opportunity] = opportunity_tiers.get(opportunity, 0) + 1
        have, total = completeness(profile)
        _, nums, inferred = numeric_source_failures(profile.get("metrics") or {})
        numeric_fields += nums
        inferred_fields += inferred
        placement_refs += len(profile.get("placements") or [])
        per_profile.append({
            "issuer_id": profile.get("issuer_id"),
            "status": status,
            "data_tier": data_tier,
            "opportunity_tier": opportunity,
            "required_fields_complete": have,
            "required_fields_total": total,
            "completeness": round(have / total, 3) if total else None,
            "placement_refs": len(profile.get("placements") or []),
            "numeric_fields": nums,
            "official_source_inferences": inferred,
            "file": str(path.relative_to(root)),
        })
    now = datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "generated_at": now,
        "statuses": statuses,
        "data_tiers": data_tiers,
        "opportunity_tiers": opportunity_tiers,
        "per_profile": per_profile,
        "denominators": {
            "profiles_examined": len(profiles),
            "complete_profiles": statuses.get("COMPLETE", 0),
            "placement_refs_examined": placement_refs,
            "numeric_fields_examined": numeric_fields,
            "official_source_inferences_examined": inferred_fields,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    calibration = build_calibration(root)
    den = calibration["denominators"]
    if not den["profiles_examined"]:
        print("profile_calibrate: 0 profiles examined; no campaign data, nothing written")
        return 0

    print(f"profile_calibrate: {den['complete_profiles']}/{den['profiles_examined']} "
          f"profiles complete, {den['placement_refs_examined']} placement references, "
          f"{den['numeric_fields_examined']} numeric fields examined")
    print(f"  data tiers {calibration['data_tiers']} · opportunity tiers "
          f"{calibration['opportunity_tiers']} · "
          f"{den['official_source_inferences_examined']} official-source inferences")
    if args.dry_run:
        print("profile_calibrate: --dry-run, nothing written")
        return 0

    log_path = root / "data" / "companies" / "_profile-log.json"
    now = calibration["generated_at"]
    log = read_json(log_path)
    if isinstance(log, dict):
        prior = (log.get("calibration") or {}).get("generated_at")
        log.setdefault("changelog", []).append({
            "ts": now, "by": "profile_calibrate", "kind": "AMEND",
            "change": "recomputed profile calibration from normalized stores",
            "prior": prior,
        })
    else:
        log = {
            "id": "profile-log",
            "created_at": now,
            "generated_by": "profile_calibrate",
            "spot_tests": [],
            "repairs": [],
            "notes": [],
            "changelog": [{
                "ts": now, "by": "profile_calibrate", "kind": "BUILD",
                "change": "created profile calibration log",
            }],
        }
    log["as_of"] = now[:10]
    log["updated_at"] = now
    log["calibration"] = calibration
    log_path.write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n")
    print(f"profile_calibrate: wrote {log_path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
