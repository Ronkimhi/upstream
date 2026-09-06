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
# Ron, 2026-09-03: "I don't care about the digital size of the pages, the megabytes. I
# just want all the data." So there is no page budget and no fidelity ladder any more:
# every store is carried at the fidelity its files hold, and the only ceiling is the
# artifact platform's own 16 MB per page, which the build REFUSES to cross rather than
# trimming to fit. Price series are encoded compactly (encode_series_rows) so that
# ceiling stays far away as the campaign fills. The 2 MB budget this replaced had cut
# four of five dives and nine of eleven chains down to navigation on a 1.9 MB page.
PAGE_MAX_MB = 16.0
PAGE_REFUSE_MARGIN_BYTES = 500_000
# The campaign index is a compact projection by design (profiles, mapping evidence and
# search logs have their own stores); this refusal keeps someone from inlining them there.
CAMPAIGN_PROJECTION_MAX_BYTES = 2_000_000
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from agent_registry import agents_payload  # noqa: E402
from theme_calibrate import BASELINE_WEEKS, SURGE_MIN_COUNT, SURGE_MULTIPLE  # noqa: E402
from check_campaign import (  # noqa: E402
    canonical_mapped_placements,
    validated_public_listings,
)
# Vendored UMD modules, inlined in this exact order (each attaches to window.d3;
# d3-force resolves the other three off that object at define time). Vetting record
# and licence: app/templates/vendor/README.md and LICENSE-d3.txt.
VENDOR_FILES = ["d3-quadtree.min.js", "d3-dispatch.min.js", "d3-timer.min.js", "d3-force.min.js"]


class PayloadTooLarge(ValueError):
    """A store outgrew the one-file artifact. Raised, never silently truncated."""


def read_json_dir(folder: Path) -> list:
    out = []
    if folder.exists():
        for f in sorted(folder.glob("*.json")):
            if f.name.startswith("_"):
                continue
            out.append(json.loads(f.read_text()))
    return out


