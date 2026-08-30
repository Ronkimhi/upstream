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
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

MAP_STATUS = {"DRAFT", "ACTIVE", "COMPLETE"}
MAP_STATUS_ORDER = {"DRAFT": 0, "ACTIVE": 1, "COMPLETE": 2}
COVERAGE_STATUS = {"OPEN", "TARGET_MET", "EXHAUSTED"}
PLACEMENT_STATUS = {"ACTIVE", "REJECTED", "SUPERSEDED", "PENDING"}
EVIDENCE_TAGS = {"VERIFIED", "INFERRED"}
LISTING_SOURCE_TYPES = {
    "OFFICIAL_EXCHANGE",
    "OFFICIAL_REGISTRY",
    "OFFICIAL_REGULATOR",
    "OFFICIAL_SECURITIES_FILING",
}
SEARCH_SOURCE_TYPES = {
    "OFFICIAL_EXCHANGE",
    "OFFICIAL_REGISTRY",
    "PRIMARY_ISSUER",
    "CREDIBLE_INDUSTRY",
}
OFFICIAL_SEARCH_SOURCE_TYPES = {"OFFICIAL_EXCHANGE", "OFFICIAL_REGISTRY"}
DISCOVERY_SEARCH_SOURCE_TYPES = {"PRIMARY_ISSUER", "CREDIBLE_INDUSTRY"}
SEARCH_RESULT_STATUS = {
    "QUALIFYING_NAMES_FOUND",
    "NO_QUALIFYING_NAMES",
    "MIXED_RESULTS",
}
AUDIT_STATUS = {"PASS", "FAIL"}
AUDIT_REVIEWER = "atlas-fresh-context"
AUDIT_REVIEW_MODES = {"FRESH_CONTEXT", "SELF_REVIEW"}
AUDIT_CONFLICT_FIELDS = (
    "identity_conflicts",
    "role_conflicts",
    "source_date_conflicts",
    "amendments_required",
)
MATERIAL_MAPPING_FIELDS = (
    "id",
    "chain_id",
    "target_issuers_per_link",
    "issuers",
    "listings",
    "placements",
    "link_coverage",
)
ISSUER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMON_COUNTRY_SECOND_LEVELS = {
    "co.in",
    "co.jp",
    "co.kr",
    "co.uk",
    "com.au",
    "com.br",
    "com.cn",
    "com.hk",
    "com.sg",
    "com.tw",
    "gov.cn",
    "gov.hk",
    "org.cn",
}


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


def active_placement(placement: dict) -> bool:
    """Only an explicit ACTIVE placement is current census coverage."""
    return isinstance(placement, dict) and placement.get("status") == "ACTIVE"


def valid_date(value) -> bool:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        return False
    try:
        datetime.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _utc_datetime(value):
    """Parse a timezone-aware UTC timestamp, returning None on any other shape."""
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != datetime.timedelta(0):
        return None
    return parsed


def mapping_fingerprint(mapping: dict) -> str:
    """SHA-256 over the mapping content whose semantic audit can approve.

    Audit state, timestamps, status, notes, and changelog are deliberately excluded.
    Any issuer, listing, placement, coverage, target, or map identity change produces a
    different digest and therefore makes a prior PASS unusable for COMPLETE.
    """
    if not isinstance(mapping, dict):
        raise TypeError("mapping must be an object")
    material = {field: mapping.get(field) for field in MATERIAL_MAPPING_FIELDS}
    payload = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _http_url(value) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _normalized_name(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _source_domain(value) -> str | None:
    """Return a conservative registrable-domain approximation for breadth checks."""
    if not _http_url(value):
        return None
    host = (urlparse(value).hostname or "").strip(".").casefold()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) < 2:
        return host or None
    suffix = ".".join(parts[-2:])
    width = 3 if suffix in COMMON_COUNTRY_SECOND_LEVELS and len(parts) >= 3 else 2
    return ".".join(parts[-width:])


def _substantive_text(value) -> bool:
    """Reject empty and token-only attestations while allowing concise quotations."""
    if not isinstance(value, str):
        return False
    text = re.sub(r"\s+", " ", value.strip())
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", text)
    return (
        len(text) >= 20
        and len(words) >= 3
        and text.casefold() not in {
            "checked and verified",
            "looks good to me",
            "source was opened",
        }
    )


def audit_record_digest(record: dict) -> str:
    """Deterministic SHA-256 for one exact current audit record."""
    payload = json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def placement_claim_digest(placement: dict, evidence: dict) -> str:
    """Digest the placement identity, role, and exact evidence claim sampled."""
    return audit_record_digest({
        "chain_id": placement.get("chain_id"),
        "link_id": placement.get("link_id"),
        "issuer_id": placement.get("issuer_id"),
        "role": placement.get("role"),
        "evidence": evidence,
    })


def search_record_digest(search: dict) -> str:
    """Digest the complete current EXHAUSTED search record sampled."""
    return audit_record_digest(search)


def listing_identity_digest(listing: dict) -> str:
    """Bind a listing's identity fields and its complete evidence corpus."""
    return audit_record_digest({
        "listing_id": listing.get("listing_id"),
        "issuer_id": listing.get("issuer_id"),
        "exchange": listing.get("exchange"),
        "ticker": listing.get("ticker"),
        "market_ticker": listing.get("market_ticker"),
        "identity_evidence": listing.get("identity_evidence"),
    })


