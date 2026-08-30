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

from check_map import (
    ISSUER_ID_RE,
    read_json,
    valid_date,
)

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
SELECTION_DIMENSIONS = {
    "occurrence_strength", "economic_impact", "unmappedness",
    "public_market_reach", "overlap",
}
CANDIDATE_DISPOSITIONS = {"SELECTED", "ALTERNATE", "EXCLUDED"}


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


def _nonempty(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None


def _identity(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def validated_public_listings(mapping: dict) -> dict[str, dict]:
    """Listing identities that satisfy the map gate's issuer and market fields."""
    issuer_names = {
        issuer_id: _identity(issuer.get("name"))
        for issuer in mapping.get("issuers") or []
        if isinstance(issuer, dict)
        if (issuer_id := _identity(issuer.get("issuer_id")))
        if ISSUER_ID_RE.fullmatch(issuer_id)
        if _identity(issuer.get("name"))
    }
    listings = {}
    for listing in mapping.get("listings") or []:
        if not isinstance(listing, dict):
            continue
        listing_id = _identity(listing.get("listing_id"))
        issuer_id = _identity(listing.get("issuer_id"))
        if not all((
            listing_id,
            issuer_id in issuer_names,
            _identity(listing.get("ticker")),
            _identity(listing.get("exchange")),
        )):
            continue
        listings.setdefault(listing_id, listing)
    return listings


def canonical_mapped_placements(mappings, chain_ids=None) -> set[tuple[str, str, str]]:
    """Current listed-issuer placements, the sole campaign mapping denominator."""
    documents = mappings.values() if isinstance(mappings, dict) else mappings or []
    selected_chains = (
        {_identity(chain_id) for chain_id in chain_ids}
        if chain_ids is not None else None
    )
    placements = set()
    for mapping in documents:
        if not isinstance(mapping, dict):
            continue
        chain_id = _identity(mapping.get("chain_id"))
        if not chain_id or (
            selected_chains is not None and chain_id not in selected_chains
        ):
            continue
        listed_issuers = {
            _identity(listing.get("issuer_id"))
            for listing in validated_public_listings(mapping).values()
        }
        for placement in mapping.get("placements") or []:
            if not isinstance(placement, dict):
                continue
            if placement.get("status") != "ACTIVE":
                continue
            placement_chain = _identity(placement.get("chain_id"))
            link_id = _identity(placement.get("link_id"))
            issuer_id = _identity(placement.get("issuer_id"))
            if (
                placement_chain == chain_id
                and link_id
                and issuer_id in listed_issuers
            ):
                placements.add((chain_id, link_id, issuer_id))
    return placements


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
    screen_records = _objects(root / "data" / "screens")
    screens = [obj for _, obj in screen_records]
    stocks = [obj for _, obj in _objects(root / "data" / "stocks")
              if not obj.get("fixture")]

    listings = {}
    for chain_id, mapping in maps.items():
        for listing_id, listing in validated_public_listings(mapping).items():
            listings[(chain_id, listing_id)] = listing
    screen_refs = {}
    for path, screen in screen_records:
        for ref in (screen.get("id"), path.name, path.stem, str(path.relative_to(root))):
            if ref:
                screen_refs[ref] = screen
    return {
        "maps": maps,
        "profiles": profiles,
        "chains": chains,
        "screens": screens,
        "stocks": stocks,
        "listings": listings,
        "placements": canonical_mapped_placements(maps),
        "screen_refs": screen_refs,
    }


def _mapped_profile_placements(profile: dict, placements: set) -> set:
    issuer_id = _identity(profile.get("issuer_id"))
    if not issuer_id:
        return set()
    return {
        (chain_id, link_id, issuer_id)
        for placement in profile.get("placements") or []
        if isinstance(placement, dict)
        if (chain_id := _identity(placement.get("chain_id")))
        if (link_id := _identity(placement.get("link_id")))
        if (chain_id, link_id, issuer_id) in placements
    }


def _profile_handoff(profile: dict, inventory: dict) -> dict | None:
    basis = profile.get("selection_basis")
    handoff = basis.get("screen_handoff") if isinstance(basis, dict) else None
    if not isinstance(handoff, dict):
        return None
    issuer_id = profile.get("issuer_id")
    chain_id = handoff.get("chain_id")
    link_id = handoff.get("link_id")
    listing_id = handoff.get("listing_id")
    if not all(str(value or "").strip()
               for value in (chain_id, link_id, listing_id, handoff.get("screen_ref"))):
        return None
    if (chain_id, link_id, issuer_id) not in inventory["placements"]:
        return None
    listing = inventory["listings"].get((chain_id, listing_id))
    if not isinstance(listing, dict) or listing.get("issuer_id") != issuer_id:
        return None
    listing_refs = profile.get("listing_refs")
    if not isinstance(listing_refs, list) or listing_id not in listing_refs:
        return None
    screen = inventory["screen_refs"].get(handoff.get("screen_ref"))
    if not isinstance(screen, dict) or screen.get("chain_id") != chain_id:
        return None
    rows = [
        row for bucket in (screen.get("buckets") or {}).values()
        for row in (bucket or []) if isinstance(row, dict)
        and row.get("issuer_id") == issuer_id
        and row.get("listing_id") == listing_id
        and row.get("link_id") == link_id
        and row.get("ticker") == listing.get("ticker")
    ]
    return handoff if rows else None


def _stock_matches(profile: dict, stock: dict, inventory: dict, *,
                   require_final: bool = True) -> bool:
    if require_final and stock.get("status") != "FINAL":
        return False
    if not require_final and stock.get("status") not in {"DRAFT", "FINAL"}:
        return False
    handoff = _profile_handoff(profile, inventory)
    if not handoff:
        return False
    issuer_id = profile.get("issuer_id")
    listing_id = handoff.get("listing_id")
    chain_id = handoff.get("chain_id")
    listing = inventory["listings"].get((chain_id, listing_id)) or {}
    return stock.get("issuer_id") == issuer_id and \
        stock.get("listing_id") == listing_id and \
        stock.get("chain_id") == chain_id and \
        stock.get("link_id") == handoff.get("link_id") and \
        stock.get("ticker") == listing.get("ticker")


def _actual_theme_stage(theme: dict, inventory, targets: dict) -> str:
    maps = inventory["maps"]
    profiles = inventory["profiles"]
    chains = inventory["chains"]
    screens = inventory["screens"]
    stocks = inventory["stocks"]
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
                 and any(
                     placement_chain == chain_id
                     for placement_chain, _, _ in _mapped_profile_placements(
                         profile, inventory["placements"])
                 )]
    if len({profile.get("issuer_id") for profile in completed}) < \
            targets.get("profiles_per_theme_min", 10):
        return stage
    stage = "PROFILED"
    if not any(screen.get("chain_id") == chain_id for screen in screens):
        return stage
    stage = "SCREENED"
    theme_o1 = [
        profile for profile in profiles
        if profile.get("opportunity_tier") == "O1"
        and (_profile_handoff(profile, inventory) or {}).get("chain_id") == chain_id
    ]
    final = [
        profile for profile in theme_o1
        if any(_stock_matches(profile, stock, inventory) for stock in stocks)
    ]
    dived = any(
        _stock_matches(profile, stock, inventory, require_final=False)
        for profile in theme_o1 for stock in stocks
    )
    no_name = isinstance(theme.get("no_candidate_finding"), dict) and \
        not _source_finding_failures(theme["no_candidate_finding"], "no_candidate_finding")
    if (theme_o1 and len(final) == len(theme_o1)) or (not theme_o1 and no_name):
        return "COMPLETE"
    if dived:
        return "DIVED"
    return stage


def compute_campaign_completion(root: Path, campaign: dict) -> dict:
    """Compute campaign counts from normalized stores, deduplicated by issuer_id."""
    inventory = _inventory(root)
    profiles = inventory["profiles"]
    stocks = inventory["stocks"]
    themes = [theme for theme in campaign.get("themes") or [] if isinstance(theme, dict)]
    theme_chains = {theme.get("chain_id") for theme in themes if theme.get("chain_id")}
    campaign_placements = {
        placement for placement in inventory["placements"]
        if placement[0] in theme_chains
    }
    mapped_issuers = {placement[2] for placement in campaign_placements}
    by_issuer = {
        issuer_id: profile for profile in profiles
        if (issuer_id := _identity(profile.get("issuer_id"))) in mapped_issuers
        if _mapped_profile_placements(profile, campaign_placements)
    }
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
        if any(_stock_matches(profile, stock, inventory) for stock in stocks)
    }

    per_theme = []
    for theme in themes:
        chain_id = theme.get("chain_id")
        theme_placements = {
            placement for placement in campaign_placements
            if placement[0] == chain_id
        }
        theme_mapped_issuers = {placement[2] for placement in theme_placements}
        theme_profiles = {iid: profile for iid, profile in by_issuer.items()
                          if any(
                              placement[0] == chain_id
                              for placement in _mapped_profile_placements(
                                  profile, theme_placements)
                          )}
        theme_complete = {iid: profile for iid, profile in theme_profiles.items()
                          if profile.get("status") == "COMPLETE"
                          and profile.get("opportunity_tier") in {"O1", "O2"}}
        theme_o1 = {
            iid: profile for iid, profile in theme_profiles.items()
            if profile.get("opportunity_tier") == "O1"
            and (_profile_handoff(profile, inventory) or {}).get("chain_id") == chain_id
        }
        theme_final = {
            iid for iid, profile in theme_o1.items()
            if any(_stock_matches(profile, stock, inventory) for stock in stocks)
        }
        per_theme.append({
            "theme_id": theme.get("theme_id"),
            "chain_id": chain_id,
            "stage_computed": _actual_theme_stage(
                theme, inventory, campaign.get("targets") or LOCKED_TARGETS),
            "distinct_mapped_issuers": len(theme_mapped_issuers),
            "completed_profiles": len(theme_complete),
            "o1": len(theme_o1),
            "o1_final": len(theme_final),
        })
    return {
        "themes_selected": len(themes),
        "themes_complete": sum(1 for row in per_theme
                               if row["stage_computed"] == "COMPLETE"),
        "distinct_mapped_issuers": len(mapped_issuers),
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
        basis = {}
    else:
        if not valid_date(basis.get("as_of")):
            failures.append("selection_basis.as_of must be YYYY-MM-DD")
        if not isinstance(basis.get("candidates_examined"), int) or \
                basis.get("candidates_examined", 0) < 0:
            failures.append("selection_basis.candidates_examined must be a non-negative integer")
        if not isinstance(basis.get("criteria"), list) or not basis.get("criteria"):
            failures.append("selection_basis.criteria must be a non-empty frozen list")
        elif not all(isinstance(item, str) and item.strip()
                     for item in basis.get("criteria")):
            failures.append("selection_basis.criteria entries must be non-empty strings")
    candidates = basis.get("candidates")
    if not isinstance(candidates, list):
        failures.append("selection_basis.candidates must be a list of candidate records")
        candidates = []
    if basis.get("candidates_examined") != len(candidates):
        failures.append(
            "selection_basis.candidates_examined must equal the candidate-record count "
            f"({basis.get('candidates_examined')!r} written, {len(candidates)} records)")
    candidate_ids = []
    selected_candidate_signals = []
    for i, candidate in enumerate(candidates):
        where = f"selection_basis.candidates[{i}]"
        if not isinstance(candidate, dict):
            failures.append(f"{where}: must be an object")
            continue
        needed = {
            "candidate_id", "title", "occurrence", "dimensions", "disposition", "reason",
        }
        missing_candidate = sorted(needed - set(candidate))
        if missing_candidate:
            failures.append(f"{where}: missing {', '.join(missing_candidate)}")
        candidate_id = candidate.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id.strip():
            candidate_ids.append(candidate_id)
        else:
            failures.append(f"{where}.candidate_id must be non-empty")
        if not str(candidate.get("title") or "").strip():
            failures.append(f"{where}.title must be non-empty")
        occurrence = candidate.get("occurrence")
        if not isinstance(occurrence, dict):
            failures.append(f"{where}.occurrence must be an evidence object")
        else:
            if not str(occurrence.get("reference") or "").strip():
                failures.append(f"{where}.occurrence.reference must be non-empty")
            failures.extend(_source_finding_failures(
                occurrence, f"{where}.occurrence"))
        dimensions = candidate.get("dimensions")
        if not isinstance(dimensions, dict):
            failures.append(f"{where}.dimensions must be an object")
        else:
            for dimension in sorted(SELECTION_DIMENSIONS):
                if dimension not in dimensions or not _nonempty(dimensions.get(dimension)):
                    failures.append(f"{where}.dimensions.{dimension} must be non-empty")
        disposition = candidate.get("disposition")
        if disposition not in CANDIDATE_DISPOSITIONS:
            failures.append(
                f"{where}.disposition {disposition!r} not in "
                f"{sorted(CANDIDATE_DISPOSITIONS)}")
        if not str(candidate.get("reason") or "").strip():
            failures.append(f"{where}.reason must be non-empty")
        if disposition == "SELECTED":
            if not str(candidate.get("signal_id") or "").strip():
                failures.append(f"{where}: SELECTED candidate needs signal_id")
            else:
                selected_candidate_signals.append(candidate.get("signal_id"))
    if len(candidate_ids) != len(set(candidate_ids)):
        failures.append("selection_basis.candidates contain duplicate candidate_id values")
    if campaign.get("status") in {"SELECTED", "ACTIVE", "COMPLETE"}:
        if len(candidates) < 25:
            failures.append("selected campaign requires at least 25 candidate records")
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
    ranks = []
    for i, theme in enumerate(themes):
        where = f"themes[{i}]"
        if not isinstance(theme, dict):
            failures.append(f"{where}: must be an object")
            continue
        needed = {"theme_id", "signal_id", "chain_id", "title", "stage"}
        if campaign.get("status") in {"SELECTED", "ACTIVE", "COMPLETE"}:
            needed |= {"rank", "rationale"}
        miss = sorted(needed - set(theme))
        if miss:
            failures.append(f"{where}: missing {', '.join(miss)}")
        theme_ids.append(theme.get("theme_id"))
        signal_ids.append(theme.get("signal_id"))
        chain_ids.append(theme.get("chain_id"))
        ranks.append(theme.get("rank"))
        if not str(theme.get("title") or "").strip():
            failures.append(f"{where}.title must be non-empty")
        if campaign.get("status") in {"SELECTED", "ACTIVE", "COMPLETE"}:
            if isinstance(theme.get("rank"), bool) or \
                    not isinstance(theme.get("rank"), int) or theme.get("rank") < 1:
                failures.append(f"{where}.rank must be a positive integer")
            if not str(theme.get("rationale") or "").strip():
                failures.append(f"{where}.rationale must be non-empty")
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
    if campaign.get("status") in {"SELECTED", "ACTIVE", "COMPLETE"}:
        if sorted(rank for rank in ranks if isinstance(rank, int) and not isinstance(rank, bool)) \
                != list(range(1, len(themes) + 1)):
            failures.append("selected theme ranks must be a permutation of 1..theme count")
        if set(selected_candidate_signals) != set(signal_ids):
            failures.append(
                "SELECTED candidate signal_id values must exactly match selected themes")
        if len(selected_candidate_signals) != len(set(selected_candidate_signals)):
            failures.append("SELECTED candidate signal_id values must be unique")
        if len(selected_candidate_signals) != len(themes):
            failures.append("selected campaigns need exactly one SELECTED candidate per theme")

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
    per_counts = {row["theme_id"]: row for row in computed["per_theme"]}
    for i, theme in enumerate(themes):
        written_stage = theme.get("stage")
        actual_stage = per_actual.get(theme.get("theme_id"), "SELECTED")
        if written_stage in STAGE_ORDER and STAGE_ORDER[written_stage] > STAGE_ORDER[actual_stage]:
            failures.append(f"themes[{i}].stage {written_stage} is ahead of evidence-backed "
                            f"stage {actual_stage}")
        if campaign.get("status") == "COMPLETE" and written_stage != "COMPLETE":
            failures.append(f"themes[{i}] is {written_stage}, not COMPLETE")
        if theme.get("no_candidate_finding") is not None and \
                per_counts.get(theme.get("theme_id"), {}).get("o1", 0):
            failures.append(
                f"themes[{i}] has a no_candidate_finding despite having O1 handoffs")
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
    frozen_statuses = {"SELECTED", "ACTIVE", "COMPLETE"}
    if prior.get("status") in frozen_statuses and current.get("status") in frozen_statuses:
        if prior.get("selection_basis") != current.get("selection_basis"):
            failures.append("frozen selection_basis changed versus HEAD")
        for theme_id in set(old_themes) & set(new_themes):
            for field in ("signal_id", "chain_id", "title", "rank", "rationale"):
                if old_themes[theme_id].get(field) != new_themes[theme_id].get(field):
                    failures.append(
                        f"theme {theme_id} frozen {field} changed versus HEAD")
    for field in ("alternates", "exclusions"):
        before = prior.get(field) or []
        after = current.get(field) or []
        if len(after) < len(before) or after[:len(before)] != before:
            failures.append(f"{field} is not append-only versus HEAD")
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