def _compact_text(value, limit=None):
    """One-line display text: whitespace collapsed, never truncated (2026-09-04)."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text or None


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


BOARD_VERDICT_ORDER = {"INVESTABLE": 0, "WATCH": 1, "TOO_LATE": 2}
BOARD_HEAT_ORDER = {"UNDISCOVERED": 0, "EMERGING": 1, "QUIET": 2, "CROWDED": 3,
                    "OVER_CROWDED": 4}


def build_board(data_dir=DATA, chains=None, stocks=None, campaign_ix=None) -> dict:
    """The page's front door (Ron, 2026-09-01): names first, then what stops the rest.

    Four lists, every row a projection of a store already on the page or on disk, no
    number invented here: FINAL and DRAFT dives ranked INVESTABLE, WATCH, TOO_LATE; the O1
    queue with its dive state; O2 profiles by the heat of the link they sit on; and BLOCKED
    profiles with the first data gap the profile itself recorded. Themes come from the
    campaign projection with the blocker text it already carries. Caps are printed as
    `*_total` beside the list they cut, so a cut is never silent.
    """
    data_dir = Path(data_dir)
    chains = list(chains or [])
    stocks = list(stocks or [])
    campaign_ix = campaign_ix or {}
    companies = read_json_dir(data_dir / "companies")
    profiles = {p.get("issuer_id"): p for p in companies if p.get("issuer_id")}

    heat_by_link = {}
    chain_titles = {}
    for chain in chains:
        chain_titles[chain.get("id")] = chain.get("title") or chain.get("id")
        for link in chain.get("links") or []:
            heat = link.get("heat") if isinstance(link.get("heat"), dict) else {}
            heat_by_link[(chain.get("id"), link.get("id"))] = {
                "name": link.get("name") or link.get("id"),
                "verdict": heat.get("verdict"),
                "money_corner": heat.get("money_corner") is True,
            }

    def _profile_ticker(profile):
        for listing in profile.get("listings") or []:
            if isinstance(listing, dict) and listing.get("ticker"):
                return listing.get("ticker")
        refs = profile.get("listing_refs") or []
        return refs[0].split(":")[-1].split("-")[-1] if refs else None

    verdicts = []
    dived_issuers = {}
    for stock in stocks:
        key = (stock.get("issuer_id"), stock.get("chain_id"))
        dived_issuers[stock.get("issuer_id")] = stock.get("status")
        link = heat_by_link.get((stock.get("chain_id"), stock.get("link_id")), {})
        verdicts.append({
            "ticker": stock.get("ticker"),
            "name": stock.get("name"),
            "chain_id": stock.get("chain_id"),
            "chain_title": chain_titles.get(stock.get("chain_id")),
            "link_id": stock.get("link_id"),
            "link_name": link.get("name"),
            "verdict": stock.get("verdict"),
            "clock": stock.get("clock"),
            "tier": stock.get("tier"),
            "status": stock.get("status"),
            "entry_zone": stock.get("entry_zone"),
            "no_entry_above": stock.get("no_entry_above"),
            "watch_triggers": [
                {k: t.get(k) for k in ("metric", "direction", "level") if k in t}
                for t in (stock.get("watch_triggers") or []) if isinstance(t, dict)
            ],
            "review_by": stock.get("review_by"),
            "updated_at": stock.get("updated_at") or stock.get("as_of"),
        })
    verdicts.sort(key=lambda r: (
        0 if r["status"] == "FINAL" else 1,
        BOARD_VERDICT_ORDER.get(r["verdict"], 9),
        str(r["updated_at"] or ""),
    ))
    verdicts.sort(key=lambda r: (0 if r["status"] == "FINAL" else 1,
                                 BOARD_VERDICT_ORDER.get(r["verdict"], 9)))

    o1_queue, o2, blocked = [], [], []
    for issuer_id, profile in sorted(profiles.items()):
        tier = profile.get("opportunity_tier")
        placements = [p for p in profile.get("placements") or [] if isinstance(p, dict)]
        first = placements[0] if placements else {}
        link = heat_by_link.get((first.get("chain_id"), first.get("link_id")), {})
        row = {
            "issuer_id": issuer_id,
            "name": profile.get("issuer_name"),
            "ticker": _profile_ticker(profile),
            "chain_id": first.get("chain_id"),
            "link_id": first.get("link_id"),
            "link_name": link.get("name"),
            "data_tier": profile.get("data_tier"),
        }
        if tier == "O1" and profile.get("status") == "COMPLETE":
            handoff = ((profile.get("selection_basis") or {}).get("screen_handoff") or {})
            row.update({"chain_id": handoff.get("chain_id") or row["chain_id"],
                        "link_id": handoff.get("link_id") or row["link_id"],
                        "dive_status": dived_issuers.get(issuer_id)})
            o1_queue.append(row)
        elif tier == "O2" and profile.get("status") == "COMPLETE":
            row.update({"heat_verdict": link.get("verdict"),
                        "money_corner": link.get("money_corner", False)})
            o2.append(row)
        elif profile.get("status") in ("BLOCKED", "DRAFT"):
            gaps = [g for g in profile.get("data_gaps") or [] if isinstance(g, str)]
            row.pop("link_name", None)
            row.update({"status": profile.get("status"),
                        "on": _compact_text(gaps[0]) if gaps else None,
                        "gaps": gaps})
            blocked.append(row)
    o2.sort(key=lambda r: (0 if r["money_corner"] else 1,
                           BOARD_HEAT_ORDER.get(r["heat_verdict"], 9), r["issuer_id"]))
    blocked.sort(key=lambda r: (str(r["chain_id"] or ""), r["issuer_id"]))

    themes = []
    for theme in campaign_ix.get("themes") or []:
        counts = theme.get("counts") or {}
        blockers = theme.get("blockers") or []
        themes.append({
            "id": theme.get("id"),
            "title": theme.get("title"),
            "stage": theme.get("status"),
            "profiles": counts.get("profiles"),
            "o1": counts.get("o1"),
            "finals": counts.get("finals"),
            "refusing": _compact_text(blockers[0]) if blockers else None,
            "blockers": blockers,
        })

    return {
        "verdicts": verdicts,
        "o1_queue": o1_queue,
        "o2": o2,
        "o2_total": len(o2),
        "blocked": blocked,
        "blocked_total": len(blocked),
        "themes": themes,
        "counts": {
            "final": sum(1 for r in verdicts if r["status"] == "FINAL"),
            "draft": sum(1 for r in verdicts if r["status"] != "FINAL"),
            "o1": len(o1_queue),
            "o2": len(o2),
            "blocked": len(blocked),
            "profiles": len(profiles),
        },
    }


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


# --- store projections ------------------------------------------------------------
#
# One rule, applied store by store: THE PAGE CARRIES WHAT IT RENDERS, AND SAYS WHAT IT
# DID NOT CARRY. build_campaign_ix above is the established form of it for the biggest
# stores (mappings, companies); these are the same move for the rest.
#
# The reason this became urgent on 2026-08-30: the page was 2,636,580 bytes against a
# 2,000,000-byte warn threshold, and a ten-theme campaign heading for 10 chains, ~200
# profiles, 30-60 dives and several hundred tickers put it on a path to 15-20 MB against
# a 16 MB artifact hard cap. Measured composition at the time: `impact` 727 KB (31%),
# `market` 580 KB (25%).
#
# The discipline that makes this safe is tools/check_render.py's: a cut the reader cannot
# see is the same defect as a number the data never had. So every projection below either
# (a) drops something the renderer provably never reads, which is invisible because it was
# already invisible, or (b) carries a count/denominator beside what it kept, which app.js
# is required to print.



def _history(doc: dict) -> dict:
    """`changelog` and `notes` carried whole, with their counts beside them.

    Both are append-only. Until 2026-09-04 the page carried a ten-row tail and printed the
    denominator; Ron's instruction that day ("I just want all the data") ends the tail.
    `_total` stays because app.js prints it.
    """
    out = {}
    for key in ("changelog", "notes"):
        rows = doc.get(key)
        if not isinstance(rows, list):
            continue
        out[key] = list(rows)
        out[key + "_total"] = len(rows)
    return out


# Blocks in a market file that no template renders. Everything else is carried whole.
MARKET_UNRENDERED = ("fundamentals", "insider", "prints", "legs")


def encode_series_rows(rows: list) -> dict:
    """Compact, lossless form of a [date, close, ...] series.

    `start` is the first row's date; `d` holds each row's day offset from it as an int;
    `c` holds the rest of the row (the close alone when the row is a pair). app.js's
    decodeSeries() rebuilds the exact rows at boot. Roughly a third of the bytes of the
    row list, which is what keeps 400+ full daily series inside one 16 MB page. A row
    whose first field is not an ISO date is carried verbatim under `raw`, never dropped.
    """
    from datetime import date as _date
    start = None
    offsets, closes, raw = [], [], []
    for row in rows:
        try:
            day = _date.fromisoformat(str(row[0]))
            rest = row[1] if len(row) == 2 else list(row[1:])
        except (ValueError, TypeError, IndexError):
            raw.append(row)
            continue
        if start is None:
            start = day
        offsets.append((day - start).days)
        closes.append(rest)
    out = {"start": start.isoformat() if start else None, "d": offsets, "c": closes}
    if raw:
        out["raw"] = raw
    return out


def project_market(market: dict) -> dict:
    """Every market file whole except the blocks no template renders (MARKET_UNRENDERED),
    with the full daily series for EVERY ticker, compactly encoded (encode_series_rows).
    `row_count`, `inlined_rows` and `sampling` stay on the series header because app.js
    prints them; since 2026-09-04 they always read equal and COMPLETE."""
    out = {}
    for key in sorted(market):
        doc = market[key] or {}
        row = {k: v for k, v in doc.items() if k not in MARKET_UNRENDERED}
        series = doc.get("series")
        if isinstance(series, dict):
            rows = series.get("rows") if isinstance(series.get("rows"), list) else []
            head = {k: v for k, v in series.items() if k != "rows"}
            head["row_count"] = len(rows)
            head["inlined_rows"] = len(rows)
            head["sampling"] = "COMPLETE"
            head["rows_c"] = encode_series_rows(rows)
            row["series"] = head
        out[key] = row
    return out


def project_impact(appraisals: list) -> list:
    """Every appraisal whole: legs with rationale, basis and every evidence row's verbatim
    excerpt, plus notes, changelog, confidence_audit, ticker_refs and review_by. Each leg
    carries `evidence_count` because the card prints it beside the rows."""
    legs = ("money_at_stake", "public_reach", "capture_odds", "timing_fit")
    out = []
    for doc in appraisals:
        if not isinstance(doc, dict):
            continue
        row = dict(doc)
        row.update(_history(doc))
        row["legs_inlined"] = True
        for name in legs:
            block = doc.get(name)
            if isinstance(block, dict):
                leg = dict(block)
                evidence = block.get("evidence")
                leg["evidence_count"] = len(evidence) if isinstance(evidence, list) else 0
                row[name] = leg
        out.append(row)
    return out


# --- chains ------------------------------------------------------------------------
# Every chain whole (Ron, 2026-09-03). The campaign manifest's theme rank is still read
# (campaign_chain_order) and recorded in the `carried` note, but nothing is cut by it.
CHAIN_FULL = "FULL"  # the only fidelity since 2026-09-04

HEAT_LEGS = ("impact", "crowdedness", "capture")


def _heat_leg(leg: dict) -> dict:
    """One heat leg, whole, with its denominators: every evidence row with its verbatim
    `source_excerpt`, `url` and `source_date`, plus `evidence_total` and `excerpts_held`
    (equal to what is carried, and printed by app.js)."""
    out = dict(leg)
    items = leg.get("evidence")
    if isinstance(items, list):
        out["evidence"] = list(items)
        out["evidence_total"] = len(items)
        out["excerpts_held"] = sum(1 for item in items
                                   if isinstance(item, dict) and item.get("source_excerpt"))
    return out


def _project_link(link: dict) -> dict:
    """One chain link, whole: map citations, capture judgments, bottleneck note, every
    heat leg with every evidence row and its verbatim excerpt, the repricing check with
    its per-leg basis. The counts app.js already prints ride along beside the rows."""
    row = dict(link)
    evidence = link.get("evidence")
    if isinstance(evidence, list):
        row["evidence_count"] = len(evidence)
    inputs = link.get("capture_inputs")
    if isinstance(inputs, (dict, list)):
        row["capture_inputs_count"] = len(inputs)
    heat = link.get("heat")
    if isinstance(heat, dict):
        full = dict(heat)
        for leg in HEAT_LEGS:
            block = heat.get(leg)
            if isinstance(block, dict):
                full[leg] = _heat_leg(block)
        check = heat.get("repricing_check")
        if isinstance(check, dict):
            slim = dict(check)
            legs = check.get("legs")
            if isinstance(legs, list):
                slim["legs_detail_held"] = len(legs)
            full["repricing_check"] = slim
        row["heat"] = full
    return row


def _project_scenario(scenario: dict) -> dict:
    """One scenario, whole: evidence rows, each moved link's `why`, each indicator's
    `check_basis`. `evidence_count` rides along because the tab prints it."""
    row = dict(scenario)
    evidence = scenario.get("evidence")
    if isinstance(evidence, list):
        row["evidence_count"] = len(evidence)
    return row


def _project_chain(chain: dict) -> dict:
    """One chain, whole, stamped FULL (the only fidelity since 2026-09-04)."""
    doc = dict(chain)
    doc.update(_history(chain))
    links = chain.get("links")
    if isinstance(links, list):
        doc["links"] = [_project_link(l) if isinstance(l, dict) else l for l in links]
    scenarios = chain.get("scenarios")
    if isinstance(scenarios, list):
        doc["scenarios"] = [_project_scenario(s) if isinstance(s, dict) else s
                            for s in scenarios]
    doc["chain_fidelity"] = CHAIN_FULL
    return doc


def campaign_chain_order(data_dir=DATA) -> list:
    """Chain ids in the campaign manifest's theme-rank order, best first.

    The manifest freezes a rank per theme with a written rationale (`run campaign init`,
    method §6A). Reusing it here means the elastic budget below spends the page on the
    themes the campaign already argued are worth the most, instead of on whichever chain
    happens to sort first. Returns [] when there is no manifest, and the caller then falls
    back to chain id order, which is at least stable across builds.
    """
    campaigns = read_json_dir(Path(data_dir) / "campaigns")
    if not campaigns:
        return []
    campaigns.sort(key=lambda c: (str(c.get("as_of") or ""), str(c.get("id") or "")))
    active = [c for c in campaigns if c.get("status") == "ACTIVE"]
    campaign = (active or campaigns)[-1]
    ranked = []
    for i, theme in enumerate(_campaign_descriptors(campaign)):
        chain_id = theme.get("chain_id")
        if not chain_id:
            continue
        rank = theme.get("rank")
        ranked.append(((rank if isinstance(rank, int) else 10_000 + i), str(chain_id)))
    ranked.sort()
    out = []
    for _, chain_id in ranked:
        if chain_id not in out:
            out.append(chain_id)
    return out



def project_chains(chains: list, order=()) -> tuple:
    """Every chain whole. `order` (the campaign manifest's theme rank) is recorded in the
    note; the rows come back in chain-id order, which is stable across builds."""
    docs = [c for c in chains if isinstance(c, dict)]
    rows = [_project_chain(c) for c in docs]
    rows.sort(key=lambda c: str(c.get("id") or ""))
    note = {"carried": len(rows), "summary": 0, "index_only": 0, "total": len(rows),
            "order": ("campaign theme rank" if order else "chain id")}
    return rows, note


def project_signals(signals: list) -> list:
    """Signals with their history windowed. Evidence and prose are rendered, so they stay."""
    out = []
    for signal in signals:
        if not isinstance(signal, dict):
            out.append(signal)
            continue
        doc = dict(signal)
        doc.update(_history(signal))
        out.append(doc)
    return out


def _row_valuation(ticker, market):
    """Compact valuation for a screen row's ticker, drawn from its market file.

    The link modal shows market cap / price / 52-week range beside the screen's
    fundamentals. Those live in data/market/<T>.json, but project_market() strips
    `fundamentals` from every ticker and `quality` from every non-dived one, so a modal
    that read D.market would find nothing for a screened-but-undived name (POWL, MYRG, …).
    Carrying the four numbers on the screen row instead makes them survive that trimming:
    the row is kept whole by project_screens, and this block is ~8 rows x 4 numbers.

    Every field is None where the source is absent and is never fabricated — the two
    foreign names (HPS-A.TO, ENR.DE) resolve week52 only, cap/price stay None. Returns
    None when nothing at all resolved, so the payload carries no empty blocks.
    """
    if not ticker or not isinstance(market, dict):
        return None
    doc = market.get(str(ticker).replace(".", "-")) or market.get(str(ticker))
    if not isinstance(doc, dict):
        return None
    q = doc.get("quality") or {}
    derived = q.get("derived") or {}
    price_used = q.get("price_used") or {}
    week52 = doc.get("week52") or {}
    val = {
        "market_cap": derived.get("market_cap"),
        "price": price_used.get("value"),
        "price_as_of": price_used.get("as_of"),
        "week52_low": week52.get("low"),
        "week52_high": week52.get("high"),
    }
    if all(v is None for v in val.values()):
        return None
    return val


def project_screens(screens: list, market=None) -> list:
    out = []
    for screen in screens:
        if not isinstance(screen, dict):
            out.append(screen)
            continue
        doc = dict(screen)
        doc.update(_history(screen))
        buckets = screen.get("buckets")
        if isinstance(buckets, dict):
            new_buckets = {}
            for bname, rows in buckets.items():
                if not isinstance(rows, list):
                    new_buckets[bname] = rows
                    continue
                new_rows = []
                for r in rows:
                    if not isinstance(r, dict):
                        new_rows.append(r)
                        continue
                    nr = dict(r)
                    val = _row_valuation(nr.get("ticker"), market)
                    if val is not None:
                        nr["valuation"] = val
                    new_rows.append(nr)
                new_buckets[bname] = new_rows
            doc["buckets"] = new_buckets
        out.append(doc)
    return out



def project_stocks(stocks: list) -> tuple:
    """Every dive whole, with its history, newest-updated first in the note's order and
    ticker order in the rows. Returns (rows, note); the note is the denominator app.js
    prints, and since 2026-09-04 it always reads carried == total."""
    rows = []
    for stock in stocks:
        if not isinstance(stock, dict):
            continue
        doc = dict(stock)
        doc.update(_history(stock))
        doc["detail_inlined"] = True
        rows.append(doc)
    rows.sort(key=lambda s: (str(s.get("ticker") or ""), str(s.get("chain_id") or "")))
    note = {"carried": len(rows), "summary": 0, "index_only": 0, "total": len(rows),
            "order": "every dive whole"}
    return rows, note


def project_requests(requests: dict) -> dict:
    """Every request row, whole. `settled` is the count of rows that are neither PENDING
    nor FAILED, printed beside the two open counts on the cortex register."""
    rows = [r for r in ((requests or {}).get("requests") or []) if isinstance(r, dict)]
    open_rows = [r for r in rows if r.get("status") in ("PENDING", "FAILED")]
    return {"requests": rows, "total": len(rows),
            "settled": len(rows) - len(open_rows)}



def project_candidates(candidates: dict) -> dict:
    """Every candidate whole, including its selection audit and changelog, with the
    changelog count beside it because the drawer prints the count."""
    rows = (candidates or {}).get("candidates") or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        projected = dict(row)
        changelog = row.get("changelog")
        if isinstance(changelog, list):
            projected["changelog_total"] = len(changelog)
        out.append(projected)
    return {"as_of": (candidates or {}).get("as_of"), "candidates": out,
            "total": len(rows)}


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


def _strip_derived_sizes(obj):
    """Drop allocation bookkeeping before comparing a committed page against data/.

    Keys ending in `budget_bytes` record how many bytes a projection was ALLOWED, not
    what it says. They are computed from the total payload size, and the payload includes
    `data/ledger.md` — which the postlude appends to at step 3, AFTER the build at step 2
    and before the commit at step 5. So every honest commit ships a page whose budget
    numbers were computed against a ledger one line shorter than the one it commits, and
    those numbers can never match on re-check. That is not drift, it is arithmetic about
    the file's own size, and on 2026-08-31 it held CI red across a dozen commits while
    every session was doing the right thing.

    This is the third instance of one pattern: build, then append, then commit. The ledger
    and health.sessions were special-cased for it; this generalises the same insight to
    anything whose value is a function of the payload's byte size. Content is still
    compared exactly — a chain, a stock, a score or a quote that disagrees still fails.
    """
    if isinstance(obj, dict):
        return {k: _strip_derived_sizes(v) for k, v in obj.items()
                if not k.endswith("budget_bytes")}
    if isinstance(obj, list):
        return [_strip_derived_sizes(v) for v in obj]
    return obj


def _is_ordered_subsequence(sub: list, whole: list) -> bool:
    """True when every line of `sub` appears in `whole`, in the same relative order.

    This was `_is_contiguous_run` and required the page's lines to be CONSECUTIVE in the
    ledger. That assumption was wrong and it blocked CI on 2026-08-31 with a false
    positive: the page carried ledger lines 151-171 and 173-176, skipping 172, so the
    contiguity test failed and reported "the page shows entries the ledger does not
    have". It showed no such thing — every line on the page was a real ledger line, and
    the page was MISSING one, which is the opposite complaint.

    The cause is that `data/ledger.md` is append-only but not strictly ordered. Several
    sessions write to it concurrently, timestamps arrive out of order, and a conflict
    resolved by union-and-sort inserts a line into the MIDDLE of the file. A page built
    before that insert legitimately holds lines on both sides of it. The build also caps
    the ledger payload by byte budget, which can drop a line from the middle of a window.
    Neither is a defect, and a gate that fails on both trains its reader to ignore it.

    The invariant that actually matters is unchanged and is what this now tests: the page
    may LAG the ledger or omit lines from it, but it may never show a line the ledger does
    not contain, and never in a different order. Invention and reordering still fail.
    """
    if not sub:
        return True
    it = iter(whole)
    return all(any(w == s for w in it) for s in sub)


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
        if key == "health":
            # `data/health/sessions.json` is stamped at postlude step 4, AFTER the build
            # at step 2 and before the commit at step 5. The committed page therefore
            # CANNOT carry the stamp that the same commit adds — every honest postlude
            # produces this mismatch, and it kept CI red on 2026-08-31 while the sessions
            # doing the work were doing it correctly. Same structural lag the ledger has,
            # and it was special-cased there and missed here.
            #
            # `actions` stays strict: the fetch workflow writes it and rebuilds in the
            # same job, so it has no lag and a disagreement there is real drift.
            page_h = dict(committed.get(key) or {})
            now_h = dict(payload.get(key) or {})
            page_h.pop("sessions", None)
            now_h.pop("sessions", None)
            if page_h != now_h:
                drift.append("health.actions: the committed page disagrees with data/")
            continue
        if key == "ledger":
            # The postlude order is build, THEN append the ledger line describing the
            # build, THEN commit — so the committed page is always a line or two behind
            # by construction. Requiring equality here would fail every honest commit.
            # What must hold is that the page invented nothing and dropped nothing: its
            # lines are a contiguous run of the real ledger, in order.
            # Compare against the WHOLE ledger file, not the payload's window. Both the
            # page's list and the payload's are byte-capped tails of data/ledger.md, and
            # the two windows are cut at different points, so a line present on the page
            # can be absent from today's window while being a perfectly real ledger line.
            # Checking page-against-window reported invention where there was none and
            # kept CI red on 2026-08-31 even after the contiguity bug was fixed. The file
            # is the truth; the window is just what the page had room for.
            page_lines = committed.get(key) or []
            try:
                now_lines = [ln.strip() for ln in (DATA / "ledger.md").read_text().splitlines()
                             if ln[:2].isdigit() and "|" in ln]
            except Exception:  # noqa: BLE001
                now_lines = payload.get(key) or []
            if page_lines and not _is_ordered_subsequence(page_lines, now_lines):
                drift.append("ledger: the committed page shows line(s) that data/ledger.md does "
                             "not contain, or shows them in a different order")
            continue
        if key not in committed:
            drift.append(f"{key}: missing from the committed page")
        elif key not in payload:
            drift.append(f"{key}: on the committed page but no longer built")
        elif _strip_derived_sizes(committed[key]) != _strip_derived_sizes(payload[key]):
            a, b = _strip_derived_sizes(committed[key]), _strip_derived_sizes(payload[key])
            detail = ""
            if isinstance(a, list) and isinstance(b, list) and len(a) != len(b):
                detail = f" ({len(a)} on the page, {len(b)} in data/)"
            elif isinstance(a, dict) and isinstance(b, dict):
                moved = sorted(set(a) ^ set(b)) or \
                    sorted(k for k in set(a) & set(b) if a[k] != b[k])
                detail = f" (differs at: {', '.join(map(str, moved[:6]))})" if moved else ""
            drift.append(f"{key}: the committed page disagrees with data/{detail}")
    return drift


GUIDE_FRAGMENT = APP / "templates" / "guide.html"
GUIDE_SHELL = APP / "templates" / "guide-shell.html"
GUIDE_PAGE = APP / "guide.html"
# A string that would end the element the fragment is inlined into, or that the
# substitution below would treat as a placeholder. Refused, never escaped: the guide is
# hand-written markup and a fix belongs in the template.
GUIDE_FORBIDDEN = ("</script", "</template", "{{")


def read_guide_fragment(text=None) -> str:
    """The onboarding guide (app/templates/guide.html), checked for the three strings
    that would break its two hosts. Shipped inside <template id="upstream-guide"> in
    index.html and wrapped into the standalone app/guide.html."""
    if text is None:
        if not GUIDE_FRAGMENT.exists():
            raise ValueError(f"missing guide template {GUIDE_FRAGMENT}")
        text = GUIDE_FRAGMENT.read_text()
    for bad in GUIDE_FORBIDDEN:
        if bad in text:
            raise ValueError(
                f"guide.html contains {bad!r}, which would terminate the element it is "
                "inlined into or read as a template placeholder; rewrite the fragment")
    return text


def assemble_guide_html() -> str:
    """The standalone guide page: the fragment inside its own small shell, sharing the
    dashboard's stylesheet so both copies look identical. Deterministic by design (no
    build stamp), so --check can compare the committed file byte for byte."""
    shell = GUIDE_SHELL.read_text()
    css = (APP / "templates" / "app.css").read_text()
    return shell.replace("{{APP_CSS}}", css).replace("{{GUIDE_HTML}}", read_guide_fragment())


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
                 .replace("{{GUIDE_HTML}}", read_guide_fragment())
                 .replace("{{UPSTREAM_DATA}}", blob))


def store_sizes(payload: dict) -> dict:
    """Serialized bytes of every top-level key, so the page's weight has a named owner."""
    return {k: len(json.dumps(v, separators=(",", ":")).encode())
            for k, v in payload.items()}