def _listing_identity_evidence_key(item: dict) -> tuple:
    """The source claim an audit sample must identify unambiguously."""
    return (
        item.get("url") or item.get("source_url"),
        item.get("source_date"),
        item.get("source_type"),
        item.get("official_registry"),
        item.get("source_excerpt"),
    )


def _self_labelled_blog_url(value) -> bool:
    """A source calling itself a blog cannot establish official listing identity."""
    if not _http_url(value):
        return False
    parsed = urlparse(value)
    labels = (parsed.hostname or "").casefold().split(".")
    path_parts = [part.casefold() for part in parsed.path.split("/") if part]
    return any(part in {"blog", "blogs"} for part in labels + path_parts)


def _nonempty_issue(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value) and any(_nonempty_issue(item) for item in value.values())
    if isinstance(value, list):
        return bool(value) and any(_nonempty_issue(item) for item in value)
    return value is not None


def audit_failures(mapping: dict) -> list[str]:
    """Validate audit freshness, declared provenance, and exact content coverage.

    Repository state cannot cryptographically prove fresh context or reviewer identity.
    This gate therefore checks declared provenance, temporal freshness, and deterministic
    coverage of current records without claiming stronger identity assurance.
    """
    audit = mapping.get("audit")
    if audit is None:
        if mapping.get("status") == "COMPLETE":
            return [
                "COMPLETE mapping needs a current PASS audit by "
                f"{AUDIT_REVIEWER}"
            ]
        return []
    if not isinstance(audit, dict):
        return ["audit must be an object"]

    failures = []
    required = {
        "audited_at",
        "reviewed_by",
        "agent_id",
        "transcript_ref",
        "review_mode",
        "independence_limitation",
        "status",
        "mapping_fingerprint",
        "links_examined",
        "placements_examined",
        "searches_examined",
        "target_met_links_examined",
        "sampled_checks",
        *AUDIT_CONFLICT_FIELDS,
        "exhausted_links_examined",
        "surviving_limitation",
    }
    missing = sorted(required - set(audit))
    if missing:
        failures.append(f"audit missing keys: {', '.join(missing)}")

    audited_at = _utc_datetime(audit.get("audited_at"))
    if audited_at is None:
        failures.append("audit.audited_at must be a timezone-aware UTC timestamp")
    if audit.get("reviewed_by") != AUDIT_REVIEWER:
        failures.append(f"audit.reviewed_by must be {AUDIT_REVIEWER!r}")
    if not str(audit.get("agent_id") or "").strip():
        failures.append("audit.agent_id must be non-empty")
    elif audit.get("agent_id") != audit.get("reviewed_by"):
        failures.append("audit.agent_id must equal audit.reviewed_by")
    if not str(audit.get("transcript_ref") or "").strip():
        failures.append("audit.transcript_ref must be non-empty")
    review_mode = audit.get("review_mode")
    if review_mode not in AUDIT_REVIEW_MODES:
        failures.append(
            f"audit.review_mode {review_mode!r} not in {sorted(AUDIT_REVIEW_MODES)}"
        )
    independence_limitation = audit.get("independence_limitation")
    if not _substantive_text(independence_limitation):
        failures.append("audit.independence_limitation must be substantive")
    elif review_mode == "SELF_REVIEW" and not any(
            marker in independence_limitation.casefold()
            for marker in ("self-review", "self review", "same context", "not independent")):
        failures.append(
            "audit SELF_REVIEW independence_limitation must explicitly disclose "
            "same-context or non-independent review"
        )
    audit_status = audit.get("status")
    if audit_status not in AUDIT_STATUS:
        failures.append(
            f"audit.status {audit_status!r} not in {sorted(AUDIT_STATUS)}"
        )
    fingerprint = audit.get("mapping_fingerprint")
    if not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint):
        failures.append("audit.mapping_fingerprint must be a lowercase SHA-256 digest")
    try:
        current_fingerprint = mapping_fingerprint(mapping)
    except (TypeError, ValueError):
        current_fingerprint = None
    is_current = fingerprint == current_fingerprint
    if current_fingerprint is not None and not is_current:
        failures.append("audit.mapping_fingerprint does not match current material content")

    for field in (
        "links_examined",
        "placements_examined",
        "searches_examined",
        "target_met_links_examined",
        "exhausted_links_examined",
    ):
        value = audit.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            failures.append(f"audit.{field} must be a non-negative integer")
    if "listings_examined" in audit:
        value = audit.get("listings_examined")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            failures.append("audit.listings_examined must be a non-negative integer")
    elif audit.get("status") == "PASS":
        failures.append("audit missing keys: listings_examined")

    issue_lists = {}
    for field in AUDIT_CONFLICT_FIELDS:
        value = audit.get(field)
        if not isinstance(value, list):
            failures.append(f"audit.{field} must be a list")
            issue_lists[field] = []
            continue
        issue_lists[field] = value
        for i, issue in enumerate(value):
            if not _nonempty_issue(issue):
                failures.append(f"audit.{field}[{i}] must explain the finding")

    if not str(audit.get("surviving_limitation") or "").strip():
        failures.append("audit.surviving_limitation must be non-empty")

    material_times = []
    changelog = mapping.get("changelog")
    if isinstance(changelog, list):
        for i, entry in enumerate(changelog):
            if not isinstance(entry, dict):
                failures.append(f"changelog[{i}] must be an object for audit freshness")
                continue
            if str(entry.get("kind") or "").upper() == "NOTE":
                continue
            changed_at = _utc_datetime(entry.get("ts"))
            if changed_at is None:
                failures.append(
                    f"changelog[{i}].ts must be a UTC timestamp for audit freshness"
                )
            else:
                material_times.append((i, changed_at))
    if audited_at is not None and material_times:
        latest_index, latest_material_at = max(material_times, key=lambda item: item[1])
        if audited_at < latest_material_at:
            failures.append(
                "audit.audited_at is earlier than material mapping changelog "
                f"timestamp at changelog[{latest_index}]"
            )

    sampled = audit.get("sampled_checks")
    if not isinstance(sampled, list):
        failures.append("audit.sampled_checks must be a list")
        sampled = []

    coverage = [
        row for row in mapping.get("link_coverage") or [] if isinstance(row, dict)
    ]
    placements = [
        row for row in mapping.get("placements") or []
        if active_placement(row)
    ]
    listings = [
        row for row in mapping.get("listings") or [] if isinstance(row, dict)
    ]
    placement_records: dict[tuple, list[dict]] = {}
    placements_by_link: dict[str, set] = {}
    for placement in placements:
        key = (
            placement.get("chain_id"),
            placement.get("link_id"),
            placement.get("issuer_id"),
        )
        records = []
        for item in placement.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            records.append({
                "source_url": item.get("url") or item.get("source_url"),
                "source_date": item.get("source_date"),
                "record_digest": placement_claim_digest(placement, item),
            })
        placement_records[key] = records
        placements_by_link.setdefault(str(placement.get("link_id")), set()).add(key)

    placed_issuer_ids = {
        placement.get("issuer_id")
        for placement in placements
        if isinstance(placement.get("issuer_id"), str) and placement.get("issuer_id")
    }
    listing_records: dict[str, list[dict]] = {}
    required_listing_ids = set()
    for listing in listings:
        listing_id = listing.get("listing_id")
        if not isinstance(listing_id, str) or not listing_id:
            continue
        if listing.get("issuer_id") not in placed_issuer_ids:
            continue
        required_listing_ids.add(listing_id)
        listing_records.setdefault(listing_id, []).append({
            "issuer_id": listing.get("issuer_id"),
            "record_digest": listing_identity_digest(listing),
            "evidence": [
                {
                    "source_url": item.get("url") or item.get("source_url"),
                    "source_date": item.get("source_date"),
                    "source_type": item.get("source_type"),
                    "source_excerpt": item.get("source_excerpt"),
                }
                for item in listing.get("identity_evidence") or []
                if isinstance(item, dict)
            ],
        })

    search_records: dict[tuple, list[dict]] = {}
    searches_by_link: dict[str, set] = {}
    for row in coverage:
        link_id = str(row.get("link_id"))
        if row.get("status") != "EXHAUSTED":
            continue
        for search in row.get("searches") or []:
            if not isinstance(search, dict):
                continue
            url = search.get("url") or search.get("source_url")
            key = (link_id, search.get("query"), url)
            search_records.setdefault(key, []).append({
                "source_url": url,
                "source_date": search.get("source_date"),
                "record_digest": search_record_digest(search),
            })
            searches_by_link.setdefault(link_id, set()).add(key)

    checked_placements = set()
    checked_listings = set()
    checked_searches = set()
    checked_links = set()
    check_keys = []
    all_checks_true = True
    for i, row in enumerate(sampled):
        where = f"audit.sampled_checks[{i}]"
        if not isinstance(row, dict):
            failures.append(f"{where}: must be an object")
            continue
        chain_id = row.get("chain_id")
        link_id = row.get("link_id")
        issuer_id = row.get("issuer_id")
        listing_id = row.get("listing_id")
        search_query = row.get("search")
        has_issuer = isinstance(issuer_id, str) and bool(issuer_id)
        has_listing = isinstance(listing_id, str) and bool(listing_id)
        has_search = isinstance(search_query, str) and bool(search_query.strip())
        if chain_id != mapping.get("chain_id"):
            failures.append(
                f"{where}.chain_id {chain_id!r} does not match mapping chain_id"
            )
        if not has_listing and (not isinstance(link_id, str) or not link_id):
            failures.append(f"{where}.link_id must be non-empty")
        if sum((has_issuer and not has_listing, has_listing, has_search)) != 1:
            failures.append(
                f"{where} must identify exactly one placement, listing, or search"
            )
        for field in ("identity_ok", "role_ok", "source_date_ok"):
            value = row.get(field)
            if not isinstance(value, bool):
                failures.append(f"{where}.{field} must be boolean")
                all_checks_true = False
            elif value is not True:
                all_checks_true = False
        source_url = row.get("source_url")
        source_date = row.get("source_date")
        source_type = row.get("source_type")
        source_excerpt = row.get("source_excerpt")
        record_digest = row.get("record_digest")
        if not _http_url(source_url):
            failures.append(f"{where}.source_url must be a fetchable http(s) URL")
        elif has_listing and _self_labelled_blog_url(source_url):
            failures.append(
                f"{where}.source_url cannot be a self-labelled blog for listing identity"
            )
        if not valid_date(source_date):
            failures.append(f"{where}.source_date must be YYYY-MM-DD")
        if has_listing and (
                not isinstance(source_type, str) or not source_type.strip()):
            failures.append(f"{where}.source_type must be non-empty for listing identity")
        if not _substantive_text(source_excerpt):
            failures.append(f"{where}.source_excerpt must be substantive")
        if not isinstance(record_digest, str) or not SHA256_RE.fullmatch(record_digest):
            failures.append(f"{where}.record_digest must be a lowercase SHA-256 digest")

        if has_listing:
            check_keys.append(("listing", listing_id))
            candidates = listing_records.get(listing_id, [])
            exact = [
                item for item in candidates
                if item["issuer_id"] == issuer_id
                and item["record_digest"] == record_digest
                and {
                    "source_url": source_url,
                    "source_date": source_date,
                    "source_type": source_type,
                    "source_excerpt": source_excerpt,
                } in item["evidence"]
            ]
            if not candidates:
                failures.append(f"{where} does not resolve to a current counted listing")
            elif record_digest not in {
                    item["record_digest"] for item in candidates}:
                failures.append(
                    f"{where}.record_digest is stale for the current listing identity"
                )
            elif len(exact) != 1:
                failures.append(
                    f"{where} does not resolve exactly one current listing identity claim"
                )
            else:
                checked_listings.add(listing_id)
        elif has_issuer:
            key = (chain_id, link_id, issuer_id)
            check_keys.append(("placement", key))
            candidates = placement_records.get(key, [])
            exact = [
                item for item in candidates
                if item["source_url"] == source_url
                and item["source_date"] == source_date
                and item["record_digest"] == record_digest
            ]
            if not candidates:
                failures.append(f"{where} does not resolve to a current placement")
            elif record_digest not in {
                    item["record_digest"] for item in candidates}:
                failures.append(
                    f"{where}.record_digest is stale for the current placement claim"
                )
            elif len(exact) != 1:
                failures.append(
                    f"{where} does not resolve exactly one current placement claim"
                )
            else:
                checked_placements.add(key)
                checked_links.add(str(link_id))
        elif has_search:
            key = (str(link_id), search_query, source_url)
            check_keys.append(("search", key))
            candidates = search_records.get(key, [])
            exact = [
                item for item in candidates
                if item["source_date"] == source_date
                and item["record_digest"] == record_digest
            ]
            if not candidates:
                failures.append(f"{where} does not resolve to a current EXHAUSTED search")
            elif record_digest not in {
                    item["record_digest"] for item in candidates}:
                failures.append(
                    f"{where}.record_digest is stale for the current EXHAUSTED search"
                )
            elif len(exact) != 1:
                failures.append(
                    f"{where} does not resolve exactly one current EXHAUSTED search"
                )
            else:
                checked_searches.add(key)
                checked_links.add(str(link_id))

    if len(check_keys) != len(set(check_keys)):
        failures.append(
            "audit.sampled_checks contains duplicate placement, listing, or search rows"
        )

    coverage_links = {str(row.get("link_id")) for row in coverage}
    target_links = {
        str(row.get("link_id"))
        for row in coverage
        if row.get("status") == "TARGET_MET"
    }
    exhausted_links = {
        str(row.get("link_id"))
        for row in coverage
        if row.get("status") == "EXHAUSTED"
    }
    if checked_links != coverage_links:
        failures.append(
            "audit sampled links must equal current link_coverage; "
            f"missing {sorted(coverage_links - checked_links)}, "
            f"extra {sorted(checked_links - coverage_links)}"
        )

    target_links_examined = set()
    for link_id in sorted(target_links):
        examined = checked_placements & placements_by_link.get(link_id, set())
        if len(examined) < 2:
            failures.append(
                f"audit TARGET_MET link {link_id!r} sampled {len(examined)} "
                "distinct placements, needs at least 2"
            )
        else:
            target_links_examined.add(link_id)

    exhausted_links_examined = set()
    for link_id in sorted(exhausted_links):
        missing_placements = placements_by_link.get(link_id, set()) - checked_placements
        if missing_placements:
            failures.append(
                f"audit EXHAUSTED link {link_id!r} did not examine every placement: "
                f"{sorted(missing_placements, key=repr)}"
            )
        missing_searches = searches_by_link.get(link_id, set()) - checked_searches
        if missing_searches:
            failures.append(
                f"audit EXHAUSTED link {link_id!r} did not examine every search"
            )
        if not missing_placements and not missing_searches:
            exhausted_links_examined.add(link_id)

    denominator_checks = {
        "links_examined": len(checked_links),
        "placements_examined": len(checked_placements),
        "searches_examined": len(checked_searches),
        "target_met_links_examined": len(target_links_examined),
        "exhausted_links_examined": len(exhausted_links_examined),
    }
    if audit_status == "PASS":
        denominator_checks["listings_examined"] = len(required_listing_ids)
        missing_listings = required_listing_ids - checked_listings
        if missing_listings:
            failures.append(
                "audit did not examine every listing counted by placements: "
                f"{sorted(missing_listings)}"
            )
    for field, expected in denominator_checks.items():
        if audit.get(field) != expected:
            failures.append(
                f"audit.{field} is {audit.get(field)!r}, "
                f"resolved current-content denominator is {expected}"
            )

    if audit_status == "PASS":
        nonempty = [field for field, rows in issue_lists.items() if rows]
        if nonempty:
            failures.append(
                "PASS audit requires empty conflict and amendment arrays: "
                + ", ".join(nonempty)
            )
        if not all_checks_true:
            failures.append("PASS audit requires every sampled check to be true")
        if is_current and mapping.get("status") != "COMPLETE":
            failures.append("current PASS audit requires mapping status COMPLETE")
    elif audit_status == "FAIL":
        if not issue_lists.get("amendments_required"):
            failures.append("FAIL audit requires non-empty amendments_required")
        if is_current and mapping.get("status") != "ACTIVE":
            failures.append("current FAIL audit requires mapping status ACTIVE")

    if mapping.get("status") == "COMPLETE":
        if audit_status != "PASS":
            failures.append("COMPLETE mapping requires audit.status PASS")
    return failures


