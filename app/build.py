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
OCCURRENCE_ROWS_INLINED = 150
# Price points carried per ticker that has a dive. data/market/<T>.json holds ~550 rows
# (1d over 24 months, 1w before that); the chart is 940px wide and downsamples to 420 of
# them anyway, so 180 evenly spaced points draw the same line. What must never happen is
# the page presenting a downsample as the whole series: every projected series carries
# `row_count` (what the file holds), `inlined_rows` (what the page got) and `sampling`,
# and app.js prints all three under the chart. See tools/check_render.py.
MARKET_SERIES_POINTS = 60  # 60 points across a 940px chart is one point every 14px
# changelog/notes rows carried per object. Both are append-only, so an object's history
# grows without bound while the page only ever shows the tail. Same rule as the ledger's
# 60 lines and the occurrence log's 900 rows: carry the tail, print the denominator.
HISTORY_ROWS_INLINED = 10
LEDGER_LINES_INLINED = 25
FEED_ITEMS_INLINED = 120
# What each store may weigh inside the one file, measured on the projected payload.
# These are NOT refusals: a store over its share prints a loud WARNING naming the knob,
# and the build still produces a page. The repo's own rule for turning a warning into a
# gate is two failures, not one (tasks/lessons.md, .claude/agents/adam-gm.md), and a
# builder that refuses to build is worse than a page that is 40 KB heavy for a day.
# The one exception is `stocks`, which is enforced, because dives are the one store that
# provably cannot fit at campaign scale: see project_stocks.
# Sized from a measured ten-theme campaign (tools/tests/test_page_scale.py), so a store
# over its share is an anomaly worth a line, not the ordinary state of a full campaign.
# They do not partition the page: SIZE_WARN_MB is the binding number, and these only say
# which store to look at when it fires.
# `chains` and `stocks` are the two ELASTIC stores: _elastic_page_budgets gives them
# whatever the rest of the page leaves under SIZE_WARN_MB, so their real knob is that
# function and their share here is only a backstop for "this store ate the page in a way
# the elastic rule did not intend". Their shares are therefore the most the elastic rule
# can hand them on a page this size, not a measured campaign figure: on today's small
# store chains legitimately take 680 KB, and at the ten-theme scale they take 419 KB
# because nine of the ten have been reduced to navigation.
STORE_SHARE_BYTES = {
    "chains": 720_000,
    "screens": 240_000,
    "signals": 220_000,
    "stocks": 260_000,
    "market": 200_000,
    "impact": 160_000,
    "agentix": 140_000,
    "themes": 110_000,
    "candidates": 110_000,
    "ledger": 60_000,
    "campaign_ix": CAMPAIGN_PROJECTION_MAX_BYTES,
    "board": 64_000,
}
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


BOARD_VERDICT_ORDER = {"INVESTABLE": 0, "WATCH": 1, "TOO_LATE": 2}
BOARD_HEAT_ORDER = {"UNDISCOVERED": 0, "EMERGING": 1, "QUIET": 2, "CROWDED": 3,
                    "OVER_CROWDED": 4}
