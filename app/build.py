#!/usr/bin/env python3
"""Upstream UI builder. Stdlib only, offline, runs identically in every venue.

Reads data/, inlines everything into app/templates/shell.html, writes app/index.html.
Runs tools/validate.py first and refuses to build on invalid data.

Usage: python3 app/build.py [--check]   (--check: validate + assemble, write nothing)
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
DATA = ROOT / "data"
TOOLS = ROOT / "tools"
SIZE_WARN_MB = 2.0
CAMPAIGN_PROJECTION_MAX_BYTES = 250_000
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from agent_registry import agents_payload  # noqa: E402
from check_campaign import (  # noqa: E402
    canonical_mapped_placements,
    validated_public_listings,
)
# Vendored UMD modules, inlined in this exact order (each attaches to window.d3;
# d3-force resolves the other three off that object at define time). Vetting record
# and licence: app/templates/vendor/README.md and LICENSE-d3.txt.
VENDOR_FILES = ["d3-quadtree.min.js", "d3-dispatch.min.js", "d3-timer.min.js", "d3-force.min.js"]


def read_json_dir(folder: Path) -> list:
    out = []
    if folder.exists():
        for f in sorted(folder.glob("*.json")):
            if f.name.startswith("_"):
                continue
            out.append(json.loads(f.read_text()))
    return out


def _compact_text(value, limit=180):
    """One-line display text only; campaign evidence stays in its canonical store."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _blocker_texts(value) -> list:
    if isinstance(value, str):
        text = _compact_text(value)
        return [text] if text else []
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, dict):
            item = item.get("reason") or item.get("basis") or item.get("text")
        text = _compact_text(item)
        if text and text not in out:
            out.append(text)
    return out


def _mapping_links(doc: dict) -> dict:
    """Normalize supported mapping layouts without projecting mapping evidence."""
    links = {}
    raw_links = doc.get("links")
    if isinstance(raw_links, dict):
        for link_id, row in raw_links.items():
            if isinstance(row, dict):
                links[str(link_id)] = row
    elif isinstance(raw_links, list):
        for row in raw_links:
            if isinstance(row, dict):
                link_id = row.get("link_id") or row.get("id")
                if link_id is not None:
                    links[str(link_id)] = row

    # A flat placement list is accepted as an input shape, but the artifact still gets
    # only per-link counts. This keeps the dashboard compatible with a normalized store
    # without ever inlining its 1,200 attribution/evidence rows.
    flat = doc.get("placements")
    if not isinstance(flat, list) and not links:
        flat = doc.get("mappings")
    if isinstance(flat, list):
        for placement in flat:
            if not isinstance(placement, dict):
                continue
            link_id = placement.get("link_id")
            if link_id is None:
                continue
            row = links.setdefault(str(link_id), {"mappings": []})
            row.setdefault("mappings", []).append(placement)

    coverage = doc.get("link_coverage")
    if isinstance(coverage, list):
        for item in coverage:
            if not isinstance(item, dict) or item.get("link_id") is None:
                continue
            row = links.setdefault(str(item["link_id"]), {"mappings": []})
            row["coverage_status"] = item.get("status")
            row["target"] = doc.get("target_issuers_per_link")
            row["exhausted_basis"] = item.get("exhausted_reason")
            row["as_of"] = doc.get("as_of")
    return links


def _ticker(value):
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    return value or None