def build_payload(data_dir=DATA, root=ROOT):
    """Read data/ and return the browser payload, projections applied.

    Pulled out of main() on 2026-08-30 so the page's SCALE is testable. main() reads the
    one real data/ and nothing could ask "what does this build look like at ten chains
    and sixty dives?" — which is the question the campaign made urgent, and the one
    tools/tests/test_campaign_ui.py::test_projected_campaign_scale_fits_the_page now
    answers over a synthetic tree.
    """
    DATA = data_dir  # noqa: N806 — shadows the module default on purpose, see signature
    ROOT = root  # noqa: N806

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
                               {"t": it.get("title"), "s": it.get("source"),
                                "f": it.get("family"), "d": it.get("ts"),
                                "u": it.get("url"), "i": it.get("id"),
                                "sm": it.get("summary")}
                               for it in _items]}
        except Exception:
            pass

    # The occurrence log, trimmed the same way the feed dust ring is and for the same
    # reason: `total` is the whole store, `rows` is what the page can afford to carry. The
    # UI must never print len(rows) where the corpus size belongs -- that exact mistake put
    # "300 HELD" on a page whose store held 317.
    themes_store = None
    tf, of = DATA / "themes" / "themes.json", DATA / "themes" / "occurrences.json"
    if tf.exists() and of.exists():
        try:
            _t = json.loads(tf.read_text())
            _o = json.loads(of.read_text())
            _rows = [r for r in _o.get("occurrences", []) if isinstance(r, dict)]
            _rows.sort(key=lambda r: (str(r.get("ts") or ""), str(r.get("id") or "")), reverse=True)
            themes_store = {
                "as_of": _t.get("as_of"),
                "themes": [{k: v for k, v in t.items() if k != "changelog"}
                           for t in _t.get("themes", [])],
                "calibration": _t.get("calibration") or {},
                "total": len(_o.get("occurrences", [])),
                # Short keys and only the fields the page renders. `origin_ref` rides
                # along only for the origins the page can link to; every feed id on the
                # page would be 355 dead strings, since the store they point into prunes.
                "rows": [{"i": r.get("id"), "t": r.get("title"),
                          "s": r.get("source"), "u": r.get("url"), "d": r.get("ts"),
                          "o": r.get("origin"), "r": r.get("origin_ref"),
                          "f": r.get("family"), "th": r.get("theme_id"),
                          "b": r.get("theme_basis"), "by": r.get("theme_by")}
                         for r in _rows],
            }
        except Exception:
            themes_store = None

    campaign_ix = build_campaign_ix(
        DATA, chains=chains, screens=screens, stocks=stocks, requests=requests)
    campaign_size = len(json.dumps(campaign_ix, separators=(",", ":")).encode())
    if campaign_size > CAMPAIGN_PROJECTION_MAX_BYTES:
        raise PayloadTooLarge(
            "compact campaign projection is "
            f"{campaign_size:,} bytes (> {CAMPAIGN_PROJECTION_MAX_BYTES:,}); "
            "do not inline profiles, market series, mapping evidence, or search logs")

    # One pass, one fidelity: every chain and every dive whole (Ron, 2026-09-03).
    chain_order = campaign_chain_order(DATA)
    projected_chains, chain_note = project_chains(chains, chain_order)
    projected_stocks, stock_note = project_stocks(stocks)

    payload = {
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
        "signals": project_signals(signals),
        "chains": projected_chains,
        "screens": project_screens(screens, market),
        "stocks": projected_stocks,
        # What this build carried, as numbers the page prints. Always everything since
        # 2026-09-04; the note stays so the page can say so in its own words.
        "carried": {"stocks": stock_note, "chains": chain_note},
        "market": project_market(market),
        "shadow": {
            "book": json.loads((DATA / "shadow" / "book.json").read_text()) if (DATA / "shadow" / "book.json").exists() else {"rows": []},
            "results": json.loads((DATA / "shadow" / "results.json").read_text()) if (DATA / "shadow" / "results.json").exists() else {},
        },
        "trades": trades,
        "requests": project_requests(requests),
        "health": {
            "sessions": json.loads((DATA / "health" / "sessions.json").read_text()) if (DATA / "health" / "sessions.json").exists() else {},
            "actions": json.loads((DATA / "health" / "actions.json").read_text()) if (DATA / "health" / "actions.json").exists() else {},
            # The orchestrator's resume point, derived from disk by
            # tools/campaign_board.py --write. Absent until that has run: null here means
            # "no board on disk", never "no work outstanding", and the Campaign view says
            # so rather than drawing an empty worklist.
            "board": json.loads((DATA / "health" / "board.json").read_text()) if (DATA / "health" / "board.json").exists() else None,
        },
        "ledger": ledger_lines,
        "digests": digests,
        "indicators": json.loads((DATA / "indicators.json").read_text()) if (DATA / "indicators.json").exists() else {"trips": []},
        "calendar": json.loads((DATA / "calendar" / "events.json").read_text()) if (DATA / "calendar" / "events.json").exists() else {"events": []},
        "feeds": feeds_store,
        "candidates": project_candidates(
            json.loads((DATA / "radar" / "candidates.json").read_text())
            if (DATA / "radar" / "candidates.json").exists() else {"candidates": []}),
        "impact": project_impact(impact),
        "rank": json.loads((DATA / "impact" / "_rank-log.json").read_text()) if (DATA / "impact" / "_rank-log.json").exists() else None,
        "scout": json.loads((DATA / "radar" / "scout-log.json").read_text()) if (DATA / "radar" / "scout-log.json").exists() else None,
        "map": json.loads((DATA / "chains" / "_map-log.json").read_text()) if (DATA / "chains" / "_map-log.json").exists() else None,
        "campaign_ix": campaign_ix,
        "board": build_board(DATA, chains, stocks, campaign_ix),
        # The eight agent contracts, verbatim, plus the ownership map parsed out of the
        # command table. Ron drives eight agents and until now could not read what any of
        # them was told: the only agent-shaped text on the page was two section labels.
        # Inlining them makes `--check` refuse a page whose displayed instructions have
        # drifted from `.claude/agents/`, which is the property that makes them worth
        # showing. A contract body is DATA here, exactly like feed text: it instructs the
        # agent that runs under it, never the process that renders it.
        "agentix": agents_payload(ROOT),
        "themes": themes_store,
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
            # Same reason again: the surge rule lives in tools/theme_calibrate.py and is
            # shipped here, so the page can say what "surging" means without owning a
            # second copy of the threshold that decides it.
            "themes": {"surge_min_count": SURGE_MIN_COUNT, "surge_multiple": SURGE_MULTIPLE,
                       "baseline_weeks": BASELINE_WEEKS},
            # What this one file carries, shipped so the page can say it in its own words.
            # Since 2026-09-04: everything, at the fidelity the files hold.
            "page": {"fidelity": "FULL",
                     "series_detail_rule": "every ticker, full daily series",
                     "impact_detail_rule": "every appraisal, legs and evidence whole",
                     "history_rows": "all", "ledger_lines": "all",
                     "not_carried": ["market fundamentals/insider/prints/legs blocks "
                                     "(no template renders them)",
                                     "raw EDGAR filing text (data/edgar/docs, no page)"]},
        },
    }

    return payload



