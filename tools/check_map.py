#!/usr/bin/env python3
"""Campaign issuer-map gate. Stdlib only, offline.

Validates normalized per-chain issuer maps under data/mappings/. The identity unit is an
issuer, never a ticker, and a placement is uniquely keyed by
(chain_id, link_id, issuer_id). One issuer may therefore occupy many links and themes
without padding any link's denominator.

Run: python3 tools/check_map.py [--root PATH]
Exit 0 clean, 1 on any failure.
"""
import argparse
import datetime
import json
import re
import subprocess
from pathlib import Path

MAP_STATUS = {"DRAFT", "ACTIVE", "COMPLETE"}
MAP_STATUS_ORDER = {"DRAFT": 0, "ACTIVE": 1, "COMPLETE": 2}
COVERAGE_STATUS = {"OPEN", "TARGET_MET", "EXHAUSTED"}
EVIDENCE_TAGS = {"VERIFIED", "INFERRED"}
ISSUER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


def valid_date(value) -> bool:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        return False
    try:
        datetime.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _source_failures(item, where: str, *, claim: bool = False) -> list[str]:
    out = []
    if not isinstance(item, dict):
        return [f"{where}: must be an object"]
    if claim and not str(item.get("claim") or "").strip():
        out.append(f"{where}: issuer-role evidence needs a non-empty claim")
    tag = item.get("tag")
    if tag not in EVIDENCE_TAGS:
        out.append(f"{where}.tag: {tag!r} not in {sorted(EVIDENCE_TAGS)}")
    if not str(item.get("source_name") or item.get("source") or "").strip():
        out.append(f"{where}: needs source_name")
    if not valid_date(item.get("source_date")):
        out.append(f"{where}.source_date: {item.get('source_date')!r} is not YYYY-MM-DD")
    url = item.get("url") or item.get("source_url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        out.append(f"{where}: needs a fetchable http(s) url")
    return out


def _chain_links(root: Path, chain_id) -> set:
    chain = read_json(root / "data" / "chains" / f"{chain_id}.json")
    if not isinstance(chain, dict):
        return set()
    return {str(link.get("id")) for link in chain.get("links") or []
            if isinstance(link, dict) and link.get("id")}


def validate_mapping(root: Path, path: Path, obj=None) -> list[str]:
    """Return schema and reference failures for one mapping file."""
    m = obj if isinstance(obj, dict) else read_json(path)
    if not isinstance(m, dict):
        return ["mapping must be a readable JSON object"]

    failures = []
    required = {
        "id", "chain_id", "as_of", "status", "target_issuers_per_link",
        "issuers", "listings", "placements", "link_coverage",
        "confidence_audit", "changelog",
    }
    missing = sorted(required - set(m))
    if missing:
        failures.append(f"missing keys: {', '.join(missing)}")

    chain_id = m.get("chain_id")
    if not isinstance(chain_id, str) or not chain_id:
        failures.append("chain_id must be non-empty")
    else:
        if path.stem != chain_id:
            failures.append(f"filename {path.name!r} must be {chain_id}.json")
        if m.get("id") != f"MAP-{chain_id}":
            failures.append(f"id must be 'MAP-{chain_id}'")
    if not valid_date(m.get("as_of")):
        failures.append(f"as_of: {m.get('as_of')!r} is not YYYY-MM-DD")
    if m.get("status") not in MAP_STATUS:
        failures.append(f"status {m.get('status')!r} not in {sorted(MAP_STATUS)}")
    target = m.get("target_issuers_per_link")
    if target != 10:
        failures.append("target_issuers_per_link must be 10 for the ten-theme campaign")

    links = _chain_links(root, chain_id)
    if chain_id and not links:
        failures.append(f"chain_id {chain_id!r} has no readable chain links")

    issuers = m.get("issuers")
    if not isinstance(issuers, list):
        failures.append("issuers must be a list")
        issuers = []
    issuer_ids = []
    for i, issuer in enumerate(issuers):
        where = f"issuers[{i}]"
        if not isinstance(issuer, dict):
            failures.append(f"{where}: must be an object")
            continue
        iid = issuer.get("issuer_id")
        if not isinstance(iid, str) or not ISSUER_ID_RE.fullmatch(iid):
            failures.append(f"{where}.issuer_id {iid!r} is not a stable safe identifier")
        issuer_ids.append(iid)
        if not str(issuer.get("name") or "").strip():
            failures.append(f"{where}: needs a non-empty name")
        # Data availability and opportunity quality are different axes. An issuer census
        # does not assign either implicitly.
        if "tier" in issuer:
            failures.append(f"{where}: ambiguous tier is forbidden; use data_tier and "
                            "opportunity_tier only in the reusable profile")
    if len(issuer_ids) != len(set(issuer_ids)):
        failures.append("issuers contain duplicate issuer_id values")
    issuer_set = set(issuer_ids)

    listings = m.get("listings")
    if not isinstance(listings, list):
        failures.append("listings must be a list")
        listings = []
    listing_ids = []
    listing_keys = []
    for i, listing in enumerate(listings):
        where = f"listings[{i}]"
        if not isinstance(listing, dict):
            failures.append(f"{where}: must be an object")
            continue
        needed = {"listing_id", "issuer_id", "ticker", "exchange"}
        miss = sorted(needed - set(listing))
        if miss:
            failures.append(f"{where}: missing {', '.join(miss)}")
        lid = listing.get("listing_id")
        listing_ids.append(lid)
        if not isinstance(lid, str) or not lid.strip():
            failures.append(f"{where}.listing_id must be non-empty")
        if listing.get("issuer_id") not in issuer_set:
            failures.append(f"{where}.issuer_id {listing.get('issuer_id')!r} is unresolved")
        if not str(listing.get("ticker") or "").strip() or \
                not str(listing.get("exchange") or "").strip():
            failures.append(f"{where}: ticker and exchange must be non-empty")
        listing_keys.append((str(listing.get("exchange") or "").casefold(),
                             str(listing.get("ticker") or "").casefold()))
    if len(listing_ids) != len(set(listing_ids)):
        failures.append("listings contain duplicate listing_id values")
    if len(listing_keys) != len(set(listing_keys)):
        failures.append("the same exchange/ticker appears more than once in listings")

    placements = m.get("placements")
    if not isinstance(placements, list):
        failures.append("placements must be a list")
        placements = []
    placement_keys = []
    issuers_by_link: dict[str, set] = {}
    for i, placement in enumerate(placements):
        where = f"placements[{i}]"
        if not isinstance(placement, dict):
            failures.append(f"{where}: must be an object")
            continue
        needed = {"chain_id", "link_id", "issuer_id", "role", "evidence"}
        miss = sorted(needed - set(placement))
        if miss:
            failures.append(f"{where}: missing {', '.join(miss)}")
        key = (placement.get("chain_id"), placement.get("link_id"),
               placement.get("issuer_id"))
        placement_keys.append(key)
        if placement.get("chain_id") != chain_id:
            failures.append(f"{where}.chain_id {placement.get('chain_id')!r} does not "
                            f"match map chain_id {chain_id!r}")
        link_id = placement.get("link_id")
        if links and str(link_id) not in links:
            failures.append(f"{where}.link_id {link_id!r} is not a link of {chain_id}")
        iid = placement.get("issuer_id")
        if iid not in issuer_set:
            failures.append(f"{where}.issuer_id {iid!r} is unresolved")
        if not str(placement.get("role") or "").strip():
            failures.append(f"{where}.role must be non-empty")
        evidence = placement.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            failures.append(f"{where}: needs dated issuer-role evidence")
        else:
            for j, item in enumerate(evidence):
                failures.extend(_source_failures(
                    item, f"{where}.evidence[{j}]", claim=True))
        issuers_by_link.setdefault(str(link_id), set()).add(iid)
    if len(placement_keys) != len(set(placement_keys)):
        failures.append("duplicate (chain_id, link_id, issuer_id) placement key; "
                        "duplicate listings or roles never pad issuer coverage")

    coverage = m.get("link_coverage")
    if not isinstance(coverage, list):
        failures.append("link_coverage must be a list")
        coverage = []
    coverage_links = []
    for i, row in enumerate(coverage):
        where = f"link_coverage[{i}]"
        if not isinstance(row, dict):
            failures.append(f"{where}: must be an object")
            continue
        link_id = str(row.get("link_id"))
        coverage_links.append(link_id)
        if links and link_id not in links:
            failures.append(f"{where}.link_id {row.get('link_id')!r} is unresolved")
        status = row.get("status")
        if status not in COVERAGE_STATUS:
            failures.append(f"{where}.status {status!r} not in "
                            f"{sorted(COVERAGE_STATUS)}")
        actual = len({x for x in issuers_by_link.get(link_id, set()) if x in issuer_set})
        if row.get("distinct_issuer_count") != actual:
            failures.append(f"{where}.distinct_issuer_count is "
                            f"{row.get('distinct_issuer_count')!r}, computed {actual}; "
                            "counts use distinct issuers, never listings or duplicate roles")
        if status == "TARGET_MET" and actual < target:
            failures.append(f"{where}: TARGET_MET with {actual}/{target} distinct issuers")
        if status == "EXHAUSTED":
            if actual >= target:
                failures.append(f"{where}: EXHAUSTED is dishonest at {actual}/{target}; "
                                "the target is already met")
            if not str(row.get("exhausted_reason") or "").strip():
                failures.append(f"{where}: EXHAUSTED needs exhausted_reason")
            searches = row.get("searches")
            if not isinstance(searches, list) or not searches:
                failures.append(f"{where}: EXHAUSTED needs the searches actually run")
            else:
                for j, search in enumerate(searches):
                    sw = f"{where}.searches[{j}]"
                    if not isinstance(search, dict) or \
                            not str(search.get("query") or "").strip():
                        failures.append(f"{sw}: needs a non-empty query")
                        continue
                    failures.extend(_source_failures(search, sw))
        if status == "OPEN" and actual >= target:
            failures.append(f"{where}: OPEN despite meeting the {target}-issuer target")
    if len(coverage_links) != len(set(coverage_links)):
        failures.append("link_coverage contains duplicate link_id values")
    if links and set(coverage_links) != links:
        failures.append("link_coverage must contain every chain link exactly once; "
                        f"missing {sorted(links - set(coverage_links))}, "
                        f"extra {sorted(set(coverage_links) - links)}")
    if m.get("status") == "COMPLETE":
        open_links = [row.get("link_id") for row in coverage
                      if isinstance(row, dict) and row.get("status") == "OPEN"]
        if open_links:
            failures.append(f"COMPLETE map still has OPEN links: {open_links}")

    if not isinstance(m.get("confidence_audit"), dict):
        failures.append("confidence_audit must be an object")
    if not isinstance(m.get("changelog"), list):
        failures.append("changelog must be a list")
    return failures


def corpus_identity_failures(mappings: list[tuple[Path, dict]]) -> list[str]:
    """Stable issuer and listing identity across every chain map."""
    failures = []
    issuer_names: dict[str, tuple[str, str]] = {}
    names_to_id: dict[str, tuple[str, str]] = {}
    listings: dict[tuple[str, str], tuple[str, str]] = {}
    for path, mapping in mappings:
        for issuer in mapping.get("issuers") or []:
            if not isinstance(issuer, dict):
                continue
            iid = issuer.get("issuer_id")
            name = str(issuer.get("name") or "").strip()
            norm = re.sub(r"\s+", " ", name).casefold()
            if iid in issuer_names and issuer_names[iid][0] != norm:
                failures.append(f"{path.name}: issuer_id {iid!r} names {name!r}, but "
                                f"{issuer_names[iid][1]} names a different issuer")
            elif iid:
                issuer_names[iid] = (norm, path.name)
            if norm in names_to_id and names_to_id[norm][0] != iid:
                failures.append(f"{path.name}: issuer {name!r} is split across issuer_id "
                                f"{names_to_id[norm][0]!r} and {iid!r}")
            elif norm:
                names_to_id[norm] = (iid, path.name)
        for listing in mapping.get("listings") or []:
            if not isinstance(listing, dict):
                continue
            key = (str(listing.get("exchange") or "").casefold(),
                   str(listing.get("ticker") or "").casefold())
            iid = listing.get("issuer_id")
            if key in listings and listings[key][0] != iid:
                failures.append(f"{path.name}: listing {key[0]}:{key[1]} points to "
                                f"{iid!r}, but {listings[key][1]} points it to "
                                f"{listings[key][0]!r}")
            elif all(key):
                listings[key] = (iid, path.name)
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
    """Permanent-memory checks that can be compared safely with git HEAD."""
    prior = _head_json(root, path)
    if not isinstance(prior, dict):
        return []
    failures = []
    if MAP_STATUS_ORDER.get(current.get("status"), -1) < \
            MAP_STATUS_ORDER.get(prior.get("status"), -1):
        failures.append(f"status regressed {prior.get('status')} -> {current.get('status')}")

    def ids(obj, field, key):
        return {x.get(key) for x in obj.get(field) or [] if isinstance(x, dict)}

    for field, key in (("issuers", "issuer_id"), ("listings", "listing_id")):
        gone = ids(prior, field, key) - ids(current, field, key)
        if gone:
            failures.append(f"{field} removed versus HEAD: {sorted(gone)}")
    old_places = {(x.get("chain_id"), x.get("link_id"), x.get("issuer_id"))
                  for x in prior.get("placements") or [] if isinstance(x, dict)}
    new_places = {(x.get("chain_id"), x.get("link_id"), x.get("issuer_id"))
                  for x in current.get("placements") or [] if isinstance(x, dict)}
    if old_places - new_places:
        failures.append(f"placements removed versus HEAD: {sorted(old_places - new_places)}")
    old_closed = {x.get("link_id") for x in prior.get("link_coverage") or []
                  if isinstance(x, dict) and x.get("status") in {"TARGET_MET", "EXHAUSTED"}}
    now_open = {x.get("link_id") for x in current.get("link_coverage") or []
                if isinstance(x, dict) and x.get("status") == "OPEN"}
    if old_closed & now_open:
        failures.append(f"closed link searches regressed to OPEN: {sorted(old_closed & now_open)}")
    if len(current.get("changelog") or []) < len(prior.get("changelog") or []):
        failures.append("changelog shrank versus HEAD")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root")
    args = parser.parse_args()
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    folder = root / "data" / "mappings"
    paths = sorted(folder.glob("*.json")) if folder.is_dir() else []
    paths = [path for path in paths if not path.name.startswith("_")]
    if not paths:
        print("check_map: no data/mappings files; campaign map gate not active yet.")
        return 0

    mappings = []
    failures = []
    links = placements = distinct_pairs = exhausted = 0
    for path in paths:
        obj = read_json(path)
        if isinstance(obj, dict):
            mappings.append((path, obj))
            links += len(obj.get("link_coverage") or [])
            placements += len(obj.get("placements") or [])
            distinct_pairs += len({(p.get("link_id"), p.get("issuer_id"))
                                   for p in obj.get("placements") or []
                                   if isinstance(p, dict)})
            exhausted += sum(1 for row in obj.get("link_coverage") or []
                             if isinstance(row, dict) and row.get("status") == "EXHAUSTED")
        for finding in validate_mapping(root, path, obj):
            failures.append(f"{path.name}: {finding}")
        if isinstance(obj, dict):
            for finding in preservation_failures(root, path, obj):
                failures.append(f"{path.name}: preservation: {finding}")
    failures.extend(corpus_identity_failures(mappings))

    print(f"check_map: {len(paths)} maps, {links} links, {distinct_pairs}/{placements} "
          f"distinct issuer placements, {exhausted} EXHAUSTED searches examined")
    if failures:
        for finding in failures:
            print(f"  FAIL  {finding}")
        print(f"check_map: FAILED with {len(failures)} finding(s)")
        return 1
    print("check_map: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
