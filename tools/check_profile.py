#!/usr/bin/env python3
"""Reusable issuer-profile gate. Stdlib only, offline.

Profiles are medium-depth research, not Stocky verdicts. Data availability (T1/T2/T3)
and opportunity priority (O1/O2/O3) are separate axes. T2 and T3 profiles may complete
with official-source INFERRED facts; the gate never requires a market-data file or a T1
classification.

Run: python3 tools/check_profile.py [--root PATH]
Exit 0 clean, 1 on any failure.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

from check_map import ISSUER_ID_RE, read_json, valid_date

PROFILE_STATUS = {"DRAFT", "BLOCKED", "COMPLETE"}
DATA_TIERS = {"T1", "T2", "T3"}
OPPORTUNITY_TIERS = {"O1", "O2", "O3"}
DISPOSITIONS = {"ADVANCE", "RETAIN", "DEFER", "PASS", "BLOCKED"}
EVIDENCE_TAGS = {"VERIFIED", "INFERRED", "SPECULATIVE", "NULL"}
VERDICT_WORDS = {"INVESTABLE", "WATCH", "TOO_LATE"}
METRIC_GROUPS = {
    "revenue", "growth", "margins", "cash_conversion",
    "leverage", "quality", "valuation", "reverse_dcf",
}
COMPLETE_FIELDS = {
    "business_summary", "exposure_summary", "metrics", "crowdedness_caveats",
    "catalysts", "risks", "data_gaps", "disposition",
}


def _mapping_indexes(root: Path):
    issuer_names = {}
    listing_to_issuer = {}
    placements = set()
    folder = root / "data" / "mappings"
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if path.name.startswith("_"):
            continue
        mapping = read_json(path)
        if not isinstance(mapping, dict):
            continue
        for issuer in mapping.get("issuers") or []:
            if isinstance(issuer, dict) and issuer.get("issuer_id"):
                issuer_names.setdefault(issuer["issuer_id"], issuer.get("name"))
        for listing in mapping.get("listings") or []:
            if isinstance(listing, dict) and listing.get("listing_id"):
                listing_to_issuer[listing["listing_id"]] = listing.get("issuer_id")
        for placement in mapping.get("placements") or []:
            if isinstance(placement, dict):
                placements.add((placement.get("chain_id"), placement.get("link_id"),
                                placement.get("issuer_id")))
    return issuer_names, listing_to_issuer, placements


def _verdict_leaks(obj, where: str = "profile") -> list[str]:
    leaks = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{where}.{key}"
            if str(key).casefold() == "verdict":
                leaks.append(f"{child}: verdict fields belong only in Stocky dives")
            leaks.extend(_verdict_leaks(value, child))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            leaks.extend(_verdict_leaks(value, f"{where}[{i}]"))
    elif isinstance(obj, str) and obj.strip().upper() in VERDICT_WORDS:
        leaks.append(f"{where}: {obj!r} is final verdict vocabulary; profiles use a "
                     "non-final disposition")
    return leaks


def _has_source(block: dict) -> bool:
    name = block.get("source_name") or block.get("source")
    url = block.get("url") or block.get("source_url")
    date = block.get("source_date") or block.get("as_of")
    return bool(str(name or "").strip()) and valid_date(date) and \
        isinstance(url, str) and url.startswith(("http://", "https://"))


def numeric_source_failures(metrics, where: str = "metrics") -> tuple[list[str], int, int]:
    """Every numeric analytical leaf inherits a source block from itself or an ancestor.

    Returns (failures, numeric_fields_examined, official_inferences_examined).
    """
    failures = []
    examined = inferred = 0

    def walk(value, path: str, backing: dict | None = None):
        nonlocal examined, inferred
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            examined += 1
            if not isinstance(backing, dict) or not _has_source(backing):
                failures.append(f"{path}: numeric field has no source_name, source_date "
                                "and http(s) url")
                return
            tag = backing.get("tag")
            if tag not in EVIDENCE_TAGS - {"NULL"}:
                failures.append(f"{path}: source block tag {tag!r} must be VERIFIED, "
                                "INFERRED or SPECULATIVE")
            if tag == "INFERRED":
                inferred += 1
                if backing.get("official_source") is not True:
                    failures.append(f"{path}: INFERRED numeric data must explicitly set "
                                    "official_source: true")
                if not str(backing.get("basis") or "").strip():
                    failures.append(f"{path}: INFERRED numeric data needs its derivation basis")
            if tag == "SPECULATIVE" and not str(backing.get("basis") or "").strip():
                failures.append(f"{path}: SPECULATIVE numeric data needs its assumption basis")
            return
        if isinstance(value, dict):
            local = value if _has_source(value) else backing
            if "value" in value and value.get("value") is None:
                if value.get("tag") != "NULL":
                    failures.append(f"{path}: null metric value must be tagged NULL")
                if not str(value.get("basis") or "").strip():
                    failures.append(f"{path}: NULL metric needs a basis naming the data gap")
            for key, child in value.items():
                # Source metadata is descriptive, not analytical payload.
                if key in {"source_name", "source", "source_date", "as_of", "url",
                           "source_url", "tag", "official_source", "basis"}:
                    continue
                walk(child, f"{path}.{key}", local)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{path}[{i}]", backing)

    walk(metrics, where)
    return failures, examined, inferred


def _nonempty(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None


def completeness(profile: dict) -> tuple[int, int]:
    """Required-field completeness with an explicit denominator."""
    checks = [
        bool(profile.get("issuer_id")),
        bool(profile.get("issuer_name")),
        valid_date(profile.get("as_of")),
        profile.get("data_tier") in DATA_TIERS,
        profile.get("opportunity_tier") in OPPORTUNITY_TIERS,
        bool(profile.get("listing_refs")),
        bool(profile.get("placements")),
    ]
    checks.extend(_nonempty(profile.get(field)) for field in sorted(COMPLETE_FIELDS))
    metrics = profile.get("metrics")
    checks.extend(isinstance(metrics, dict) and group in metrics for group in sorted(METRIC_GROUPS))
    return sum(checks), len(checks)


def validate_profile(root: Path, path: Path, obj=None) -> list[str]:
    """Return schema, source-discipline and reference failures for one profile."""
    profile = obj if isinstance(obj, dict) else read_json(path)
    if not isinstance(profile, dict):
        return ["profile must be a readable JSON object"]
    failures = []
    required = {
        "issuer_id", "issuer_name", "as_of", "status", "data_tier",
        "opportunity_tier", "listing_refs", "placements", "business_summary",
        "exposure_summary", "metrics", "crowdedness_caveats", "catalysts",
        "risks", "data_gaps", "disposition", "confidence_audit", "changelog",
    }
    missing = sorted(required - set(profile))
    if missing:
        failures.append(f"missing keys: {', '.join(missing)}")

    issuer_id = profile.get("issuer_id")
    if not isinstance(issuer_id, str) or not ISSUER_ID_RE.fullmatch(issuer_id):
        failures.append(f"issuer_id {issuer_id!r} is not a stable safe identifier")
    elif path.stem != issuer_id:
        failures.append(f"filename {path.name!r} must match issuer_id {issuer_id!r}")
    if not str(profile.get("issuer_name") or "").strip():
        failures.append("issuer_name must be non-empty")
    if not valid_date(profile.get("as_of")):
        failures.append(f"as_of: {profile.get('as_of')!r} is not YYYY-MM-DD")
    if profile.get("status") not in PROFILE_STATUS:
        failures.append(f"status {profile.get('status')!r} not in {sorted(PROFILE_STATUS)}")
    if profile.get("data_tier") not in DATA_TIERS:
        failures.append(f"data_tier {profile.get('data_tier')!r} not in {sorted(DATA_TIERS)}")
    if profile.get("opportunity_tier") not in OPPORTUNITY_TIERS:
        failures.append(f"opportunity_tier {profile.get('opportunity_tier')!r} not in "
                        f"{sorted(OPPORTUNITY_TIERS)}")
    if "tier" in profile:
        failures.append("ambiguous tier is forbidden; data_tier T1/T2/T3 and "
                        "opportunity_tier O1/O2/O3 are separate fields")
    if profile.get("status") == "COMPLETE" and profile.get("opportunity_tier") == "O3":
        failures.append("O3 is mapped-only coverage; completed-profile counts use O1 + O2")

    issuer_names, listing_to_issuer, mapped_placements = _mapping_indexes(root)
    if issuer_id not in issuer_names:
        failures.append(f"issuer_id {issuer_id!r} does not resolve in data/mappings/")
    elif str(issuer_names[issuer_id] or "").strip().casefold() != \
            str(profile.get("issuer_name") or "").strip().casefold():
        failures.append(f"issuer_name disagrees with the mapping census for {issuer_id}")

    listing_refs = profile.get("listing_refs")
    if not isinstance(listing_refs, list):
        failures.append("listing_refs must be a list of mapping listing_id values")
        listing_refs = []
    if len(listing_refs) != len(set(listing_refs)):
        failures.append("listing_refs contains duplicates")
    for i, listing_id in enumerate(listing_refs):
        if listing_to_issuer.get(listing_id) != issuer_id:
            failures.append(f"listing_refs[{i}] {listing_id!r} does not resolve to {issuer_id}")

    placements = profile.get("placements")
    if not isinstance(placements, list):
        failures.append("placements must be a list")
        placements = []
    seen = set()
    for i, placement in enumerate(placements):
        where = f"placements[{i}]"
        if not isinstance(placement, dict):
            failures.append(f"{where}: must be an object")
            continue
        key = (placement.get("chain_id"), placement.get("link_id"), issuer_id)
        if key in seen:
            failures.append(f"{where}: duplicate chain/link placement")
        seen.add(key)
        if key not in mapped_placements:
            failures.append(f"{where}: {key[:2]} does not resolve to an issuer placement "
                            "in data/mappings/")

    metrics = profile.get("metrics")
    if not isinstance(metrics, dict):
        failures.append("metrics must be an object")
    else:
        source_findings, _, _ = numeric_source_failures(metrics)
        failures.extend(source_findings)

    disposition = profile.get("disposition")
    if not isinstance(disposition, dict) or \
            disposition.get("state") not in DISPOSITIONS or \
            not str(disposition.get("basis") or "").strip():
        failures.append("disposition needs non-final state "
                        f"{sorted(DISPOSITIONS)} and a non-empty basis")

    for field in ("crowdedness_caveats", "catalysts", "risks", "data_gaps"):
        if not isinstance(profile.get(field), list):
            failures.append(f"{field} must be a list")
    if profile.get("status") == "COMPLETE":
        have, total = completeness(profile)
        if have != total:
            failures.append(f"COMPLETE profile has {have}/{total} required fields complete")
        missing_groups = sorted(METRIC_GROUPS - set(metrics or {}))
        if missing_groups:
            failures.append(f"COMPLETE profile missing metric groups: {missing_groups}")
        if not profile.get("catalysts") or not profile.get("risks"):
            failures.append("COMPLETE profile needs at least one catalyst and one risk")

    if not isinstance(profile.get("confidence_audit"), dict):
        failures.append("confidence_audit must be an object")
    if not isinstance(profile.get("changelog"), list):
        failures.append("changelog must be a list")
    failures.extend(_verdict_leaks(profile))
    return failures


def _head_json(root: Path, path: Path):
    try:
        rel = str(path.relative_to(root))
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"HEAD:{rel}"],
            capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout) if result.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def preservation_failures(root: Path, path: Path, current: dict) -> list[str]:
    prior = _head_json(root, path)
    if not isinstance(prior, dict):
        return []
    failures = []
    if prior.get("issuer_id") != current.get("issuer_id"):
        failures.append("issuer_id changed versus HEAD")
    if str(prior.get("issuer_name") or "").casefold() != \
            str(current.get("issuer_name") or "").casefold():
        failures.append("issuer_name changed versus HEAD")
    old_refs, new_refs = set(prior.get("listing_refs") or []), set(current.get("listing_refs") or [])
    if old_refs - new_refs:
        failures.append(f"listing_refs removed versus HEAD: {sorted(old_refs - new_refs)}")
    old_places = {(p.get("chain_id"), p.get("link_id")) for p in prior.get("placements") or []
                  if isinstance(p, dict)}
    new_places = {(p.get("chain_id"), p.get("link_id")) for p in current.get("placements") or []
                  if isinstance(p, dict)}
    if old_places - new_places:
        failures.append(f"placements removed versus HEAD: {sorted(old_places - new_places)}")
    if prior.get("status") == "COMPLETE" and current.get("status") != "COMPLETE":
        failures.append("status regressed from COMPLETE")
    if len(current.get("changelog") or []) < len(prior.get("changelog") or []):
        failures.append("changelog shrank versus HEAD")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root")
    args = parser.parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    folder = root / "data" / "companies"
    paths = sorted(folder.glob("*.json")) if folder.is_dir() else []
    paths = [path for path in paths if not path.name.startswith("_")]
    if not paths:
        print("check_profile: no data/companies profiles; profile gate not active yet.")
        return 0

    failures = []
    statuses = {key: 0 for key in PROFILE_STATUS}
    data_tiers = {key: 0 for key in DATA_TIERS}
    opportunity = {key: 0 for key in OPPORTUNITY_TIERS}
    numeric = inferred = 0
    for path in paths:
        profile = read_json(path)
        if isinstance(profile, dict):
            statuses[profile.get("status")] = statuses.get(profile.get("status"), 0) + 1
            data_tiers[profile.get("data_tier")] = data_tiers.get(profile.get("data_tier"), 0) + 1
            opportunity[profile.get("opportunity_tier")] = \
                opportunity.get(profile.get("opportunity_tier"), 0) + 1
            _, count, inf = numeric_source_failures(profile.get("metrics") or {})
            numeric += count
            inferred += inf
        for finding in validate_profile(root, path, profile):
            failures.append(f"{path.name}: {finding}")
        if isinstance(profile, dict):
            for finding in preservation_failures(root, path, profile):
                failures.append(f"{path.name}: preservation: {finding}")

    print(f"check_profile: {len(paths)} profiles, {statuses.get('COMPLETE', 0)} complete; "
          f"data tiers {data_tiers}; opportunity tiers {opportunity}")
    print(f"  source discipline: {numeric} numeric field(s) examined, "
          f"{inferred} official-source inference(s)")
    if failures:
        for finding in failures:
            print(f"  FAIL  {finding}")
        print(f"check_profile: FAILED with {len(failures)} finding(s)")
        return 1
    print("check_profile: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