def page_byte_limit() -> int:
    """Bytes the build may produce: the platform cap less a margin for republish framing."""
    return int(PAGE_MAX_MB * 1_000_000) - PAGE_REFUSE_MARGIN_BYTES


def main() -> int:
    check = "--check" in sys.argv

    r = subprocess.run([sys.executable, str(ROOT / "tools" / "validate.py")])
    if r.returncode != 0:
        print("build: refused — validation failed (fix data/, then rebuild)")
        return 1

    try:
        payload = build_payload()
        html = assemble_html(payload)
        guide_html = assemble_guide_html()
    except (ValueError, PayloadTooLarge) as exc:
        print(f"build: refused — {exc}")
        return 1

    size = len(html.encode())
    size_mb = size / 1e6
    # The one ceiling, and it refuses rather than trims: the artifact platform rejects a
    # page over 16 MB, so a build that crossed it would publish nothing. The refusal names
    # the heaviest stores, which is the next action (a store that has outgrown one file).
    if size > page_byte_limit():
        biggest = sorted(store_sizes(payload).items(), key=lambda kv: -kv[1])[:5]
        print(f"build: refused — index.html would be {size:,} bytes, over the artifact "
              f"platform's {PAGE_MAX_MB} MB cap less a {PAGE_REFUSE_MARGIN_BYTES:,}-byte "
              "margin. Heaviest stores: " +
              ", ".join(f"{k} {v:,}" for k, v in biggest))
        return 1

    if check:
        drift = compare_committed(payload)
        # The standalone guide is deterministic, so staleness is an exact comparison.
        if not GUIDE_PAGE.exists():
            drift.append("guide: app/guide.html has not been built")
        elif GUIDE_PAGE.read_text() != guide_html:
            drift.append("guide: app/guide.html does not match app/templates/guide.html")
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
    GUIDE_PAGE.write_text(guide_html)
    print(f"build: wrote app/guide.html ({len(guide_html.encode()) / 1e3:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