BOARD_O2_CAP = 30
BOARD_BLOCKED_CAP = 40
BOARD_TEXT_CHARS = 140


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
                for t in (stock.get("watch_triggers") or [])[:2] if isinstance(t, dict)
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
                        "on": _compact_text(gaps[0], BOARD_TEXT_CHARS) if gaps else None})
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
            "refusing": _compact_text(blockers[0], BOARD_TEXT_CHARS) if blockers else None,
        })

    return {
        "verdicts": verdicts,
        "o1_queue": o1_queue,
        "o2": o2[:BOARD_O2_CAP],
        "o2_total": len(o2),
        "blocked": blocked[:BOARD_BLOCKED_CAP],
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
    """`changelog` and `notes` windowed to their tail, with the full count beside them.

    Both are append-only. The page renders the timeline and the note list in full, so an
    object worked on for a year carries a year of history into every reader's browser to
    show the last few entries. Carry the tail and print the denominator, exactly as the
    ledger (60 lines) and the occurrence log (900 rows) already do.
    """
    out = {}
    for key in ("changelog", "notes"):
        rows = doc.get(key)
        if not isinstance(rows, list):
            continue
        out[key] = rows[-HISTORY_ROWS_INLINED:]
        out[key + "_total"] = len(rows)
    return out


def _downsample(rows: list, limit: int):
    """Evenly spaced subset of `rows` keeping the first and last row.

    Index-spaced, like the chart's own x-axis, so the shape of the line is preserved:
    dropping every other point cannot move a peak, only thin it. Returns
    (points, sampling) where sampling is "COMPLETE" or "EVEN".
    """
    n = len(rows)
    if n <= limit or limit < 2:
        return list(rows), "COMPLETE"
    keep = sorted({round(i * (n - 1) / (limit - 1)) for i in range(limit)})
    return [rows[i] for i in keep], "EVEN"


def project_market(market: dict, detail_tickers=(), quality_tickers=None) -> dict:
    """Market files trimmed to what app.js reads, at the fidelity each surface needs.

    app.js touches exactly four things in a market file: presence (a dot on the screen
    table, a filled cortex node), `price_status` and `series.as_of` (the company drawer),
    `pcs.axis_a.machine_admissible` (one cortex register), and — only inside stockView —
    `series.rows` and `quality`. Everything else in the file (`fundamentals`, `insider`,
    `prints`, `legs`, `week52`, `cik`, `fetched_at`, `tier`) reaches no template at all;
    it was 92 KB of the page on 2026-08-30 and rendered nowhere.

    So: every ticker gets the header. Only a ticker with a dive gets the series and the
    quality block, because only stockView draws them, and that series is downsampled to
    MARKET_SERIES_POINTS with `row_count`/`inlined_rows`/`sampling` alongside so the page
    can state what it is showing. A downsample presented as a whole series would be the
    check_render defect class in data form.
    """
    def norm(values):
        return {str(t).strip().upper() for t in (values or ()) if str(t or "").strip()}

    detail = norm(detail_tickers)
    quality = detail if quality_tickers is None else norm(quality_tickers)
    out = {}
    for key in sorted(market):
        doc = market[key] or {}
        ticker = _ticker(doc.get("ticker")) or str(key).replace("-", ".").upper()
        names = {ticker, str(key).upper()}
        wants_series = bool(names & detail)
        wants_quality = bool(names & quality)
        row = {"ticker": doc.get("ticker"), "price_status": doc.get("price_status")}

        pcs = doc.get("pcs")
        if isinstance(pcs, dict) and isinstance(pcs.get("axis_a"), dict):
            row["pcs"] = {"axis_a": {
                "machine_admissible": pcs["axis_a"].get("machine_admissible")}}

        series = doc.get("series")
        if isinstance(series, dict):
            rows = series.get("rows") if isinstance(series.get("rows"), list) else []
            head = {"as_of": series.get("as_of"), "row_count": len(rows)}
            if wants_series and rows:
                # Only a chart needs the provenance line; a header-only ticker never draws
                # one, and 400 copies of the same interval string is 40 KB of nothing.
                head["source"] = series.get("source")
                head["interval"] = series.get("interval")
                if series.get("currency") is not None:
                    head["currency"] = series["currency"]
                kept, sampling = _downsample(rows, MARKET_SERIES_POINTS)
                head["rows"] = kept
                head["inlined_rows"] = len(kept)
                head["sampling"] = sampling
            else:
                head["inlined_rows"] = 0
                head["sampling"] = "HEADER_ONLY"
            row["series"] = head

        if wants_quality and isinstance(doc.get("quality"), dict):
            row["quality"] = doc["quality"]
        out[key] = row
    return out


def project_impact(appraisals: list, detail_ids=()) -> list:
    """Impact appraisals trimmed to the two surfaces that render them.

    The chip (signal row, cortex hover) reads `impact_band`, `impact_score`, the money
    band and the three leg scores. The card (signal page) additionally reads each leg's
    `rationale` or `basis`. Nothing renders the `evidence` arrays — which is where the
    verbatim `source_excerpt` spans method §1 requires now live, 259 KB of the 2026-08-30
    page — nor `notes`, `changelog`, `confidence_audit`, `ticker_refs` or `review_by`.

    The excerpts exist so a machine can cross-check a claim offline against the file, and
    data/impact/<id>.json stays canonical for that. The page carries the leg's evidence
    COUNT instead, and app.js prints it beside the rationale with the file path, so the
    reader can see there are sources and where they are rather than being left to assume
    the rationale is unsourced.

    `detail_ids` are the occurrence ids a signal card exists for; only those get the
    rationales, because impactCard is only ever reached from a signal page. An appraisal
    of a candidate is carried at chip fidelity and flagged `legs_inlined: false`, and
    app.js says so rather than drawing an empty card.
    """
    detail = {str(i) for i in detail_ids if i}
    legs = ("money_at_stake", "public_reach", "capture_odds", "timing_fit")
    out = []
    for doc in appraisals:
        if not isinstance(doc, dict):
            continue
        full = str(doc.get("occurrence_id")) in detail
        row = {
            "id": doc.get("id"),
            "occurrence_id": doc.get("occurrence_id"),
            "impact_band": doc.get("impact_band"),
            "impact_score": doc.get("impact_score"),
            "as_of": doc.get("as_of"),
            "legs_inlined": full,
        }
        if doc.get("unranked_reason") is not None:
            row["unranked_reason"] = doc["unranked_reason"]
        for name in legs:
            block = doc.get(name)
            if not isinstance(block, dict):
                continue
            leg = {}
            if "score" in block:
                leg["score"] = block.get("score")
            if "band" in block:
                leg["band"] = block.get("band")
            evidence = block.get("evidence")
            leg["evidence_count"] = len(evidence) if isinstance(evidence, list) else 0
            if full:
                if block.get("rationale") is not None:
                    leg["rationale"] = block["rationale"]
                if block.get("basis") is not None:
                    leg["basis"] = block["basis"]
            row[name] = leg
        out.append(row)
    return out


# --- chains: the second elastic store ---------------------------------------------
#
# A finished chain is ~200 KB on disk and ~140 KB after the trims below, and essentially
# all of that renders: three heat legs per link each with a rationale and cited evidence,
# a repricing check, five scenarios with narratives, moved links, leading indicators and
# invalidation signs. Ten of them is 1.4 MB, which is 70% of the whole file's budget on
# its own, so chains get the same treatment dives already had: whole chains up to a stated
# budget, then reduced ones, and the page SAYS which and names the file.
#
# The order is the campaign manifest's theme rank (data/campaigns/CAMP-*.json), not
# alphabetical and not by size: that ranking is Nell's evidence-backed selection order, it
# already exists, and using it means the highest-impact themes keep their analysis longest.
# A chain the campaign never ranked sorts after every ranked one, by id.
CHAIN_FULL = "FULL"
CHAIN_SUMMARY = "SUMMARY"
CHAIN_INDEX = "INDEX"
# Exactly the three fields `linkModal`'s ev() draws. `url` and `source_date` were carried
# on the theory that they make a citation checkable, but the modal never prints either, so
# a reader could not check anything with them; they were 77 KB of invisible page. The
# chain file stays canonical and the modal names it.
HEAT_EVIDENCE_FIELDS = ("tag", "claim", "source_name")
HEAT_EVIDENCE_INLINED = 3
HEAT_LEGS = ("impact", "crowdedness", "capture")


def _heat_leg(leg: dict, keep_evidence: bool, keep_rationale: bool) -> dict:
    """One heat leg at a stated fidelity, always carrying its denominators.

    `evidence_total` and `excerpts_held` are written at every fidelity, including the ones
    that carry no evidence rows at all, because a leg whose sources vanished silently
    reads as an unsourced assertion. app.js prints them and names data/chains/<slug>.json.
    """
    items = leg.get("evidence")
    out = {k: v for k, v in leg.items() if k not in ("evidence", "rationale")}
    if keep_rationale and leg.get("rationale") is not None:
        out["rationale"] = leg["rationale"]
    if isinstance(items, list):
        out["evidence"] = [
            {k: v for k, v in item.items() if k in HEAT_EVIDENCE_FIELDS}
            if isinstance(item, dict) else item
            for item in (items[:HEAT_EVIDENCE_INLINED] if keep_evidence else [])
        ]
        out["evidence_total"] = len(items)
        out["excerpts_held"] = sum(1 for item in items
                                   if isinstance(item, dict) and item.get("source_excerpt"))
    return out


def _project_link(link: dict, fidelity: str) -> dict:
    """One chain link at one of three fidelities.

    Dropped at EVERY fidelity, because no template reads them and they were 110 KB of the
    2026-08-30 page: `evidence` (the map citation bar tools/check_chain.py enforces, 46 KB),
    `capture_inputs` (66 KB), `heat.repricing_check.legs` and its per-leg `basis` prose
    (45 KB), and `source_excerpt` on every heat evidence item (77 KB). Each leaves a count
    behind and app.js prints all of them beside the file that holds the text.

    SUMMARY additionally drops the heat evidence ROWS (keeping their counts) and the
    bottleneck note. INDEX additionally drops the three heat rationales, which is the
    link's written analysis: everything the flow strip, the heat scatter, the cortex and
    the modal header draw survives, and the modal says the reasoning is in the file.
    """
    full = fidelity == CHAIN_FULL
    row = {k: v for k, v in link.items()
           if k not in ("evidence", "capture_inputs", "heat", "bottleneck")}

    evidence = link.get("evidence")
    if isinstance(evidence, list):
        row["evidence_count"] = len(evidence)
    inputs = link.get("capture_inputs")
    if isinstance(inputs, dict):
        row["capture_inputs_count"] = len(inputs)
    elif isinstance(inputs, list):
        row["capture_inputs_count"] = len(inputs)

    bottleneck = link.get("bottleneck")
    if isinstance(bottleneck, dict):
        keep = dict(bottleneck) if full else {
            k: v for k, v in bottleneck.items() if k not in ("note", "basis")}
        if not full and (bottleneck.get("note") or bottleneck.get("basis")):
            keep["note_held"] = True
        row["bottleneck"] = keep
    elif bottleneck is not None:
        row["bottleneck"] = bottleneck

    heat = link.get("heat")
    if isinstance(heat, dict):
        trimmed = {k: v for k, v in heat.items()
                   if k not in HEAT_LEGS and k != "repricing_check"}
        for leg in HEAT_LEGS:
            block = heat.get(leg)
            if isinstance(block, dict):
                trimmed[leg] = _heat_leg(block, keep_evidence=full,
                                         keep_rationale=fidelity != CHAIN_INDEX)
            elif leg in heat:
                trimmed[leg] = block
        check = heat.get("repricing_check")
        if isinstance(check, dict):
            legs = check.get("legs")
            slim = {k: v for k, v in check.items()
                    if k not in ("legs", "list", "method")}
            if isinstance(legs, list):
                slim["legs_detail_held"] = len(legs)
            trimmed["repricing_check"] = slim
        elif "repricing_check" in heat:
            trimmed["repricing_check"] = check
        row["heat"] = trimmed
    elif heat is not None:
        row["heat"] = heat
    return row


def _project_scenario(scenario: dict, fidelity: str) -> dict:
    """One scenario at one of three fidelities.

    `evidence` is dropped at every fidelity (no template reads it) and its count carried.
    SUMMARY and INDEX drop the per-moved-link `why` and each indicator's `check_basis`,
    both of which are prose that reaches no template today. The narrative, the moved
    links, the indicators with their armed/tripped state and the invalidation signs
    survive at every fidelity: scenTab, the cortex scenario drawer and the activity feed
    all read them, and a scenario without them is a title and a percentage.
    """
    full = fidelity == CHAIN_FULL
    row = {k: v for k, v in scenario.items()
           if k not in ("evidence", "links_moved", "leading_indicators")}
    evidence = scenario.get("evidence")
    if isinstance(evidence, list):
        row["evidence_count"] = len(evidence)

    moved = scenario.get("links_moved")
    if isinstance(moved, list):
        row["links_moved"] = [
            (dict(m) if full else {k: v for k, v in m.items() if k != "why"})
            if isinstance(m, dict) else m for m in moved]
        if not full:
            row["why_held"] = sum(1 for m in moved
                                  if isinstance(m, dict) and m.get("why"))
    elif "links_moved" in scenario:
        row["links_moved"] = moved

    indicators = scenario.get("leading_indicators")
    if isinstance(indicators, list):
        row["leading_indicators"] = [
            (dict(i) if full else {k: v for k, v in i.items() if k != "check_basis"})
            if isinstance(i, dict) else i for i in indicators]
    elif "leading_indicators" in scenario:
        row["leading_indicators"] = indicators
    return row


def _project_chain(chain: dict, fidelity: str) -> dict:
    """One chain at one of three fidelities, always stamped with which one it is."""
    doc = {k: v for k, v in chain.items() if k not in ("links", "scenarios", "notes")}
    doc.update(_history(chain))
    if fidelity == CHAIN_INDEX:
        # The notes are the chain's hand-written margin, ~7 KB a chain. At index fidelity
        # they go; `notes_total` stays so app.js prints "N note(s), not on this page"
        # rather than the "None — add one" empty state, which would read as never written.
        doc.pop("notes", None)
    links = chain.get("links")
    if isinstance(links, list):
        doc["links"] = [_project_link(l, fidelity) if isinstance(l, dict) else l
                        for l in links]
    elif "links" in chain:
        doc["links"] = links
    scenarios = chain.get("scenarios")
    if isinstance(scenarios, list):
        doc["scenarios"] = [_project_scenario(s, fidelity) if isinstance(s, dict) else s
                            for s in scenarios]
    elif "scenarios" in chain:
        doc["scenarios"] = scenarios
    doc["chain_fidelity"] = fidelity
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


def project_chains(chains: list, full_budget=None, summary_budget=0, order=()) -> tuple:
    """Three fidelities in campaign theme-rank order, bounded by two byte budgets.

    Whole chains while `full_budget` lasts, then chains with their heat rationales and
    scenario prose but no evidence rows while `summary_budget` lasts, then the navigation
    row: links, positions, edges, verdicts, scores, scenarios and indicators, with every
    denominator attached and no written analysis.

    Returns (rows, note). The note is what app.js prints on the chain page and in the
    cortex, so a reader never has to infer from an empty Impact block that a link was
    never scored. Rows come back in chain-id order however the budget fell, exactly as
    project_stocks does: the order decides WHAT is carried, never how the rows come out.

    `full_budget=None` means unbounded, which is what a caller outside build_payload
    (a test, a one-off measurement) almost always wants.
    """
    docs = [c for c in chains if isinstance(c, dict)]
    rank = {cid: i for i, cid in enumerate(order or ())}
    ordered = sorted(docs, key=lambda c: (rank.get(str(c.get("id")), len(rank)),
                                          str(c.get("id") or "")))
    full_spent = summary_spent = 0
    rows, counts = [], {"full": 0, "summary": 0, "index": 0}
    for chain in ordered:
        doc = _project_chain(chain, CHAIN_FULL)
        size = len(json.dumps(doc, separators=(",", ":")).encode())
        if full_budget is None or full_spent + size <= full_budget:
            full_spent += size
            counts["full"] += 1
            rows.append(doc)
            continue
        summary = _project_chain(chain, CHAIN_SUMMARY)
        index = _project_chain(chain, CHAIN_INDEX)
        extra = (len(json.dumps(summary, separators=(",", ":")).encode())
                 - len(json.dumps(index, separators=(",", ":")).encode()))
        if summary_spent + extra <= summary_budget:
            summary_spent += extra
            counts["summary"] += 1
            rows.append(summary)
        else:
            counts["index"] += 1
            rows.append(index)
    rows.sort(key=lambda c: str(c.get("id") or ""))
    note = {"carried": counts["full"], "summary": counts["summary"],
            "index_only": counts["index"], "total": len(ordered),
            "budget_bytes": full_budget or 0,
            "summary_budget_bytes": summary_budget,
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


# A finished dive is ~55 KB of dense analytical prose and essentially all of it renders:
# red team, expectations gap, priced-in table, filing quotes, data gaps, valuation lines.
# There is no fat to cut. Sixty of them is 3.3 MB, which is more than the whole one-file
# artifact may weigh, so the honest move is not to trim each dive into unreadability but
# to carry whole dives up to a stated budget and SAY which ones did not fit. The page
# still shows every dive's verdict, clock, tier, entry basis, review date and price chart:
# what an uncarried dive loses is its written case, and it says so and names its file.
# The floor, not the budget. build_payload gives dives whatever the rest of the page
# leaves under SIZE_WARN_MB (see _elastic_stock_budgets); this is the minimum it will
# hand over even when everything else has already eaten the file, so a page can never
# reach zero carried dives without the size warning firing first and naming the store.
STOCK_DETAIL_BUDGET_BYTES = 60_000
# The same floor for chains: one whole chain. A projected campaign-era chain measured
# 133 KB (glp1-fill-finish) and the campaign-scale fixture's is 140 KB, so this is one of
# the biggest and it guarantees that the top-ranked theme is always readable in full.
CHAIN_DETAIL_BUDGET_BYTES = 140_000
# Headroom held back from SIZE_WARN_MB when pricing chain and dive text. It has to be at
# least the sum of every floor spent when there is no room for it — today
# CHAIN_DETAIL_BUDGET_BYTES + STOCK_DETAIL_BUDGET_BYTES — so the worst case a build can
# reach is (SIZE_WARN_MB - this + those floors), and that must still be under SIZE_WARN_MB
# or the guarantee this whole mechanism exists for is not one.
PAGE_SAFETY_MARGIN_BYTES = 200_000
# What every dive keeps at every fidelity: the fields the lists, the cortex, the book, the
# shadow page and the dive hero read. No prose — the bull, bear and surviving-bear-case
# paragraphs are the middle fidelity, in _stock_summary.
STOCK_INDEX_FIELDS = (
    "ticker", "name", "chain_id", "link_id", "issuer_id", "listing_id", "screen_ref",
    "verdict", "clock", "tier", "status", "as_of", "created_at", "updated_at",
    "review_by", "entry_zone", "no_entry_above", "watch_triggers", "shadow_ref",
    "price_ref", "scenario_ids", "events",
)


def _stock_index(stock: dict) -> dict:
    """A dive reduced to what every list, chip and hero on the page reads. No prose."""
    index = {k: stock[k] for k in STOCK_INDEX_FIELDS if k in stock}
    # The hero prints a WATCH dive's triggers as metric/direction/level. Each trigger's
    # `basis` paragraph belongs to the writeup, which this row is not.
    triggers = stock.get("watch_triggers")
    if isinstance(triggers, list):
        index["watch_triggers"] = [
            {k: t.get(k) for k in ("metric", "direction", "level") if k in t}
            for t in triggers if isinstance(t, dict)]
    grade = stock.get("earnings_quality")
    if isinstance(grade, dict) and "grade" in grade:
        index["earnings_quality"] = {"grade": grade["grade"]}
    index["detail_inlined"] = False
    return index


def _stock_summary(stock: dict) -> dict:
    """The index plus the nine lines a reader reads first: three bull, three bear, and
    the paragraph the red team could not kill."""
    row = _stock_index(stock)
    for key in ("bull", "bear"):
        if isinstance(stock.get(key), list):
            row[key] = stock[key]
    red = stock.get("red_team")
    if isinstance(red, dict) and red.get("surviving_bear_case"):
        row["red_team"] = {
            "attacked_at": red.get("attacked_at"),
            "verdict_survived": red.get("verdict_survived"),
            "surviving_bear_case": red.get("surviving_bear_case"),
            "challenge_count": len(red.get("challenges") or []),
        }
    return row


def project_stocks(stocks: list, full_budget=STOCK_DETAIL_BUDGET_BYTES,
                   summary_budget=0) -> tuple:
    """Three fidelities, newest-updated first, bounded by two byte budgets.

    Whole dives while `full_budget` lasts, then bull/bear/surviving-bear summaries while
    `summary_budget` lasts, then the index row. Returns (rows, note); the note is the
    denominator app.js prints, so a reader is never left to infer that a dive with no red
    team on the page was never attacked.

    Both budgets are set by build_payload from what the rest of the page leaves (see
    _elastic_stock_budgets), which is why they are parameters and not constants: today
    every dive is carried whole, and the degradation only begins when the store outgrows
    the file. The order is fixed and the budgets decide only WHAT is carried, never how
    the rows come out — build_campaign_ix holds the same discipline.
    """
    ordered = sorted(
        [s for s in stocks if isinstance(s, dict)],
        key=lambda s: (str(s.get("updated_at") or s.get("as_of") or ""),
                       str(s.get("ticker") or "")),
        reverse=True,
    )
    full_spent = summary_spent = 0
    rows, counts = [], {"full": 0, "summary": 0, "index": 0}
    for stock in ordered:
        doc = dict(stock)
        doc.update(_history(stock))
        size = len(json.dumps(doc, separators=(",", ":")).encode())
        if full_spent + size <= full_budget:
            doc["detail_inlined"] = True
            full_spent += size
            counts["full"] += 1
            rows.append(doc)
            continue
        summary = _stock_summary(stock)
        extra = (len(json.dumps(summary, separators=(",", ":")).encode())
                 - len(json.dumps(_stock_index(stock), separators=(",", ":")).encode()))
        if summary_spent + extra <= summary_budget:
            summary_spent += extra
            counts["summary"] += 1
            rows.append(summary)
        else:
            counts["index"] += 1
            rows.append(_stock_index(stock))
    rows.sort(key=lambda s: (str(s.get("ticker") or ""), str(s.get("chain_id") or "")))
    note = {"carried": counts["full"], "summary": counts["summary"],
            "index_only": counts["index"], "total": len(ordered),
            "budget_bytes": full_budget, "summary_budget_bytes": summary_budget,
            "order": "most recently updated first"}
    return rows, note


def project_requests(requests: dict) -> dict:
    """Only the rows the page can filter to, plus the denominator for the rest.

    app.js reads data/requests.json for exactly two numbers: how many rows are PENDING
    and how many are FAILED. Every FULFILLED row — 116 of 134 on 2026-08-30, 50 KB — was
    inlined so that two counts could be computed over it. Keeping the open rows is
    lossless for every filter the page runs; `settled` carries the rest as a number, and
    the cortex register prints it beside the two counts.
    """
    rows = (requests or {}).get("requests") or []
    keep = ("id", "kind", "ticker", "status", "requested_at", "by", "note")
    open_rows = [{k: r[k] for k in keep if k in r} for r in rows
                 if isinstance(r, dict) and r.get("status") in ("PENDING", "FAILED")]
    return {"requests": open_rows, "total": len(rows),
            "settled": len(rows) - len(open_rows)}


def project_candidates(candidates: dict) -> dict:
    """Ambient candidates trimmed to the cortex fields.

    `campaign_record` is the selection audit `run campaign init` writes onto every
    candidate it considered: 64 KB of the 88 KB candidate store on 2026-08-30, read by no
    template. The campaign dashboard's own denominators come from build_campaign_ix over
    data/campaigns/, never from here, so dropping it changes no number on the page. The
    candidate `changelog` is not rendered either — there is no candidate page, only a
    drawer — so it is carried as a count.
    """
    rows = (candidates or {}).get("candidates") or []
    keep = ("id", "title", "status", "family", "why", "date", "source_name",
            "source_date", "window", "promoted_signal_id")
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        projected = {k: row[k] for k in keep if k in row}
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


def store_sizes(payload: dict) -> dict:
    """Serialized bytes of every top-level key, so the page's weight has a named owner."""
    return {k: len(json.dumps(v, separators=(",", ":")).encode())
            for k, v in payload.items()}


def store_overruns(payload: dict) -> list:
    """(store, bytes, share) for every store over its STORE_SHARE_BYTES allowance."""
    sizes = store_sizes(payload)
    return [(store, sizes[store], share)
            for store, share in sorted(STORE_SHARE_BYTES.items())
            if store in sizes and sizes[store] > share]


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
                               {"t": (it.get("title") or "")[:110], "s": it.get("source"),
                                "f": it.get("family"), "d": it.get("ts")}
                               for it in _items[:FEED_ITEMS_INLINED]]}
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
                "rows": [{"i": r.get("id"), "t": (r.get("title") or "")[:130],
                          "s": r.get("source"), "u": r.get("url"), "d": r.get("ts"),
                          "o": r.get("origin"),
                          "r": r.get("origin_ref") if r.get("origin") != "feed" else None,
                          "f": r.get("family"), "th": r.get("theme_id"),
                          "b": (r.get("theme_basis") or "")[:120],
                          "by": (r.get("theme_by") if not str(r.get("theme_by") or "")
                                 .startswith("rule:") else None)}
                         for r in _rows[:OCCURRENCE_ROWS_INLINED]],
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

    # Fidelity follows the surface. A market series and a quality block are drawn only by
    # stockView, so only a ticker with a dive carries them; an impact card is reached only
    # from a signal page, so only a signal-backed appraisal carries its rationales.
    dive_tickers = {_ticker(s.get("ticker")) for s in stocks
                    if isinstance(s, dict) and s.get("ticker")}
    dive_tickers.discard(None)
    signal_ids = {str(s.get("id")) for s in signals
                  if isinstance(s, dict) and s.get("id")}
    # Pass one: the floor. Every chain and every dive at index fidelity and no quality
    # blocks, which is the smallest honest page this data can make. Pass two below spends
    # whatever SIZE_WARN_MB leaves on chain and dive text — see _elastic_page_budgets.
    chain_order = campaign_chain_order(DATA)
    projected_chains, chain_note = project_chains(chains, 0, 0, chain_order)
    projected_stocks, stock_note = project_stocks(stocks, 0, 0)
    quality_tickers = set()

    payload = {
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
        "signals": project_signals(signals),
        "chains": projected_chains,
        "screens": project_screens(screens, market),
        "stocks": projected_stocks,
        # What this build did NOT carry, as numbers the page prints. A cut the reader
        # cannot see is the same defect as a number the data never had.
        "carried": {"stocks": stock_note, "chains": chain_note},
        "market": project_market(market, dive_tickers, quality_tickers),
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
        "ledger": ledger_lines[-LEDGER_LINES_INLINED:],
        "digests": digests[:4],
        "indicators": json.loads((DATA / "indicators.json").read_text()) if (DATA / "indicators.json").exists() else {"trips": []},
        "calendar": json.loads((DATA / "calendar" / "events.json").read_text()) if (DATA / "calendar" / "events.json").exists() else {"events": []},
        "feeds": feeds_store,
        "candidates": project_candidates(
            json.loads((DATA / "radar" / "candidates.json").read_text())
            if (DATA / "radar" / "candidates.json").exists() else {"candidates": []}),
        "impact": project_impact(impact, signal_ids),
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
                       "baseline_weeks": BASELINE_WEEKS, "rows_inlined": OCCURRENCE_ROWS_INLINED},
            # What this one file is and is not carrying, shipped so the page can say it in
            # its own words instead of a reader assuming a store is empty when it is only
            # unprojected. Every number here is the constant the projection actually used.
            "page": {"series_points": MARKET_SERIES_POINTS,
                     "history_rows": HISTORY_ROWS_INLINED,
                     "ledger_lines": LEDGER_LINES_INLINED,
                     "series_detail_rule": "tickers with a deep dive",
                     "impact_detail_rule": "appraisals of an occurrence that has a signal card"},
        },
    }

    # Pass two. Chains and dives are the two elastic stores: a finished chain is ~140 KB
    # of projected analysis and a finished dive ~60 KB, both of which render essentially
    # in full, and ten chains plus sixty dives is 5 MB against a 2 MB file. Every other
    # store is either bounded by its own nature (eight agent contracts, twenty-five ledger
    # lines) or projected above, so the rest of the page is priced first and these two
    # split the change between them. Today that is both stores at full text; at campaign
    # scale it is as many whole objects as fit, then reduced ones, and `carried` says
    # which, for each store, with the file that holds the rest.
    chain_full, chain_summary, stock_full, stock_summary = _elastic_page_budgets(
        payload, chains, stocks, chain_order)
    projected_chains, chain_note = project_chains(chains, chain_full, chain_summary,
                                                  chain_order)
    projected_stocks, stock_note = project_stocks(stocks, stock_full, stock_summary)
    quality_tickers = {_ticker(s.get("ticker")) for s in projected_stocks
                       if s.get("detail_inlined") and s.get("ticker")}
    quality_tickers.discard(None)
    payload["chains"] = projected_chains
    payload["stocks"] = projected_stocks
    payload["carried"] = {"stocks": stock_note, "chains": chain_note}
    payload["market"] = project_market(market, dive_tickers, quality_tickers)
    return payload


