#!/usr/bin/env python3
"""Ten-theme campaign gate. Stdlib only, offline.

Campaign manifests freeze selection and report computed coverage. COMPLETE is deliberately
hard: exactly ten themes, at least 200 distinct completed O1/O2 issuer profiles, 30 to 60
O1 issuers, and a FINAL Stocky dive for every O1 issuer.

Run: python3 tools/check_campaign.py [--root PATH]
Exit 0 clean, 1 on any failure.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

from check_map import read_json, valid_date

CAMPAIGN_ID_RE = re.compile(r"^CAMP-\d{8}-\d{2}$")
CAMPAIGN_STATUS = {"DRAFT", "SELECTED", "ACTIVE", "COMPLETE"}
CAMPAIGN_STATUS_ORDER = {"DRAFT": 0, "SELECTED": 1, "ACTIVE": 2, "COMPLETE": 3}
THEME_STAGES = (
    "SELECTED", "CHAINED", "HEATED", "SCENARIOS",
    "MAPPED", "PROFILED", "SCREENED", "DIVED", "COMPLETE",
)
STAGE_ORDER = {stage: i for i, stage in enumerate(THEME_STAGES)}
LOCKED_TARGETS = {
    "theme_count": 10,
    "issuers_per_link": 10,
    "completed_profiles_min": 200,
    "profiles_per_theme_min": 10,
    "o1_min": 30,
    "o1_max": 60,
}


def _objects(folder: Path) -> list[tuple[Path, dict]]:
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if path.name.startswith("_"):
            continue
        obj = read_json(path)
        if isinstance(obj, dict):
            out.append((path, obj))
    return out


def _theme_ids(rows) -> list:
    out = []
    for row in rows or []:
        if isinstance(row, dict):
            out.append(row.get("theme_id") or row.get("signal_id") or row.get("chain_id"))
        else:
            out.append(row)
    return out


def _source_finding_failures(finding, where: str) -> list[str]:
    if not isinstance(finding, dict):
        return [f"{where}: must be an evidence object"]
    failures = []
    if not str(finding.get("claim") or "").strip():
        failures.append(f"{where}: needs a non-empty claim")
    if not str(finding.get("source_name") or finding.get("source") or "").strip():
        failures.append(f"{where}: needs source_name")
    if not valid_date(finding.get("source_date")):
        failures.append(f"{where}.source_date is not YYYY-MM-DD")
    url = finding.get("url") or finding.get("source_url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        failures.append(f"{where}: needs a fetchable http(s) url")
    return failures


def _inventory(root: Path):
    maps = {obj.get("chain_id"): obj for _, obj in _objects(root / "data" / "mappings")}
    profiles = [obj for _, obj in _objects(root / "data" / "companies")]
    chains = {obj.get("id"): obj for _, obj in _objects(root / "data" / "chains")}
    screens = [obj for _, obj in _objects(root / "data" / "screens")]
    stocks = [obj for _, obj in _objects(root / "data" / "stocks")
              if not obj.get("fixture")]

    tickers_by_issuer: dict[str, set[str]] = {}
    for mapping in maps.values():
        for listing in mapping.get("listings") or []:
            if isinstance(listing, dict) and listing.get("issuer_id") and listing.get("ticker"):
                tickers_by_issuer.setdefault(listing["issuer_id"], set()).add(
                    str(listing["ticker"]))
    return maps, profiles, chains, screens, stocks, tickers_by_issuer


def _profile_chains(profile: dict) -> set:
    return {p.get("chain_id") for p in profile.get("placements") or []
            if isinstance(p, dict) and p.get("chain_id")}


def _stock_matches(profile: dict, stock: dict, tickers_by_issuer: dict[str, set]) -> bool:
    if stock.get("status") != "FINAL":
        return False
    issuer_id = profile.get("issuer_id")
    identity_match = stock.get("issuer_id") == issuer_id or \
        str(stock.get("ticker") or "") in tickers_by_issuer.get(issuer_id, set())
    return identity_match and stock.get("chain_id") in _profile_chains(profile)


def _actual_theme_stage(theme: dict, inventory, targets: dict) -> str:
    maps, profiles, chains, screens, stocks, tickers_by_issuer = inventory
    chain_id = theme.get("chain_id")
    chain = chains.get(chain_id)
    if not isinstance(chain, dict):
        return "SELECTED"
    stage = "CHAINED"
    links = chain.get("links") or []
    if links and all(isinstance(link.get("heat"), dict) and
                     link["heat"].get("verdict") for link in links):
        stage = "HEATED"
    else:
        return stage
    if chain.get("scenarios"):
        stage = "SCENARIOS"
    else:
        return stage
    mapping = maps.get(chain_id)
    if not isinstance(mapping, dict) or mapping.get("status") != "COMPLETE":
        return stage
    stage = "MAPPED"
    completed = [profile for profile in profiles
                 if profile.get("status") == "COMPLETE"
                 and profile.get("opportunity_tier") in {"O1", "O2"}
                 and chain_id in _profile_chains(profile)]
    if len({profile.get("issuer_id") for profile in completed}) < \
            targets.get("profiles_per_theme_min", 10):
        return stage
    stage = "PROFILED"
    if not any(screen.get("chain_id") == chain_id for screen in screens):
        return stage
    stage = "SCREENED"
    final = any(stock.get("status") == "FINAL" and stock.get("chain_id") == chain_id
                for stock in stocks)
    dived = any(stock.get("chain_id") == chain_id for stock in stocks)
    no_name = isinstance(theme.get("no_candidate_finding"), dict) and \
        not _source_finding_failures(theme["no_candidate_finding"], "no_candidate_finding")
    if final or no_name:
        return "COMPLETE"
    if dived:
        return "DIVED"
    return stage


def compute_campaign_completion(root: Path, campaign: dict) -> dict:
    """Compute campaign counts from normalized stores, deduplicated by issuer_id."""
    inventory = _inventory(root)
    maps, profiles, _, _, stocks, tickers_by_issuer = inventory
    themes = [theme for theme in campaign.get("themes") or [] if isinstance(theme, dict)]
    theme_chains = {theme.get("chain_id") for theme in themes if theme.get("chain_id")}
    associated = [profile for profile in profiles if _profile_chains(profile) & theme_chains]
    by_issuer = {profile.get("issuer_id"): profile for profile in associated
                 if profile.get("issuer_id")}
    complete = {iid: profile for iid, profile in by_issuer.items()
                if profile.get("status") == "COMPLETE"
                and profile.get("opportunity_tier") in {"O1", "O2"}}
    tiers = {tier: sum(1 for profile in by_issuer.values()
                       if profile.get("opportunity_tier") == tier)
             for tier in ("O1", "O2", "O3")}
    o1 = {iid: profile for iid, profile in by_issuer.items()
          if profile.get("opportunity_tier") == "O1"}
    o1_complete = {iid for iid, profile in o1.items()
                   if profile.get("status") == "COMPLETE"}
    final_ids = {
        iid for iid, profile in o1.items()
        if any(_stock_matches(profile, stock, tickers_by_issuer) for stock in stocks)
    }

    per_theme = []
    for theme in themes:
        chain_id = theme.get("chain_id")
        theme_profiles = {iid: profile for iid, profile in by_issuer.items()
                          if chain_id in _profile_chains(profile)}
        theme_complete = {iid: profile for iid, profile in theme_profiles.items()
                          if profile.get("status") == "COMPLETE"
                          and profile.get("opportunity_tier") in {"O1", "O2"}}
        theme_o1 = {iid: profile for iid, profile in theme_profiles.items()
                    if profile.get("opportunity_tier") == "O1"}
        theme_final = {
            iid for iid, profile in theme_o1.items()
            if any(_stock_matches(profile, stock, tickers_by_issuer)
                   and stock.get("chain_id") == chain_id for stock in stocks)
        }
        mapping = maps.get(chain_id) or {}
        placement_keys = {
            (p.get("chain_id"), p.get("link_id"), p.get("issuer_id"))
            for p in mapping.get("placements") or [] if isinstance(p, dict)
        }
        per_theme.append({
            "theme_id": theme.get("theme_id"),
            "chain_id": chain_id,
            "stage_computed": _actual_theme_stage(
                theme, inventory, campaign.get("targets") or LOCKED_TARGETS),
            "distinct_issuer_placements": len(placement_keys),
            "completed_profiles": len(theme_complete),
            "o1": len(theme_o1),
            "o1_final": len(theme_final),
        })
    return {
        "themes_selected": len(themes),
        "themes_complete": sum(1 for row in per_theme
                               if row["stage_computed"] == "COMPLETE"),
        "distinct_mapped_issuers": len({
            issuer.get("issuer_id") for mapping in maps.values()
            if mapping.get("chain_id") in theme_chains
            for issuer in mapping.get("issuers") or []
            if isinstance(issuer, dict) and issuer.get("issuer_id")
        }),
        "completed_profiles": len(complete),
        "opportunity_tiers": tiers,
        "o1_complete": len(o1_complete),
        "o1_final": len(final_ids),
        "per_theme": per_theme,
    }


def completion_gate_failures(campaign: dict, computed: dict) -> list[str]:
    """The locked COMPLETE boundary, separated for scale-fixture tests."""
    if campaign.get("status") != "COMPLETE":
        return []
    failures = []
    themes = campaign.get("themes") or []
    if len(themes) != 10:
        failures.append(f"COMPLETE requires exactly 10 themes, found {len(themes)}")
    completed = computed.get("completed_profiles", 0)
    if completed < 200:
        failures.append(f"COMPLETE requires >=200 completed O1/O2 profiles, found {completed}")
    o1 = (computed.get("opportunity_tiers") or {}).get("O1", 0)
    if not 30 <= o1 <= 60:
        failures.append(f"COMPLETE requires 30-60 O1 issuers, found {o1}")
    if computed.get("o1_complete", 0) != o1:
        failures.append(f"COMPLETE requires every O1 profile complete, found "
                        f"{computed.get('o1_complete', 0)}/{o1}")
    o1_final = computed.get("o1_final", 0)
    if o1_final != o1:
        failures.append(f"COMPLETE requires FINAL coverage for every O1 issuer, "
                        f"found {o1_final}/{o1}")
    if computed.get("themes_complete") != 10:
        failures.append(f"COMPLETE requires every theme complete, found "
                        f"{computed.get('themes_complete', 0)}/10")
    minimum = (campaign.get("targets") or LOCKED_TARGETS).get("profiles_per_theme_min", 10)
    thin = [row.get("theme_id") for row in computed.get("per_theme") or []
            if row.get("completed_profiles", 0) < minimum]
    if thin:
        failures.append(f"COMPLETE themes below {minimum} completed profiles: {thin}")
    return failures


def validate_campaign(root: Path, path: Path, obj=None) -> list[str]:
    campaign = obj if isinstance(obj, dict) else read_json(path)
    if not isinstance(campaign, dict):
        return ["campaign must be a readable JSON object"]
    failures = []
    required = {
        "id", "as_of", "status", "selection_basis", "themes", "alternates",
        "exclusions", "targets", "completion", "blockers",
        "confidence_audit", "changelog",
    }
    missing = sorted(required - set(campaign))
    if missing:
        failures.append(f"missing keys: {', '.join(missing)}")
    campaign_id = campaign.get("id")
    if not isinstance(campaign_id, str) or not CAMPAIGN_ID_RE.fullmatch(campaign_id):
        failures.append(f"id {campaign_id!r} must match CAMP-YYYYMMDD-NN")
    elif path.stem != campaign_id:
        failures.append(f"filename {path.name!r} must match campaign id {campaign_id!r}")
    if not valid_date(campaign.get("as_of")):
        failures.append(f"as_of {campaign.get('as_of')!r} is not YYYY-MM-DD")
    if campaign.get("status") not in CAMPAIGN_STATUS:
        failures.append(f"status {campaign.get('status')!r} not in "
                        f"{sorted(CAMPAIGN_STATUS)}")

    targets = campaign.get("targets")
    if not isinstance(targets, dict):
        failures.append("targets must be an object")
        targets = {}
    for key, locked in LOCKED_TARGETS.items():
        if targets.get(key) != locked:
            failures.append(f"targets.{key} must be {locked}, found {targets.get(key)!r}")

    basis = campaign.get("selection_basis")
    if not isinstance(basis, dict):
        failures.append("selection_basis must be an object")
    else:
        if not valid_date(basis.get("as_of")):
            failures.append("selection_basis.as_of must be YYYY-MM-DD")
        if not isinstance(basis.get("candidates_examined"), int) or \
                basis.get("candidates_examined", 0) < 0:
            failures.append("selection_basis.candidates_examined must be a non-negative integer")
        if not isinstance(basis.get("criteria"), list) or not basis.get("criteria"):
            failures.append("selection_basis.criteria must be a non-empty frozen list")
        if campaign.get("status") in {"SELECTED", "ACTIVE", "COMPLETE"}:
            if basis.get("candidates_examined", 0) < 25:
                failures.append("selected campaign requires at least 25 candidates examined")
            if basis.get("frozen") is not True:
                failures.append("selected campaign requires selection_basis.frozen: true")

    themes = campaign.get("themes")
    if not isinstance(themes, list):
        failures.append("themes must be a list")
        themes = []
    if campaign.get("status") in {"SELECTED", "ACTIVE", "COMPLETE"} and len(themes) != 10:
        failures.append(f"{campaign.get('status')} campaign needs exactly 10 themes, "
                        f"found {len(themes)}")
    theme_ids = []
    signal_ids = []
    chain_ids = []
    for i, theme in enumerate(themes):
        where = f"themes[{i}]"
        if not isinstance(theme, dict):
            failures.append(f"{where}: must be an object")
            continue
        needed = {"theme_id", "signal_id", "chain_id", "title", "stage"}
        miss = sorted(needed - set(theme))
        if miss:
            failures.append(f"{where}: missing {', '.join(miss)}")
        theme_ids.append(theme.get("theme_id"))
        signal_ids.append(theme.get("signal_id"))
        chain_ids.append(theme.get("chain_id"))
        if not str(theme.get("title") or "").strip():
            failures.append(f"{where}.title must be non-empty")
        if theme.get("stage") not in STAGE_ORDER:
            failures.append(f"{where}.stage {theme.get('stage')!r} not in {list(THEME_STAGES)}")
        signal = read_json(root / "data" / "signals" / f"{theme.get('signal_id')}.json")
        if not isinstance(signal, dict):
            failures.append(f"{where}.signal_id {theme.get('signal_id')!r} is unresolved")
        chain = read_json(root / "data" / "chains" / f"{theme.get('chain_id')}.json")
        if theme.get("stage") != "SELECTED" and not isinstance(chain, dict):
            failures.append(f"{where}.chain_id {theme.get('chain_id')!r} is unresolved")
        if isinstance(chain, dict) and chain.get("signal_id") != theme.get("signal_id"):
            failures.append(f"{where}: chain.signal_id does not match theme.signal_id")
        if theme.get("no_candidate_finding") is not None:
            failures.extend(_source_finding_failures(
                theme["no_candidate_finding"], f"{where}.no_candidate_finding"))
    for label, values in (("theme_id", theme_ids), ("signal_id", signal_ids),
                          ("chain_id", chain_ids)):
        clean = [value for value in values if value is not None]
        if len(clean) != len(set(clean)):
            failures.append(f"themes contain duplicate {label} values")

    alternates = campaign.get("alternates")
    exclusions = campaign.get("exclusions")
    if not isinstance(alternates, list):
        failures.append("alternates must be a list")
        alternates = []
    if not isinstance(exclusions, list):
        failures.append("exclusions must be a list")
        exclusions = []
    selected_set = set(theme_ids)
    alt_ids = _theme_ids(alternates)
    excluded_ids = _theme_ids(exclusions)
    if selected_set & set(alt_ids):
        failures.append("a theme cannot be both selected and alternate")
    if len(alt_ids) != len(set(alt_ids)):
        failures.append("alternates contain duplicate theme identities")
    if len(excluded_ids) != len(set(excluded_ids)):
        failures.append("exclusions contain duplicate theme identities")
    for label, rows in (("alternates", alternates), ("exclusions", exclusions)):
        for i, row in enumerate(rows):
            if isinstance(row, dict) and not str(row.get("reason") or "").strip():
                failures.append(f"{label}[{i}] needs a reason")

    if not isinstance(campaign.get("completion"), dict):
        failures.append("completion must be an object computed by campaign_calibrate.py")
    if not isinstance(campaign.get("blockers"), list):
        failures.append("blockers must be a list")
    if not isinstance(campaign.get("confidence_audit"), dict):
        failures.append("confidence_audit must be an object")
    if not isinstance(campaign.get("changelog"), list):
        failures.append("changelog must be a list")

    computed = compute_campaign_completion(root, campaign)
    written = campaign.get("completion") or {}
    for key in ("themes_selected", "themes_complete", "distinct_mapped_issuers",
                "completed_profiles", "opportunity_tiers", "o1_complete",
                "o1_final", "per_theme"):
        if written.get(key) != computed.get(key):
            failures.append(f"completion.{key} is stale or hand-counted; written "
                            f"{written.get(key)!r}, computed {computed.get(key)!r}")
    per_actual = {row["theme_id"]: row["stage_computed"] for row in computed["per_theme"]}
    for i, theme in enumerate(themes):
        written_stage = theme.get("stage")
        actual_stage = per_actual.get(theme.get("theme_id"), "SELECTED")
        if written_stage in STAGE_ORDER and STAGE_ORDER[written_stage] > STAGE_ORDER[actual_stage]:
            failures.append(f"themes[{i}].stage {written_stage} is ahead of evidence-backed "
                            f"stage {actual_stage}")
        if campaign.get("status") == "COMPLETE" and written_stage != "COMPLETE":
            failures.append(f"themes[{i}] is {written_stage}, not COMPLETE")
    failures.extend(completion_gate_failures(campaign, computed))
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
    old_status = CAMPAIGN_STATUS_ORDER.get(prior.get("status"), -1)
    new_status = CAMPAIGN_STATUS_ORDER.get(current.get("status"), -1)
    if new_status < old_status:
        failures.append(f"campaign status regressed {prior.get('status')} -> "
                        f"{current.get('status')}")
    old_themes = {t.get("theme_id"): t for t in prior.get("themes") or []
                  if isinstance(t, dict)}
    new_themes = {t.get("theme_id"): t for t in current.get("themes") or []
                  if isinstance(t, dict)}
    if set(old_themes) - set(new_themes):
        failures.append(f"themes removed versus HEAD: {sorted(set(old_themes) - set(new_themes))}")
    for theme_id in set(old_themes) & set(new_themes):
        before = STAGE_ORDER.get(old_themes[theme_id].get("stage"), -1)
        after = STAGE_ORDER.get(new_themes[theme_id].get("stage"), -1)
        if after < before:
            failures.append(f"theme {theme_id} stage regressed "
                            f"{old_themes[theme_id].get('stage')} -> "
                            f"{new_themes[theme_id].get('stage')}")
    for field in ("alternates", "exclusions"):
        gone = set(_theme_ids(prior.get(field))) - set(_theme_ids(current.get(field)))
        if gone:
            failures.append(f"{field} removed versus HEAD: {sorted(gone)}")
    if prior.get("targets") != current.get("targets"):
        failures.append("locked campaign targets changed versus HEAD")
    if len(current.get("changelog") or []) < len(prior.get("changelog") or []):
        failures.append("changelog shrank versus HEAD")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root")
    args = parser.parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    folder = root / "data" / "campaigns"
    paths = sorted(folder.glob("CAMP-*.json")) if folder.is_dir() else []
    if not paths:
        print("check_campaign: no CAMP manifests; campaign gate not active yet.")
        return 0

    failures = []
    themes = completed = o1 = finals = 0
    for path in paths:
        campaign = read_json(path)
        if isinstance(campaign, dict):
            computed = compute_campaign_completion(root, campaign)
            themes += computed["themes_selected"]
            completed += computed["completed_profiles"]
            o1 += computed["opportunity_tiers"]["O1"]
            finals += computed["o1_final"]
        for finding in validate_campaign(root, path, campaign):
            failures.append(f"{path.name}: {finding}")
        if isinstance(campaign, dict):
            for finding in preservation_failures(root, path, campaign):
                failures.append(f"{path.name}: preservation: {finding}")

    print(f"check_campaign: {len(paths)} campaign(s), {themes} themes, "
          f"{completed} completed profiles, {o1} O1, {finals}/{o1} O1 FINAL")
    if failures:
        for finding in failures:
            print(f"  FAIL  {finding}")
        print(f"check_campaign: FAILED with {len(failures)} finding(s)")
        return 1
    print("check_campaign: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