def _source_failures(item, where: str, *, claim: bool = False) -> list[str]:
    out = []
    if not isinstance(item, dict):
        return [f"{where}: must be an object"]
    if claim and not str(item.get("claim") or "").strip():
        out.append(f"{where}: issuer-role evidence needs a non-empty claim")
    tag = item.get("tag")
    if tag not in EVIDENCE_TAGS:
        out.append(f"{where}.tag: {tag!r} not in {sorted(EVIDENCE_TAGS)}")
    if not str(item.get("source_name") or "").strip():
        out.append(f"{where}: needs source_name")
    if not valid_date(item.get("source_date")):
        out.append(f"{where}.source_date: {item.get('source_date')!r} is not YYYY-MM-DD")
    url = item.get("url") or item.get("source_url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        out.append(f"{where}: needs a fetchable http(s) url")
    return out


def _listing_identity_failures(
        item, where: str, listing: dict, issuer_name: str) -> list[str]:
    """A counted listing needs exact official legal-issuer/exchange/ticker proof."""
    out = _source_failures(item, where)
    if not isinstance(item, dict):
        return out
    if item.get("tag") != "VERIFIED":
        out.append(f"{where}.tag must be 'VERIFIED' for official listing identity")
    source_type = item.get("source_type")
    if source_type is not None and source_type not in LISTING_SOURCE_TYPES:
        out.append(
            f"{where}.source_type {source_type!r} not in "
            f"{sorted(LISTING_SOURCE_TYPES)}"
        )
    official_registry = item.get("official_registry")
    if official_registry is not None and not isinstance(official_registry, bool):
        out.append(f"{where}.official_registry must be boolean when present")
    if official_registry is not True and source_type not in LISTING_SOURCE_TYPES:
        out.append(
            f"{where} needs official_registry: true or an official listing source_type"
        )
    expected = {
        "legal_issuer": issuer_name,
        "exchange": listing.get("exchange"),
        "ticker": listing.get("ticker"),
    }
    for field, value in expected.items():
        actual = item.get(field)
        if not str(actual or "").strip():
            out.append(f"{where}.{field} must be non-empty")
        elif _normalized_name(actual) != _normalized_name(value):
            out.append(
                f"{where}.{field} {actual!r} does not match listing value {value!r}"
            )
    excerpt = item.get("source_excerpt")
    if not _substantive_text(excerpt):
        out.append(
            f"{where}.source_excerpt must be substantive official listing evidence"
        )
    else:
        normalized_excerpt = _normalized_name(excerpt)
        for field, value in expected.items():
            if _normalized_name(value) not in normalized_excerpt:
                out.append(
                    f"{where}.source_excerpt must state the exact {field} "
                    f"claimed by the listing"
                )
    return out


def _link_scope_failures(scope, where: str, link_id: str) -> list[str]:
    if not isinstance(scope, dict):
        return [f"{where} must be an object"]
    out = []
    if scope.get("link_id") != link_id:
        out.append(f"{where}.link_id must equal {link_id!r}")
    if not _substantive_text(scope.get("qualification_boundary")):
        out.append(f"{where}.qualification_boundary must be substantive")
    return out


def _zero_control_probe_failures(
        search: dict, where: str, source_domain: str | None) -> list[str]:
    probe = search.get("control_probe")
    if not isinstance(probe, dict):
        return [f"{where}.control_probe must document every zero-hit source"]
    out = []
    if not str(probe.get("query") or "").strip():
        out.append(f"{where}.control_probe.query must be non-empty")
    if not str(probe.get("expected_name") or "").strip():
        out.append(f"{where}.control_probe.expected_name must be non-empty")
    probe_url = probe.get("url")
    if not _http_url(probe_url):
        out.append(f"{where}.control_probe.url must be a fetchable http(s) URL")
    elif _source_domain(probe_url) != source_domain:
        out.append(f"{where}.control_probe must use the same source domain")
    if probe.get("source_type") != search.get("source_type"):
        out.append(f"{where}.control_probe must use the same source_type")
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
    issuer_names = {}
    for i, issuer in enumerate(issuers):
        where = f"issuers[{i}]"
        if not isinstance(issuer, dict):
            failures.append(f"{where}: must be an object")
            continue
        iid = issuer.get("issuer_id")
        if not isinstance(iid, str) or not ISSUER_ID_RE.fullmatch(iid):
            failures.append(f"{where}.issuer_id {iid!r} is not a stable safe identifier")
        else:
            issuer_ids.append(iid)
        if not str(issuer.get("name") or "").strip():
            failures.append(f"{where}: needs a non-empty name")
        elif isinstance(iid, str) and ISSUER_ID_RE.fullmatch(iid):
            issuer_names[iid] = str(issuer.get("name")).strip()
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
    listed_issuers = set()
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
        if not isinstance(lid, str) or not lid.strip():
            failures.append(f"{where}.listing_id must be non-empty")
        else:
            listing_ids.append(lid)
        listing_issuer = listing.get("issuer_id")
        issuer_ok = isinstance(listing_issuer, str) and listing_issuer in issuer_set
        if not issuer_ok:
            failures.append(f"{where}.issuer_id {listing_issuer!r} is unresolved")
        ticker_ok = bool(str(listing.get("ticker") or "").strip())
        exchange_ok = bool(str(listing.get("exchange") or "").strip())
        if not ticker_ok or not exchange_ok:
            failures.append(f"{where}: ticker and exchange must be non-empty")
        listing_keys.append((str(listing.get("exchange") or "").casefold(),
                             str(listing.get("ticker") or "").casefold()))
        identity_evidence = listing.get("identity_evidence")
        identity_valid = True
        if not isinstance(identity_evidence, list) or not identity_evidence:
            failures.append(
                f"{where}: needs dated official identity_evidence proving legal "
                "issuer, exchange, and ticker"
            )
            identity_valid = False
        else:
            evidence_keys = []
            for j, item in enumerate(identity_evidence):
                item_failures = _listing_identity_failures(
                    item,
                    f"{where}.identity_evidence[{j}]",
                    listing,
                    issuer_names.get(listing_issuer, ""),
                )
                failures.extend(item_failures)
                if item_failures:
                    identity_valid = False
                if isinstance(item, dict):
                    evidence_keys.append(_listing_identity_evidence_key(item))
            if len(evidence_keys) != len(set(evidence_keys)):
                failures.append(
                    f"{where}.identity_evidence contains duplicate ambiguous "
                    "official identity evidence"
                )
                identity_valid = False
        if isinstance(lid, str) and lid.strip() and \
                issuer_ok and ticker_ok and exchange_ok and identity_valid:
            listed_issuers.add(listing_issuer)
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
        needed = {"chain_id", "link_id", "issuer_id", "role", "evidence", "status"}
        miss = sorted(needed - set(placement))
        if miss:
            failures.append(f"{where}: missing {', '.join(miss)}")
        key = (placement.get("chain_id"), placement.get("link_id"),
               placement.get("issuer_id"))
        if all(isinstance(value, str) for value in key):
            placement_keys.append(key)
        if placement.get("chain_id") != chain_id:
            failures.append(f"{where}.chain_id {placement.get('chain_id')!r} does not "
                            f"match map chain_id {chain_id!r}")
        link_id = placement.get("link_id")
        if links and str(link_id) not in links:
            failures.append(f"{where}.link_id {link_id!r} is not a link of {chain_id}")
        iid = placement.get("issuer_id")
        issuer_valid = isinstance(iid, str) and iid in issuer_set
        if not issuer_valid:
            failures.append(f"{where}.issuer_id {iid!r} is unresolved")
        if not issuer_valid or iid not in listed_issuers:
            failures.append(f"{where}.issuer_id {iid!r} does not resolve to a validated "
                            "public listing")
        if not str(placement.get("role") or "").strip():
            failures.append(f"{where}.role must be non-empty")
        if placement.get("status") not in PLACEMENT_STATUS:
            failures.append(f"{where}.status {placement.get('status')!r} not in "
                            f"{sorted(PLACEMENT_STATUS)}")
        if placement.get("active") is False or placement.get("is_active") is False:
            failures.append(f"{where}: placement.status is the sole activity vocabulary; "
                            "inactive flags are forbidden")
        evidence = placement.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            failures.append(f"{where}: needs dated issuer-role evidence")
        else:
            for j, item in enumerate(evidence):
                failures.extend(_source_failures(
                    item, f"{where}.evidence[{j}]", claim=True))
        if active_placement(placement) and issuer_valid and iid in listed_issuers:
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
                search_domains = set()
                search_types = set()
                search_boundaries = set()
                accepted_all = set()
                rejected_all = set()
                search_keys = []
                for j, search in enumerate(searches):
                    sw = f"{where}.searches[{j}]"
                    if not isinstance(search, dict) or \
                            not str(search.get("query") or "").strip():
                        failures.append(f"{sw}: needs a non-empty query")
                        continue
                    url = search.get("url") or search.get("source_url")
                    search_keys.append((search.get("query"), url))
                    failures.extend(_source_failures(search, sw))
                    source_type = search.get("source_type")
                    if source_type not in SEARCH_SOURCE_TYPES:
                        failures.append(
                            f"{sw}.source_type {source_type!r} not in "
                            f"{sorted(SEARCH_SOURCE_TYPES)}"
                        )
                    else:
                        search_types.add(source_type)
                    source_domain = _source_domain(url)
                    if source_domain:
                        search_domains.add(source_domain)

                    link_scope = search.get("link_scope")
                    failures.extend(
                        _link_scope_failures(link_scope, f"{sw}.link_scope", link_id)
                    )
                    if isinstance(link_scope, dict) and _substantive_text(
                            link_scope.get("qualification_boundary")):
                        search_boundaries.add(
                            re.sub(
                                r"\s+",
                                " ",
                                link_scope["qualification_boundary"].strip(),
                            )
                        )

                    control_probe_passed = search.get("control_probe_passed")
                    if not isinstance(control_probe_passed, bool):
                        failures.append(f"{sw}.control_probe_passed must be boolean")
                    elif control_probe_passed is not True:
                        failures.append(
                            f"{sw}.control_probe_passed must be true to close EXHAUSTED"
                        )

                    hits = search.get("hits_examined")
                    if isinstance(hits, bool) or not isinstance(hits, int) or hits < 0:
                        failures.append(f"{sw}.hits_examined must be a non-negative integer")
                    accepted = search.get("accepted_names")
                    if not isinstance(accepted, list):
                        failures.append(f"{sw}.accepted_names must be a list")
                        accepted = []
                    else:
                        for k, name in enumerate(accepted):
                            if not isinstance(name, str) or not name.strip():
                                failures.append(
                                    f"{sw}.accepted_names[{k}] must be a non-empty name")
                                continue
                            normalized = _normalized_name(name)
                            matching_issuers = {
                                issuer_id
                                for issuer_id in issuers_by_link.get(link_id, set())
                                if _normalized_name(issuer_names.get(issuer_id)) == normalized
                            }
                            if len(matching_issuers) != 1:
                                failures.append(
                                    f"{sw}.accepted_names[{k}] {name!r} does not resolve "
                                    "to exactly one validated listing and placement on "
                                    f"link {link_id!r}"
                                )
                            accepted_all.add(normalized)
                        normalized_accepted = [
                            _normalized_name(name)
                            for name in accepted
                            if isinstance(name, str) and name.strip()
                        ]
                        if len(normalized_accepted) != len(set(normalized_accepted)):
                            failures.append(f"{sw}.accepted_names contains duplicates")
                    rejected = search.get("rejected_names")
                    if not isinstance(rejected, list):
                        failures.append(f"{sw}.rejected_names must be a list")
                        rejected = []
                    else:
                        for k, rejection in enumerate(rejected):
                            if not isinstance(rejection, dict) or \
                                    not str(rejection.get("name") or "").strip() or \
                                    not str(rejection.get("reason") or "").strip():
                                failures.append(
                                    f"{sw}.rejected_names[{k}] needs name and reason")
                                continue
                            rejected_all.add(_normalized_name(rejection.get("name")))
                    if isinstance(hits, int) and not isinstance(hits, bool) and \
                            hits < len(accepted) + len(rejected):
                        failures.append(
                            f"{sw}.hits_examined {hits} is smaller than the "
                            f"{len(accepted) + len(rejected)} recorded accepted/rejected names")
                    result_status = search.get("result_status")
                    if result_status not in SEARCH_RESULT_STATUS:
                        failures.append(
                            f"{sw}.result_status {result_status!r} not in "
                            f"{sorted(SEARCH_RESULT_STATUS)}"
                        )
                    expected_status = (
                        "MIXED_RESULTS"
                        if accepted and rejected
                        else "QUALIFYING_NAMES_FOUND"
                        if accepted
                        else "NO_QUALIFYING_NAMES"
                    )
                    if result_status in SEARCH_RESULT_STATUS and \
                            result_status != expected_status:
                        failures.append(
                            f"{sw}.result_status must be {expected_status!r} for its "
                            "accepted/rejected names"
                        )
                    if hits == 0:
                        failures.extend(
                            _zero_control_probe_failures(search, sw, source_domain)
                        )
                    if not str(search.get("exhaustion_conclusion") or "").strip():
                        failures.append(
                            f"{sw}.exhaustion_conclusion must state why this link's "
                            "search found no further public issuers")

                if len(search_keys) != len(set(search_keys)):
                    failures.append(
                        f"{where}.searches contains duplicate query/source URL records"
                    )
                if len(search_domains) < 2:
                    failures.append(
                        f"{where}: EXHAUSTED needs at least two distinct source domains"
                    )
                if len(search_types) < 2:
                    failures.append(
                        f"{where}: EXHAUSTED needs at least two distinct source_types"
                    )
                if not search_types.intersection(OFFICIAL_SEARCH_SOURCE_TYPES):
                    failures.append(
                        f"{where}: EXHAUSTED needs an official exchange/registry source"
                    )
                if not search_types.intersection(DISCOVERY_SEARCH_SOURCE_TYPES):
                    failures.append(
                        f"{where}: EXHAUSTED needs a primary issuer or credible "
                        "industry source"
                    )
                if len(search_boundaries) != 1:
                    failures.append(
                        f"{where}: every EXHAUSTED search must share one exact "
                        "qualification boundary"
                    )

                combined_scope = row.get("combined_search_scope")
                cw = f"{where}.combined_search_scope"
                if not isinstance(combined_scope, dict):
                    failures.append(f"{cw} must be an object")
                else:
                    if combined_scope.get("link_id") != link_id:
                        failures.append(f"{cw}.link_id must equal {link_id!r}")
                    combined_boundary = combined_scope.get("qualification_boundary")
                    if not _substantive_text(combined_boundary):
                        failures.append(
                            f"{cw}.qualification_boundary must be substantive"
                        )
                    elif search_boundaries and re.sub(
                            r"\s+", " ", combined_boundary.strip()) not in search_boundaries:
                        failures.append(
                            f"{cw}.qualification_boundary must match every search"
                        )
                    if not _substantive_text(combined_scope.get("coverage_statement")):
                        failures.append(f"{cw}.coverage_statement must be substantive")
                    for field, actual_values in (
                        ("domains_covered", search_domains),
                        ("source_types_covered", search_types),
                    ):
                        declared = combined_scope.get(field)
                        if not isinstance(declared, list) or any(
                                not isinstance(value, str) or not value.strip()
                                for value in declared):
                            failures.append(f"{cw}.{field} must be a list of names")
                        elif len(declared) != len(set(declared)):
                            failures.append(f"{cw}.{field} contains duplicates")
                        elif set(declared) != actual_values:
                            failures.append(
                                f"{cw}.{field} does not match current searches; "
                                f"expected {sorted(actual_values)}"
                            )

                placed_names = {
                    _normalized_name(issuer_names.get(issuer_id))
                    for issuer_id in issuers_by_link.get(link_id, set())
                }
                placed_names.discard("")
                if accepted_all != placed_names:
                    failures.append(
                        f"{where}: accepted_names must resolve exactly to counted "
                        f"placements; missing {sorted(placed_names - accepted_all)}, "
                        f"extra {sorted(accepted_all - placed_names)}"
                    )
                counted_rejections = rejected_all.intersection(placed_names)
                if counted_rejections:
                    failures.append(
                        f"{where}: rejected_names are counted as placements: "
                        f"{sorted(counted_rejections)}"
                    )
                accepted_rejections = rejected_all.intersection(accepted_all)
                if accepted_rejections:
                    failures.append(
                        f"{where}: names cannot be both accepted and rejected: "
                        f"{sorted(accepted_rejections)}"
                    )
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
    failures.extend(audit_failures(m))
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


def audit_write_scope_failures(prior: dict, current: dict) -> list[str]:
    """A fresh-context audit may change only audit, map status, and changelog."""
    if not isinstance(prior, dict) or not isinstance(current, dict):
        return []
    if prior.get("audit") == current.get("audit"):
        return []
    allowed = {"audit", "status", "changelog"}
    changed = sorted(
        key
        for key in set(prior) | set(current)
        if key not in allowed and prior.get(key) != current.get(key)
    )
    if not changed:
        return []
    return [
        "fresh audit changed fields outside audit, status, and changelog: "
        + ", ".join(changed)
    ]


def _reopen_failures(prior: dict, current: dict) -> list[str]:
    """Why a COMPLETE -> ACTIVE transition is not a fresh semantic-audit reopen."""
    failures = []
    audit = current.get("audit")
    if not isinstance(audit, dict) or audit.get("status") != "FAIL":
        failures.append("current audit is not FAIL")
        return failures
    if audit.get("reviewed_by") != AUDIT_REVIEWER:
        failures.append(f"reviewed_by is not {AUDIT_REVIEWER}")
    try:
        current_fingerprint = mapping_fingerprint(current)
    except (TypeError, ValueError):
        current_fingerprint = None
    if audit.get("mapping_fingerprint") != current_fingerprint:
        failures.append("FAIL audit fingerprint is not current")
    current_at = _utc_datetime(audit.get("audited_at"))
    if current_at is None:
        failures.append("FAIL audit audited_at is not a UTC timestamp")
    prior_audit = prior.get("audit")
    if audit == prior_audit:
        failures.append("FAIL audit is unchanged from HEAD")
    prior_at = (
        _utc_datetime(prior_audit.get("audited_at"))
        if isinstance(prior_audit, dict)
        else None
    )
    if current_at is not None and prior_at is not None and current_at <= prior_at:
        failures.append("FAIL audit is not newer than the prior audit")

    prior_log = prior.get("changelog") if isinstance(prior.get("changelog"), list) else []
    current_log = (
        current.get("changelog") if isinstance(current.get("changelog"), list) else []
    )
    appended = current_log[len(prior_log):] if len(current_log) > len(prior_log) else []
    explained = False
    for entry in appended:
        if not isinstance(entry, dict):
            continue
        change = str(entry.get("change") or entry.get("text") or "").strip()
        prior_basis = str(entry.get("prior") or "").strip()
        lowered = change.casefold()
        if (
            change
            and prior_basis
            and "audit" in lowered
            and any(word in lowered for word in ("fail", "reopen", "active", "defect"))
        ):
            explained = True
            break
    if not explained:
        failures.append("no appended changelog entry explains the audit reopen")
    return failures


def preservation_failures(root: Path, path: Path, current: dict) -> list[str]:
    """Permanent-memory checks that can be compared safely with git HEAD."""
    prior = _head_json(root, path)
    if not isinstance(prior, dict):
        return []
    failures = []
    failures.extend(audit_write_scope_failures(prior, current))
    if MAP_STATUS_ORDER.get(current.get("status"), -1) < \
            MAP_STATUS_ORDER.get(prior.get("status"), -1):
        is_reopen = (
            prior.get("status") == "COMPLETE"
            and current.get("status") == "ACTIVE"
        )
        details = _reopen_failures(prior, current) if is_reopen else []
        if not is_reopen or details:
            suffix = f": {'; '.join(details)}" if details else ""
            failures.append(
                f"status regressed {prior.get('status')} -> {current.get('status')}"
                f"{suffix}"
            )
    if prior.get("audit") is not None and current.get("audit") is None:
        failures.append("audit block removed versus HEAD")

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
    links = placements = active_placements = active_pairs = exhausted_links = 0
    for path in paths:
        obj = read_json(path)
        if isinstance(obj, dict):
            mappings.append((path, obj))
            links += len(obj.get("link_coverage") or [])
            placements += len(obj.get("placements") or [])
            active = [p for p in obj.get("placements") or [] if active_placement(p)]
            active_placements += len(active)
            active_pairs += len({(p.get("link_id"), p.get("issuer_id")) for p in active})
            exhausted_links += sum(
                1
                for row in obj.get("link_coverage") or []
                if isinstance(row, dict) and row.get("status") == "EXHAUSTED"
            )
        for finding in validate_mapping(root, path, obj):
            failures.append(f"{path.name}: {finding}")
        if isinstance(obj, dict):
            for finding in preservation_failures(root, path, obj):
                failures.append(f"{path.name}: preservation: {finding}")
    failures.extend(corpus_identity_failures(mappings))

    print(f"check_map: {len(paths)} maps, {links} links, {active_pairs}/{placements} "
          f"ACTIVE distinct issuer placements ({active_placements} ACTIVE rows), "
          f"{exhausted_links} EXHAUSTED links examined")
    if failures:
        for finding in failures:
            print(f"  FAIL  {finding}")
        print(f"check_map: FAILED with {len(failures)} finding(s)")
        return 1
    print("check_map: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