def _page_spare_bytes(floor_payload: dict) -> int:
    """Bytes SIZE_WARN_MB leaves over once the floor page is priced.

    `floor_payload` already carries every chain and every dive at index fidelity, so its
    assembled size is the smallest page this data can make. Everything above that is
    spendable, and analytical text is what it is spent on. Measured against the assembled
    HTML rather than the raw payload because the blob picks up JSON escaping on the way in
    and the shell, CSS, app.js and the vendored d3 modules are real bytes in the same file.
    """
    target = int(SIZE_WARN_MB * 1_000_000) - PAGE_SAFETY_MARGIN_BYTES
    try:
        floor = len(assemble_html(floor_payload).encode())
    except ValueError:
        return 0
    return max(target - floor, 0)


def _split(share: int, need: int, floor: int) -> tuple:
    """(full, summary) inside one store's share of the spare bytes.

    When the share covers everything the store wants at full fidelity, it all goes to full
    fidelity and no summary slice is reserved — reserving one there would push objects
    down a fidelity for no reason. When it does not, 35% is taken for summaries FIRST: a
    page of three complete writeups and fifty-seven verdict-only rows reads worse than one
    that also gives the rest their headline analysis. The remainder goes to whole objects,
    never below the store's one-object floor.
    """
    if share >= need:
        return max(share, floor), 0
    summary = int(share * 0.35)
    return max(share - summary, floor), summary