def _identity(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _freshest(*values):
    """Return the freshest non-empty ISO-like date without inventing a fallback."""
    dates = [value.strip() for value in values
             if isinstance(value, str) and value.strip()]
    return max(dates) if dates else None


def _screen_rows(screen: dict):
    for rows in (screen.get("buckets") or {}).values():
        for row in rows or []:
            if isinstance(row, dict):
                yield row


def _opportunity(profile: dict) -> dict:
    value = profile.get("opportunity")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return {"tier": value}
    value = profile.get("opportunity_tier")
    return {"tier": value} if isinstance(value, str) else {}


def _campaign_descriptors(campaign: dict) -> list:
    raw = campaign.get("themes")
    if isinstance(raw, list):
        out = []
        for item in raw:
            if isinstance(item, dict):
                out.append(dict(item))
            elif isinstance(item, str):
                out.append({"chain_id": item})
        if out:
            return out

    chain_ids = campaign.get("chain_ids") or []
    signal_ids = campaign.get("theme_signal_ids") or []
    return [{"chain_id": chain_id,
             "signal_id": signal_ids[i] if i < len(signal_ids) else None}
            for i, chain_id in enumerate(chain_ids) if isinstance(chain_id, str)]


def _coverage_for(campaign: dict, chain_id: str) -> dict:
    coverage = campaign.get("coverage")
    if not isinstance(coverage, dict):
        return {}
    row = coverage.get(chain_id)
    return row if isinstance(row, dict) else {}


def build_campaign_ix(data_dir=DATA, chains=None, screens=None, stocks=None,
                      requests=None) -> dict:
    """Build the campaign dashboard's bounded, deterministic projection.

    Canonical campaign, mapping, and company documents may be large and evidence-heavy.
    The one-file artifact receives only denominators, per-link coverage, compact blocker
    text, and the O1 work queue. It never receives raw mapping evidence, search logs,
    full company profiles, or any additional market file.
    """
    data_dir = Path(data_dir)
    campaigns = read_json_dir(data_dir / "campaigns")
    if not campaigns:
        return {
            "present": False,
            "campaign_count": 0,
            "themes": [],
            "o1": [],
        }

    campaigns.sort(key=lambda c: (str(c.get("as_of") or ""),
                                  str(c.get("id") or "")))
    active = [c for c in campaigns if c.get("status") == "ACTIVE"]
    campaign = (active or campaigns)[-1]
    mappings = read_json_dir(data_dir / "mappings")
    companies = read_json_dir(data_dir / "companies")
    chains = list(chains or [])
    screens = list(screens or [])
    stocks = list(stocks or [])
    request_rows = ((requests or {}).get("requests") or [])

    chain_by_id = {str(c.get("id")): c for c in chains if c.get("id") is not None}
    mapping_by_chain = {}
    for doc in mappings:
        chain_id = doc.get("chain_id") or doc.get("id")
        if chain_id is not None:
            mapping_by_chain[str(chain_id)] = doc

    descriptors = _campaign_descriptors(campaign)
    campaign_chain_ids = {
        str(d.get("chain_id") or d.get("id"))
        for d in descriptors if d.get("chain_id") or d.get("id")
    }

    targets = campaign.get("targets") if isinstance(campaign.get("targets"), dict) else {}
    mapping_target = (targets.get("mappings_per_investable_link")
                      if targets.get("mappings_per_investable_link") is not None
                      else targets.get("issuers_per_link"))

    issuer_names = {}
    issuers_by_chain_ticker = {}
    issuers_by_ticker = {}
    listing_by_chain_ref = {}
    for doc in mappings:
        doc_chain_id = _identity(doc.get("chain_id"))
        for issuer in doc.get("issuers") or []:
            if not isinstance(issuer, dict):
                continue
            issuer_id = _identity(issuer.get("issuer_id"))
            if issuer_id:
                issuer_names.setdefault(issuer_id, issuer.get("name"))
        listing_rows = list(validated_public_listings(doc).values())
        listing_rows.sort(key=lambda row: (
            str(row.get("exchange") or ""),
            str(row.get("ticker") or ""),
        ))
        for listing in listing_rows:
            issuer_id = _identity(listing.get("issuer_id"))
            ticker = _ticker(listing.get("ticker"))
            listing_id = _identity(listing.get("listing_id"))
            if issuer_id and ticker:
                issuers_by_ticker.setdefault(ticker, set()).add(issuer_id)
                if doc_chain_id:
                    issuers_by_chain_ticker.setdefault(
                        (doc_chain_id, ticker), set()).add(issuer_id)
            if doc_chain_id and listing_id:
                listing_by_chain_ref[(doc_chain_id, listing_id)] = listing

    normalized_mapping_links = {
        chain_id: _mapping_links(doc)
        for chain_id, doc in mapping_by_chain.items()
    }
    mapping_placements = canonical_mapped_placements(
        mapping_by_chain, campaign_chain_ids)
    mapping_issuers_by_link = {}
    for chain_id, link_id, issuer_id in mapping_placements:
        mapping_issuers_by_link.setdefault((chain_id, link_id), set()).add(
            issuer_id)

    profiles = {}
    for profile in companies:
        issuer_id = _identity(profile.get("issuer_id"))
        if issuer_id:
            profiles[issuer_id] = profile

    profile_placements = set()
    profile_places_by_issuer = {}
    for issuer_id, profile in profiles.items():
        for placement in profile.get("placements") or []:
            if not isinstance(placement, dict):
                continue
            chain_id = _identity(placement.get("chain_id"))
            link_id = _identity(placement.get("link_id"))
            key = (chain_id, link_id, issuer_id)
            if chain_id and link_id and key in mapping_placements:
                profile_placements.add((issuer_id, chain_id, link_id))
                profile_places_by_issuer.setdefault(issuer_id, set()).add(
                    (chain_id, link_id))

    screens_by_ref = {}
    screen_dates_by_chain = {}
    for screen in screens:
        chain_id = _identity(screen.get("chain_id"))
        if not chain_id:
            continue
        screen_dates_by_chain[chain_id] = _freshest(
            screen_dates_by_chain.get(chain_id), screen.get("as_of"))
        screen_id = _identity(screen.get("id"))
        for ref in (
            screen_id,
            f"{screen_id}.json" if screen_id else None,
            f"screens/{screen_id}.json" if screen_id else None,
        ):
            if ref:
                screens_by_ref[ref] = screen

    final_keys = set()
    stock_dates_by_chain = {}
    for stock in stocks:
        if stock.get("status") != "FINAL":
            continue
        ticker = _ticker(stock.get("ticker"))
        issuer_id = _identity(stock.get("issuer_id"))
        chain_id = _identity(stock.get("chain_id"))
        link_id = _identity(stock.get("link_id"))
        listing_id = _identity(stock.get("listing_id"))
        listing = listing_by_chain_ref.get((chain_id, listing_id))
        if (issuer_id and chain_id and link_id and ticker and listing_id
                and isinstance(listing, dict)
                and _identity(listing.get("issuer_id")) == issuer_id
                and _ticker(listing.get("ticker")) == ticker):
            final_keys.add((issuer_id, chain_id, link_id, ticker, listing_id))
            stock_dates_by_chain[chain_id] = _freshest(
                stock_dates_by_chain.get(chain_id), stock.get("as_of"),
                stock.get("updated_at"))

    pending_requests = [
        row for row in request_rows
        if isinstance(row, dict) and row.get("status") == "PENDING"
    ]
    pending_issuers = {
        issuer_id for issuer_id, profile in profiles.items()
        if profile.get("status") in ("PENDING_DATA", "BLOCKED")
    }
    for row in pending_requests:
        issuer_id = _identity(row.get("issuer_id"))
        request_campaign = _identity(row.get("campaign_id"))
        request_chain = _identity(row.get("chain_id"))
        ticker = _ticker(row.get("ticker"))
        if not issuer_id and ticker:
            candidates = (
                issuers_by_chain_ticker.get((request_chain, ticker), set())
                if request_chain else issuers_by_ticker.get(ticker, set())
            )
            if len(candidates) == 1:
                issuer_id = next(iter(candidates))
        relevant = (
            request_campaign == campaign.get("id")
            or request_chain in campaign_chain_ids
            or (not request_campaign and not request_chain)
        )
        if issuer_id and relevant:
            pending_issuers.add(issuer_id)

    # O1 is issuer-based, but its handoff is exact. The frozen selection_basis names the
    # screen, listing, chain, and link; nothing is inferred from a ticker or a placement.
    campaign_profile_issuers = {
        issuer_id for issuer_id, places in profile_places_by_issuer.items()
        if any(chain_id in campaign_chain_ids for chain_id, _ in places)
    }
    o1_issuers = {
        issuer_id for issuer_id in campaign_profile_issuers
        if profiles[issuer_id].get("status") == "COMPLETE"
        if _opportunity(profiles[issuer_id]).get("tier") == "O1"
        if (_opportunity(profiles[issuer_id]).get("campaign_id") in
            (None, campaign.get("id")))
    }
    selected_handoffs = {}
    o1_rows = []
    for issuer_id in sorted(o1_issuers):
        profile = profiles[issuer_id]
        opportunity = _opportunity(profile)
        opportunity_campaign = opportunity.get("campaign_id")
        if opportunity_campaign and opportunity_campaign != campaign.get("id"):
            continue
        basis = profile.get("selection_basis")
        selector = basis.get("screen_handoff") if isinstance(basis, dict) else None
        handoff = None
        if isinstance(selector, dict):
            chain_id = _identity(selector.get("chain_id"))
            link_id = _identity(selector.get("link_id"))
            listing_id = _identity(selector.get("listing_id"))
            screen_ref = _identity(selector.get("screen_ref"))
            listing = listing_by_chain_ref.get((chain_id, listing_id))
            screen = screens_by_ref.get(screen_ref)
            if screen is None and screen_ref:
                screen = (screens_by_ref.get(Path(screen_ref).name) or
                          screens_by_ref.get(Path(screen_ref).stem))
            ticker = _ticker(listing.get("ticker")) if isinstance(listing, dict) else None
            exact_row = (
                isinstance(screen, dict)
                and _identity(screen.get("chain_id")) == chain_id
                and any(
                    _identity(row.get("issuer_id")) == issuer_id
                    and _identity(row.get("listing_id")) == listing_id
                    and _identity(row.get("link_id")) == link_id
                    and _ticker(row.get("ticker")) == ticker
                    for row in _screen_rows(screen)
                )
            )
            if (
                chain_id in campaign_chain_ids
                and (issuer_id, chain_id, link_id) in profile_placements
                and listing_id in (profile.get("listing_refs") or [])
                and isinstance(listing, dict)
                and _identity(listing.get("issuer_id")) == issuer_id
                and exact_row
            ):
                handoff = (issuer_id, chain_id, link_id, ticker, listing_id)
        if handoff:
            selected_handoffs[issuer_id] = handoff

        rank = (opportunity.get("rank") if opportunity.get("rank") is not None
                else profile.get("opportunity_rank"))
        o1_rows.append({
            "issuer_id": issuer_id,
            "stock_ticker": handoff[3] if handoff else None,
            "listing_id": handoff[4] if handoff else None,
            "name": (profile.get("issuer_name") or profile.get("name") or
                     issuer_names.get(issuer_id) or issuer_id),
            "data_tier": profile.get("data_tier"),
            "opportunity_tier": "O1",
            "rank": rank,
            "chain_id": handoff[1] if handoff else None,
            "link_id": handoff[2] if handoff else None,
            "profile_status": profile.get("status"),
            "handoff_present": handoff is not None,
            "final": handoff in final_keys if handoff else False,
            "pending": issuer_id in pending_issuers,
            "as_of": profile.get("as_of"),
        })
    o1_rows.sort(key=lambda row: (
        row["rank"] is None,
        row["rank"] if isinstance(row["rank"], (int, float)) else 10**9,
        row["issuer_id"],
    ))

    projected_themes = []
    theme_company_sets = {}
    exact_final_issuers = {
        issuer_id for issuer_id, handoff in selected_handoffs.items()
        if handoff in final_keys
    }

    for descriptor in descriptors:
        chain_id = str(descriptor.get("chain_id") or descriptor.get("id") or "")
        chain = chain_by_id.get(chain_id, {})
        mapping_present = chain_id in mapping_by_chain
        mapping_doc = mapping_by_chain.get(chain_id, {})
        mapping_links = normalized_mapping_links.get(chain_id, {})
        chain_links = {
            str(link.get("id")): link for link in (chain.get("links") or [])
            if isinstance(link, dict) and link.get("id") is not None
        }
        link_ids = sorted(
            set(chain_links) | set(mapping_links),
            key=lambda link_id: (
                chain_links.get(link_id, {}).get("position", 10**9),
                link_id,
            ),
        )
        link_rows = []
        theme_issuers = set()
        coverage = _coverage_for(campaign, chain_id)
        theme_blockers = (
            _blocker_texts(descriptor.get("blockers")) +
            _blocker_texts(coverage.get("blockers"))
        )

        for link_id in link_ids:
            chain_link = chain_links.get(link_id, {})
            mapping_link = mapping_links.get(link_id, {})
            issuers = mapping_issuers_by_link.get((chain_id, link_id), set())
            theme_issuers.update(issuers)
            investability = (mapping_link.get("link_investability") or
                             chain_link.get("investability"))
            target = None
            if mapping_present:
                target = mapping_link.get("target")
                if target is None:
                    target = mapping_doc.get("target_issuers_per_link")
                if target is None:
                    target = mapping_target
            status = mapping_link.get("coverage_status")
            blocker = (
                mapping_link.get("blocker") or mapping_link.get("exhausted_basis")
            )
            blocker = _compact_text(blocker)
            completed = {
                issuer_id for issuer_id in issuers
                if (issuer_id, chain_id, link_id) in profile_placements
                and profiles[issuer_id].get("status") == "COMPLETE"
                and _opportunity(profiles[issuer_id]).get("tier") in ("O1", "O2")
            }
            pending = issuers & pending_issuers
            o1 = {
                issuer_id for issuer_id, handoff in selected_handoffs.items()
                if handoff[1] == chain_id and handoff[2] == link_id
            }
            finals = o1 & exact_final_issuers
            link_rows.append({
                "id": link_id,
                "name": chain_link.get("name") or mapping_link.get("name") or link_id,
                "position": chain_link.get("position"),
                "status": status,
                "investability": investability,
                "target": target,
                "mapped": len(issuers) if mapping_present else None,
                "profiled": len(completed),
                "o1": len(o1),
                "finals": len(finals),
                "pending": len(pending),
                "coverage_as_of": (mapping_link.get("as_of") or
                                    mapping_doc.get("as_of")),
                "blocker": blocker,
            })

        open_deficits = sum(
            1 for row in link_rows
            if isinstance(row.get("target"), int)
            and isinstance(row.get("mapped"), int)
            and row["mapped"] < row["target"]
            and row["status"] == "OPEN"
        )
        theme_company_sets[chain_id] = theme_issuers
        theme_profile_issuers = {
            issuer_id for issuer_id in theme_issuers
            if any((issuer_id, chain_id, link_id) in profile_placements
                   for link_id in chain_links | mapping_links)
            and profiles.get(issuer_id, {}).get("status") == "COMPLETE"
            and _opportunity(profiles.get(issuer_id, {})).get("tier") in ("O1", "O2")
        }
        theme_o1_issuers = {
            issuer_id for issuer_id, handoff in selected_handoffs.items()
            if handoff[1] == chain_id
        }
        theme_final_issuers = {
            issuer_id for issuer_id in theme_o1_issuers
            if issuer_id in exact_final_issuers
            and selected_handoffs[issuer_id][1] == chain_id
        }
        profile_dates = [
            profiles[issuer_id].get("as_of")
            for issuer_id in theme_issuers if issuer_id in profiles
        ]
        selection_as_of = (
            descriptor.get("selection_as_of") or descriptor.get("as_of") or
            (campaign.get("selection_basis") or {}).get("as_of") or
            campaign.get("as_of")
        )
        coverage_as_of = _freshest(
            descriptor.get("coverage_as_of"), coverage.get("as_of"),
            mapping_doc.get("as_of"), chain.get("heat_as_of"),
            chain.get("scenarios_as_of"), screen_dates_by_chain.get(chain_id),
            stock_dates_by_chain.get(chain_id), *profile_dates,
        )
        projected_themes.append({
            "id": chain_id,
            "signal_id": descriptor.get("signal_id"),
            "title": descriptor.get("title") or chain.get("title") or chain_id,
            "status": (descriptor.get("status") or descriptor.get("stage") or
                       coverage.get("status")),
            "mapping_present": mapping_present,
            "selection_as_of": selection_as_of,
            "coverage_as_of": coverage_as_of,
            "counts": {
                "links": len(link_rows),
                "mapped_issuers": (len(theme_issuers)
                                   if mapping_present else None),
                "profiles": len(theme_profile_issuers),
                "o1": len(theme_o1_issuers),
                "finals": len(theme_final_issuers),
                "pending": len(theme_issuers & pending_issuers),
                "blockers": len(theme_blockers) + open_deficits,
            },
            "blockers": theme_blockers,
            "links": link_rows,
        })

    campaign_issuers = (set().union(*theme_company_sets.values())
                        if theme_company_sets else set())
    derived_complete_profiles = {
        issuer_id for issuer_id in campaign_profile_issuers
        if profiles[issuer_id].get("status") == "COMPLETE"
        and _opportunity(profiles[issuer_id]).get("tier") in ("O1", "O2")
    }
    completion = (campaign.get("completion")
                  if isinstance(campaign.get("completion"), dict) else {})
    calibrated_tiers = (completion.get("opportunity_tiers")
                        if isinstance(completion.get("opportunity_tiers"), dict) else {})

    def calibrated_int(field, fallback):
        value = completion.get(field)
        return value if isinstance(value, int) and not isinstance(value, bool) else fallback

    campaign_pending_issuers = campaign_issuers & pending_issuers
    campaign_blockers = _blocker_texts(campaign.get("blockers"))
    selection_as_of = (
        (campaign.get("selection_basis") or {}).get("as_of") or
        campaign.get("selection_as_of") or campaign.get("as_of")
    )
    coverage_as_of = _freshest(
        campaign.get("coverage_as_of"),
        *(theme.get("coverage_as_of") for theme in projected_themes),
    )
    projection = {
        "present": True,
        "campaign_count": len(campaigns),
        "id": campaign.get("id"),
        "title": campaign.get("title"),
        "selection_as_of": selection_as_of,
        "coverage_as_of": coverage_as_of,
        "status": campaign.get("status"),
        "targets": {
            "themes": (targets.get("themes") if targets.get("themes") is not None
                       else targets.get("theme_count")),
            "mappings_per_link": mapping_target,
            "profiles_min": (targets.get("profiles_min")
                             if targets.get("profiles_min") is not None
                             else targets.get("completed_profiles_min")),
            "profiles_per_theme": targets.get("profiles_per_theme_min"),
            "opportunity_min": (targets.get("opportunity_min")
                                if targets.get("opportunity_min") is not None
                                else targets.get("o1_min")),
            "opportunity_max": (targets.get("opportunity_max")
                                if targets.get("opportunity_max") is not None
                                else targets.get("o1_max")),
        },
        "counts": {
            "themes": calibrated_int("themes_selected", len(projected_themes)),
            "links": sum(theme["counts"]["links"] for theme in projected_themes),
            "mapped_issuers": len(campaign_issuers),
            "profiles": calibrated_int(
                "completed_profiles", len(derived_complete_profiles)),
            "o1": (calibrated_tiers.get("O1")
                   if isinstance(calibrated_tiers.get("O1"), int)
                   else len(o1_issuers)),
            "finals": calibrated_int("o1_final", len(exact_final_issuers)),
            "pending": len(campaign_pending_issuers),
            "blockers": (len(campaign_blockers) +
                         sum(theme["counts"]["blockers"] for theme in projected_themes)),
        },
        "blockers": campaign_blockers,
        "themes": projected_themes,
        "o1": o1_rows,
    }
    return projection


BLOB_MARKER = "window.UPSTREAM_DATA = "


def extract_committed_payload():
    """The data blob out of the committed app/index.html, or None with a reason.

    CLAUDE.md says index.html "is never hand-edited", and nothing enforced it. `--check`
    validated data/ and assembled the HTML in memory, then threw it away without ever
    comparing it to the file on disk — so a page left stale for a week, or edited by
    hand, or built from data that has since moved, passed CI cleanly. The artifact is
    what Ron actually reads; a number on it that no longer matches data/ is a
    hallucination with a build step in front of it.
    """
    page = APP / "index.html"
    if not page.exists():
        return None, "app/index.html does not exist yet"
    text = page.read_text()
    i = text.find(BLOB_MARKER)
    if i < 0:
        return None, f"no {BLOB_MARKER!r} marker in app/index.html"
    start = i + len(BLOB_MARKER)
    end = text.find(";\n", start)
    if end < 0:
        return None, "the data blob is not terminated"
    try:
        return json.loads(text[start:end].replace("<\\/", "</")), None
    except Exception as e:  # noqa: BLE001
        return None, f"the embedded blob is not valid JSON: {e}"


def _is_contiguous_run(sub: list, whole: list) -> bool:
    """True when `sub` appears in `whole` as a run of consecutive items.

    Both are windows on the tail of data/ledger.md, so the page's window may start
    earlier than the current one; a page line that is not in the current window at all
    is only a problem if the page's run is not a suffix-aligned slice of it. Comparing
    on the overlap keeps this honest without failing on a slid window.
    """
    if not sub:
        return True
    for i in range(len(whole) - 1, -1, -1):
        if whole[i] == sub[-1]:
            n = min(len(sub), i + 1)
            if sub[-n:] == whole[i + 1 - n:i + 1]:
                return True
    # The page's last line may have slid out of the current 60-line window entirely.
    # Then the only check available is that every page line is a real ledger line.
    return all(s in whole for s in sub)


def compare_committed(payload: dict) -> list:
    """Top-level keys where the committed page disagrees with freshly-read data/.

    built_at is excluded: it changes on every build by design and says nothing about
    whether the content drifted.
    """
    committed, why = extract_committed_payload()
    if committed is None:
        return [why]
    drift = []
    for key in sorted(set(payload) | set(committed)):
        if key == "built_at":
            continue
        if key == "ledger":
            # The postlude order is build, THEN append the ledger line describing the
            # build, THEN commit — so the committed page is always a line or two behind
            # by construction. Requiring equality here would fail every honest commit.
            # What must hold is that the page invented nothing and dropped nothing: its
            # lines are a contiguous run of the real ledger, in order.
            page_lines, now_lines = committed.get(key) or [], payload.get(key) or []
            if page_lines and not _is_contiguous_run(page_lines, now_lines):
                drift.append("ledger: the committed page's lines are not a contiguous run "
                             "of data/ledger.md — the page shows entries the ledger does "
                             "not have, or in a different order")
            continue
        if key not in committed:
            drift.append(f"{key}: missing from the committed page")
        elif key not in payload:
            drift.append(f"{key}: on the committed page but no longer built")
        elif committed[key] != payload[key]:
            a, b = committed[key], payload[key]
            detail = ""
            if isinstance(a, list) and isinstance(b, list) and len(a) != len(b):
                detail = f" ({len(a)} on the page, {len(b)} in data/)"
            elif isinstance(a, dict) and isinstance(b, dict):
                moved = sorted(set(a) ^ set(b)) or \
                    sorted(k for k in set(a) & set(b) if a[k] != b[k])
                detail = f" (differs at: {', '.join(map(str, moved[:6]))})" if moved else ""
            drift.append(f"{key}: the committed page disagrees with data/{detail}")
    return drift


def assemble_html(payload: dict) -> str:
    """Assemble the single-file artifact in memory, without validation or writes."""
    shell = (APP / "templates" / "shell.html").read_text()
    css = (APP / "templates" / "app.css").read_text()
    js = (APP / "templates" / "app.js").read_text()
    if "</script" in js:
        raise ValueError(
            "app.js contains a literal </script>, which would terminate the inline tag; "
            "split the string")

    # Vendored third-party code (app/templates/vendor/README.md carries the vetting
    # record). Explicit allowlist, never a glob: order matters, and an unreviewed file
    # must never inline itself.
    vendor_js = ""
    for name in VENDOR_FILES:
        vf = APP / "templates" / "vendor" / name
        if not vf.exists():
            raise ValueError(f"missing vendor file {vf}")
        vt = vf.read_text()
        if "</script" in vt:
            raise ValueError(f"vendor/{name} contains a literal </script>")
        if "Copyright" not in vt:
            raise ValueError(
                f"vendor/{name} lost its licence banner "
                "(ISC requires the notice in all copies)")
        vendor_js += (
            f"/* vendored verbatim: {name} · "
            "licence: app/templates/vendor/LICENSE-d3.txt */\n"
            f"{vt}\n"
        )

    # Data is substituted last so its text cannot masquerade as a template placeholder.
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    return (shell.replace("{{APP_CSS}}", css)
                 .replace("{{VENDOR_JS}}", vendor_js)
                 .replace("{{APP_JS}}", js)
                 .replace("{{UPSTREAM_DATA}}", blob))


def main() -> int:
    check = "--check" in sys.argv

    r = subprocess.run([sys.executable, str(ROOT / "tools" / "validate.py")])
    if r.returncode != 0:
        print("build: refused — validation failed (fix data/, then rebuild)")
        return 1

    market = {}
    mdir = DATA / "market"
    if mdir.exists():
        for f in sorted(mdir.glob("*.json")):
            if f.name == "_meta.json":
                continue
            m = json.loads(f.read_text())
            market[f.stem] = m

    trades = []
    tfile = DATA / "trades.jsonl"
    if tfile.exists():
        for line in tfile.read_text().splitlines():
            if line.strip():
                trades.append(json.loads(line))

    ledger_lines = []
    lfile = DATA / "ledger.md"
    if lfile.exists():
        for line in lfile.read_text().splitlines():
            if line[:2].isdigit() and "|" in line:
                ledger_lines.append(line.strip())

    digests = read_json_dir(DATA / "digest")
    digests.sort(key=lambda d: d.get("week", ""), reverse=True)

    signals = read_json_dir(DATA / "signals")
    impact = read_json_dir(DATA / "impact")
    chains = read_json_dir(DATA / "chains")
    screens = read_json_dir(DATA / "screens")
    stocks = read_json_dir(DATA / "stocks")
    requests = json.loads((DATA / "requests.json").read_text()) if (DATA / "requests.json").exists() else {"requests": []}

    # trimmed feed subset for the cortex dust ring (title/source/family/date only)
    feeds_store = {"items": []}
    fp = DATA / "feeds" / "latest.json"
    if fp.exists():
        try:
            _f = json.loads(fp.read_text())
            _items = [it for it in _f.get("items", []) if isinstance(it, dict)]
            # `total` is the whole store; `items` is the subset inlined for the cortex
            # dust ring. The UI used to render len(items) under the word HELD, so the
            # page said "300 HELD" while the store held 317 and the Scout card two
            # clicks away said 317 — one corpus, two numbers, on one page. The
            # truncation is fine; reporting it as the total was not.
            # per-family counts are computed over the WHOLE store for the same reason
            # `total` is: the cortex family rail sits beside the HELD count, and a chip
            # counting only the inlined 300 would silently disagree with it.
            _fams: dict[str, int] = {}
            for it in _items:
                _fam = it.get("family")
                if _fam:
                    _fams[_fam] = _fams.get(_fam, 0) + 1
            feeds_store = {"as_of": _f.get("as_of"), "total": len(_items),
                           "families": _fams,
                           "items": [
                               {"t": (it.get("title") or "")[:110], "s": it.get("source"),
                                "f": it.get("family"), "d": it.get("ts")}
                               for it in _items[:300]]}
        except Exception:
            pass

    campaign_ix = build_campaign_ix(
        DATA, chains=chains, screens=screens, stocks=stocks, requests=requests)
    campaign_size = len(json.dumps(campaign_ix, separators=(",", ":")).encode())
    if campaign_size > CAMPAIGN_PROJECTION_MAX_BYTES:
        print("build: refused — compact campaign projection is "
              f"{campaign_size:,} bytes (> {CAMPAIGN_PROJECTION_MAX_BYTES:,}); "
              "do not inline profiles, market series, mapping evidence, or search logs")
        return 1

    payload = {
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
        "signals": signals,
        "chains": chains,
        "screens": screens,
        "stocks": stocks,
        "market": market,
        "shadow": {
            "book": json.loads((DATA / "shadow" / "book.json").read_text()) if (DATA / "shadow" / "book.json").exists() else {"rows": []},
            "results": json.loads((DATA / "shadow" / "results.json").read_text()) if (DATA / "shadow" / "results.json").exists() else {},
        },
        "trades": trades,
        "requests": requests,
        "health": {
            "sessions": json.loads((DATA / "health" / "sessions.json").read_text()) if (DATA / "health" / "sessions.json").exists() else {},
            "actions": json.loads((DATA / "health" / "actions.json").read_text()) if (DATA / "health" / "actions.json").exists() else {},
            # The orchestrator's resume point, derived from disk by
            # tools/campaign_board.py --write. Absent until that has run: null here means
            # "no board on disk", never "no work outstanding", and the Campaign view says
            # so rather than drawing an empty worklist.
            "board": json.loads((DATA / "health" / "board.json").read_text()) if (DATA / "health" / "board.json").exists() else None,
        },
        "ledger": ledger_lines[-60:],
        "digests": digests[:4],
        "indicators": json.loads((DATA / "indicators.json").read_text()) if (DATA / "indicators.json").exists() else {"trips": []},
        "calendar": json.loads((DATA / "calendar" / "events.json").read_text()) if (DATA / "calendar" / "events.json").exists() else {"events": []},
        "feeds": feeds_store,
        "candidates": json.loads((DATA / "radar" / "candidates.json").read_text()) if (DATA / "radar" / "candidates.json").exists() else {"candidates": []},
        "impact": impact,
        "rank": json.loads((DATA / "impact" / "_rank-log.json").read_text()) if (DATA / "impact" / "_rank-log.json").exists() else None,
        "scout": json.loads((DATA / "radar" / "scout-log.json").read_text()) if (DATA / "radar" / "scout-log.json").exists() else None,
        "map": json.loads((DATA / "chains" / "_map-log.json").read_text()) if (DATA / "chains" / "_map-log.json").exists() else None,
        "campaign_ix": campaign_ix,
        # The eight agent contracts, verbatim, plus the ownership map parsed out of the
        # command table. Ron drives eight agents and until now could not read what any of
        # them was told: the only agent-shaped text on the page was two section labels.
        # Inlining them makes `--check` refuse a page whose displayed instructions have
        # drifted from `.claude/agents/`, which is the property that makes them worth
        # showing. A contract body is DATA here, exactly like feed text: it instructs the
        # agent that runs under it, never the process that renders it.
        "agentix": agents_payload(ROOT),
        # The scoring thresholds, shipped to the page instead of retyped in it. app.js
        # had 60/40/60 and the band edges written as literals, duplicating
        # tools/validate.py — they agreed on the day they were written and nothing kept
        # them agreeing. A UI that draws a money-corner box from its own copy of the
        # rule can disagree with the validator that computed the verdict.
        "method": {
            "money_corner": {"impact_min": 60, "crowd_max": 40, "capture_min": 60},
            "bands": {"over_crowded_above": 80, "crowded_above": 60, "emerging_above": 40,
                      "undiscovered_impact_min": 60},
            # Same reason as the heat bands above: the impact band edges live in
            # tools/impact_score.py and are shipped here rather than retyped in app.js.
            "impact": {"reach_min": 60, "capture_min": 40, "prime_capture_min": 60,
                       "money_bands": {"LT_1B": 15, "B1_10": 40, "B10_100": 70,
                                       "GT_100B": 90}},
        },
    }

    try:
        html = assemble_html(payload)
    except ValueError as exc:
        print(f"build: refused — {exc}")
        return 1

    size_mb = len(html.encode()) / 1e6
    if size_mb > SIZE_WARN_MB:
        print(f"build: WARNING index.html is {size_mb:.1f} MB (> {SIZE_WARN_MB} MB) — consider pruning ARCHIVED series from the inline blob")

    if check:
        drift = compare_committed(payload)
        if drift:
            print("build: --check FAILED — app/index.html does not match data/:")
            for d in drift:
                print(f"  - {d}")
            print("build: run `python3 app/build.py` and commit the result")
            return 1
        print(f"build: --check OK ({size_mb:.2f} MB, {len(payload['signals'])} signals, "
              f"{len(payload['chains'])} chains, {len(payload['stocks'])} dives; "
              f"committed page matches data/)")
        return 0

    (APP / "index.html").write_text(html)
    print(f"build: wrote app/index.html ({size_mb:.2f} MB) at {payload['built_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