def _elastic_stock_budgets(floor_payload: dict, share=None, need=None) -> tuple:
    """(full, summary) byte budgets for dive text.

    Never below STOCK_DETAIL_BUDGET_BYTES: if the rest of the page has already eaten the
    file, the honest outcome is a size WARNING naming the store that did it, not a page
    that silently stops carrying the analysis it exists to show.

    Called with no share by anything that wants dives priced on their own — which is what
    it did before chains became elastic too, and what the tests that call it directly
    still expect.
    """
    if share is None:
        share = _page_spare_bytes(floor_payload)
    if need is None:
        need = share + 1  # unknown need: behave as if the store wants more than it has
    return _split(int(share), int(need), STOCK_DETAIL_BUDGET_BYTES)


def _elastic_chain_budgets(floor_payload: dict, share=None, need=None) -> tuple:
    """(full, summary) byte budgets for chain analysis, same contract as dives above.

    Never below CHAIN_DETAIL_BUDGET_BYTES, so the campaign's top-ranked theme is readable
    in full on any page that builds at all.
    """
    if share is None:
        share = _page_spare_bytes(floor_payload)
    if need is None:
        need = share + 1
    return _split(int(share), int(need), CHAIN_DETAIL_BUDGET_BYTES)


def _elastic_page_budgets(floor_payload: dict, chains: list, stocks: list,
                          chain_order=()) -> tuple:
    """(chain_full, chain_summary, stock_full, stock_summary).

    The two elastic stores split the spare bytes in proportion to what each would need at
    full fidelity. Proportional-to-need, rather than a fixed ratio, because the two stores
    are wildly different sizes and that ratio changes as the campaign fills: a repo with
    one dive and eleven chains should not hand dives half the page, and a repo with sixty
    dives and one chain should not hand chains half of it. Both degrade at the same rate,
    which is the property a fixed split cannot give.

    When the spare covers both stores whole, each simply gets its need and nothing is cut.
    """
    spare = _page_spare_bytes(floor_payload)
    chain_need = sum(len(json.dumps(_project_chain(c, CHAIN_FULL),
                                    separators=(",", ":")).encode())
                     for c in chains if isinstance(c, dict))
    stock_need = 0
    for s in stocks:
        if not isinstance(s, dict):
            continue
        doc = dict(s)
        doc.update(_history(s))
        stock_need += len(json.dumps(doc, separators=(",", ":")).encode())
    total_need = chain_need + stock_need
    if total_need <= 0:
        chain_share = stock_share = 0
    elif total_need <= spare:
        chain_share, stock_share = chain_need, stock_need
    else:
        chain_share = int(spare * chain_need / total_need)
        stock_share = spare - chain_share
    chain_full, chain_summary = _elastic_chain_budgets(floor_payload, chain_share,
                                                       chain_need)
    stock_full, stock_summary = _elastic_stock_budgets(floor_payload, stock_share,
                                                       stock_need)
    return chain_full, chain_summary, stock_full, stock_summary


def main() -> int:
    check = "--check" in sys.argv

    r = subprocess.run([sys.executable, str(ROOT / "tools" / "validate.py")])
    if r.returncode != 0:
        print("build: refused — validation failed (fix data/, then rebuild)")
        return 1

    try:
        payload = build_payload()
        html = assemble_html(payload)
    except (ValueError, PayloadTooLarge) as exc:
        print(f"build: refused — {exc}")
        return 1

    size_mb = len(html.encode()) / 1e6
    # Report the denominator, not just the verdict: a size warning that says "the page is
    # big" and not "which store made it big" is a check that names no next action. Every
    # line here points at the constant that moves it.
    for store, over, share in store_overruns(payload):
        print(f"build: WARNING `{store}` is {over:,} bytes of the page, over its "
              f"{share:,}-byte share — the knob is app/build.py's projection for it "
              f"(see STORE_SHARE_BYTES)")
    if size_mb > SIZE_WARN_MB:
        print(f"build: WARNING index.html is {size_mb:.1f} MB (> {SIZE_WARN_MB} MB) — "
              "a store is over its share above, or a new store is unprojected")

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
