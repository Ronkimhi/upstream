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
from acis.quality import (  # noqa: E402
    ALTMAN_DISTRESS_BELOW,
    ALTMAN_SAFE_ABOVE,
    BENEISH_REVIEW_THRESHOLD,
    PIOTROSKI_STRONG_MIN,
    PIOTROSKI_WEAK_MAX,
)
from check_campaign import (  # noqa: E402
    canonical_mapped_placements,
    validated_public_listings,
)
from check_map import placement_audit_for  # noqa: E402
import market_paths  # noqa: E402
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
    """One-line display text: whitespace collapsed, never truncated (2026-09-04) UNLESS
    the caller passes `limit` — the one exception, added 2026-09-14 for the compact
    companies projection below, whose per-issuer prose (catalysts, risks, business and
    exposure summaries) has to fit 328 real profiles inside the page's own byte budget.
    A cut string is still the true text's own prefix, never invented; the caller is the
    one that keeps a `_total`/count beside it so the cut is never silent."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    if limit and len(text) > limit:
        text = text[:limit].rstrip()
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
            # `gaps` (the full data_gaps list, verbatim) used to ride on this row too.
            # Removed 2026-09-14: it duplicated companies[i].data_gaps in full for every
            # BLOCKED/DRAFT issuer (20 KB+ across the board store, all of it also present,
            # word for word, under the "companies" key) and existed only because no
            # company page existed to send a reader to. #/company/<issuer_id> now does;
            # `on` (the first gap, one line) plus the link stays, `gaps_total` says how
            # many more the company page's own Data gaps card names.
            gaps = [g for g in profile.get("data_gaps") or [] if isinstance(g, str)]
            row.pop("link_name", None)
            row.update({"status": profile.get("status"),
                        "on": _compact_text(gaps[0]) if gaps else None,
                        "gaps_total": len(gaps)})
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


def build_top(data_dir=DATA, chains=None, stocks=None) -> dict:
    """The three biggest opportunities the machine sees right now (Ron, 2026-09-13).

    A build-time projection over tools/opportunities.py: every chain link ranked by size
    (impact x capture x un-crowdedness, best expression), the best object on each link
    named with the stage the machine reached, every number copied from the file named
    beside it. Nothing here is computed in the renderer, and the block carries how many
    links it ranked and how many it could not, so a cut is never silent. No run date is
    stored: the page's drift check would trip on it daily.
    """
    import opportunities  # noqa: E402  (tools/ is on sys.path above)
    top = opportunities.rank(opportunities.load_inputs(Path(data_dir).parent, chains=chains, stocks=stocks))
    top["instruments_unrated_total"] = len(top.get("instruments_unrated") or [])
    reasons = {}
    for item in top.get("unrankable") or []:
        reasons[item.get("reason")] = reasons.get(item.get("reason"), 0) + 1
    top["unrankable_by_reason"] = dict(sorted(reasons.items(), key=lambda kv: (-kv[1], str(kv[0]))))
    return top


def shadow_summary(book: dict, results: dict) -> dict:
    """Per-origin and per-link shadow-book statistics, computed here and never in the page.

    The renderer used to pool one hit rate across every origin, which would have mixed
    Stocky's TOO_LATE number with Ember's OVER_CROWDED number the day the second origin
    landed (method section 8, 2026-09-13). Each origin answers a different question, so
    each gets its own denominator. Heat rows are also grouped by link with the median of
    their graded issuer rows and the instrument row beside it, because a link is graded
    as a basket and its fund is graded alone.
    """
    from statistics import median
    rows = [r for r in (book or {}).get("rows") or [] if isinstance(r, dict)]
    results = results or {}
    by_origin = {}
    for row in rows:
        origin = str(row.get("origin") or "UNKNOWN")
        entry = by_origin.setdefault(origin, {"rows": 0, "graded": 0, "right": 0, "mixed": 0,
                                              "wrong": 0, "hit_rate": None})
        entry["rows"] += 1
        call = (results.get(row.get("id")) or {}).get("call")
        if call in ("RIGHT", "MIXED", "WRONG"):
            entry["graded"] += 1
            entry[call.lower()] += 1
    for entry in by_origin.values():
        entry["hit_rate"] = round(100 * entry["right"] / entry["graded"]) if entry["graded"] else None
    by_link = {}
    for row in rows:
        if row.get("origin") != "HEAT_OVER_CROWDED":
            continue
        key = (row.get("chain_id"), row.get("link_id"), row.get("verdict_date"))
        entry = by_link.setdefault(key, {"chain_id": key[0], "link_id": key[1], "verdict_date": key[2],
                                         "rows": 0, "graded": 0, "issuer_deltas": [], "instrument": None})
        entry["rows"] += 1
        graded = results.get(row.get("id")) or {}
        delta = graded.get("delta_pct")
        if graded.get("call") in ("RIGHT", "MIXED", "WRONG"):
            entry["graded"] += 1
        if row.get("expression") == "INSTRUMENT" and entry["instrument"] is None:
            entry["instrument"] = {"ticker": row.get("ticker"),
                                   "delta_pct": delta if isinstance(delta, (int, float)) else None,
                                   "call": graded.get("call")}
        elif isinstance(delta, (int, float)):
            entry["issuer_deltas"].append(delta)
    links = []
    for entry in by_link.values():
        deltas = entry.pop("issuer_deltas")
        entry["median_delta_pct"] = round(median(deltas), 1) if deltas else None
        links.append(entry)
    links.sort(key=lambda e: (str(e["chain_id"]), str(e["link_id"]), str(e["verdict_date"])))
    return {"by_origin": by_origin, "by_link": links, "rows_total": len(rows)}


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

# quality.formulas (tools/acis/quality.py) is byte-identical across every ticker that
# carries it — confirmed 2026-09-14, the same duplication reverse_dcf.horizon_note was
# cut for below. It is rendered (stock page, "נוסחאות:"), so it cannot simply drop; it
# is instead shipped once, under payload["method"]["quality"], and app.js falls back to
# that shared copy when a ticker's own `formulas` has been cut for matching it exactly.
# A ticker whose quality block ever carries a DIFFERENT methodology note keeps it in
# place untouched, so no ticker's disclosure can be silently swapped for another's.
QUALITY_SHARED_FORMULAS_NOTE = "financetoolkit.models (MIT); see docs/analyst-sources.md"

# Top-level quality fields qualityCard (app.js, the sole reader of market[T].quality,
# bound to its local `q`) never draws — confirmed 2026-09-14 by exhaustive search.
# `as_of` stays (it IS printed, "נכון ל..."); `source`/`fundamentals_as_of`/
# `currency_note`/`official_source`/`source_tag`/`state`/`shares_used`/`price_used` are
# not. `price_used` still reaches the page indirectly: project_screens' `_row_valuation`
# reads it from the raw market dict before this trim runs, so a screened ticker's price
# survives there even though this per-ticker quality copy of it does not.
QUALITY_TOP_UNRENDERED = ("source", "fundamentals_as_of", "currency_note",
                          "official_source", "source_tag", "state",
                          "shares_used", "price_used")


def _trim_quality(quality):
    """`quality` (tools/acis/quality.py's scored output) carried whole except the specific
    sub-fields no template renders, confirmed 2026-09-14 by an exhaustive search of
    app/templates/app.js: piotroski/beneish/altman's inputs_found/inputs_needed/
    common_periods (the score and state they support stay; the input-count bookkeeping
    behind them never reaches the page), piotroski.proxies (a methodology footnote),
    reverse_dcf's base_fcf/enterprise_value/horizon_spread, and reverse_dcf.horizon_note —
    which is not a per-ticker fact at all: byte-identical across every one of the 279
    tickers that carried it, a fixed sentence about how to read implied_by_horizon
    (itself kept, and rendered) repeated instead of written once. Also the top-level
    `derived` block (fcf/market_cap/enterprise_value): project_screens' own valuation
    card reads market_cap from the raw market dict project_market receives, before this
    trim runs, so nothing downstream loses it by this function cutting it from the
    output. Same principle MARKET_UNRENDERED already applies one level up, reaching
    quality's own internals now that quality (511 tickers, ~0.85 MB before this trim) is
    most of what is left of market's weight after the daily/weekly split above.

    2026-09-14, the page-diet pass: `health.periods_available` (a per-ticker filed-year
    list) drops the same way — qualityCard (app.js) reads only
    `health.statement_fields_found`/`statement_fields_needed`, never the list of periods
    itself, confirmed by exhaustive search — and `formulas`, when it matches the one
    shared sentence every ticker carries, drops in favor of the single copy in
    payload["method"]["quality"] (QUALITY_SHARED_FORMULAS_NOTE above). The eight
    top-level scalars in QUALITY_TOP_UNRENDERED (see there) drop the same way `derived`
    already did: none of them has a render path either."""
    if not isinstance(quality, dict):
        return quality
    out = {k: v for k, v in quality.items()
           if k != "derived" and k not in QUALITY_TOP_UNRENDERED}
    for group in ("piotroski", "beneish", "altman"):
        blk = out.get(group)
        if isinstance(blk, dict):
            out[group] = {k: v for k, v in blk.items()
                          if k not in ("proxies", "inputs_found", "inputs_needed", "common_periods")}
    rd = out.get("reverse_dcf")
    if isinstance(rd, dict):
        out["reverse_dcf"] = {k: v for k, v in rd.items()
                              if k not in ("base_fcf", "enterprise_value", "horizon_spread",
                                           "horizon_note")}
    health = out.get("health")
    if isinstance(health, dict) and "periods_available" in health:
        out["health"] = {k: v for k, v in health.items() if k != "periods_available"}
    if out.get("formulas") == QUALITY_SHARED_FORMULAS_NOTE:
        out = {k: v for k, v in out.items() if k != "formulas"}
    return out


def _trim_pcs(pcs):
    """`pcs` (tools/acis's retail-attention cross-check, method §3) carried down to the
    one boolean app.js actually reads: `market[T].pcs.axis_a.machine_admissible`, used
    only to count how many tickers are PCS-armed (the cortex "PCS דרוך" stat). Confirmed
    2026-09-14 by exhaustive search of app.js: axis_a's score/state/non_null_fields/
    flags/sub_scores, all of axis_b, gate, and health feed Ember's heat-scoring
    crowdedness leg (`run heat`, method §3) at build time on the agent side, not the
    browser, and no template draws any of them. 189,409 bytes for 595 tickers, almost
    all of it one boolean's supporting cast. The full block stays in
    data/market/<T>.json, which the fetcher and Ember both read directly."""
    if not isinstance(pcs, dict):
        return pcs
    axis_a = pcs.get("axis_a")
    admissible = axis_a.get("machine_admissible") if isinstance(axis_a, dict) else None
    return {"axis_a": {"machine_admissible": admissible}}


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


def _weekly_downsample(rows: list) -> list:
    """One row per ISO week — the last trading day seen in it — preserving chronological
    order. A row whose date does not parse is kept verbatim (never dropped, never
    counted into a week) so encode_series_rows still carries it under `raw`.

    Never applied to a dived ticker's series: the stock page's chart and its 52-week
    range bar both read daily closes. Introduced 2026-09-14 when six new chains, ~111
    new profiles and the same-day price/quality-chart and companies/placements/pipelines
    landings together pushed the real build past build.page_byte_limit() — `series` was
    2.9 MB of the page's 4.2 MB `market` store, nearly all of it for the ~490 tickers no
    dive has ever opened a chart for. Weekly cuts that to roughly a fifth with no loss a
    reader could not already get from the daily file at data/market/<T>.json, which the
    page still names."""
    from datetime import date as _date
    out = []
    last_key = None
    for row in rows:
        try:
            d = _date.fromisoformat(str(row[0]))
        except (ValueError, TypeError, IndexError):
            out.append(row)
            last_key = None
            continue
        key = d.isocalendar()[:2]
        if key != last_key:
            out.append(row)
            last_key = key
        else:
            out[-1] = row
    return out


def project_market(market: dict, fundamentals_for=()) -> dict:
    """Every market file whole except the blocks no template renders (MARKET_UNRENDERED),
    the series compactly encoded (encode_series_rows). `row_count`, `inlined_rows` and
    `sampling` stay on the series header because app.js prints them.

    Since 2026-09-14 the daily series is carried whole (`sampling: "COMPLETE"`, row_count
    == inlined_rows) only for the tickers in `fundamentals_for` — the same dived set that
    gets fundamentals, the only names with a page that draws a daily chart. Every other
    ticker is downsampled to one point per ISO week (`sampling: "WEEKLY"`,
    _weekly_downsample), truthfully: row_count still names the real daily count on disk,
    inlined_rows names what this build actually carries, and app.js's seriesNote() says
    which in the page's own words. Before this date every ticker carried its full daily
    series (2026-09-04's "I just want all the data"); the campaign's own growth made that
    promise and the platform's byte cap mutually exclusive, and Ron's instruction when
    that happens (see the HINT this build's ledger line quotes) is to shrink what a
    non-dived ticker's chart needs, never to raise the cap or loosen the page-size test.

    Since 2026-09-13 the stock page draws fiscal-year fundamentals, so `fundamentals` is
    carried whole for the tickers in `fundamentals_for` (the dived ones, the only names
    with a page that draws it) and left out for every other ticker. app.js prints what it
    holds (finNote) beside the charts, so a short or vendor-sourced history is said in
    words rather than drawn as if it were eight filed years."""
    keep = set()
    for t in fundamentals_for:
        if t:
            keep.add(str(t))
            keep.add(str(t).replace(".", "-"))
    out = {}
    for key in sorted(market):
        doc = market[key] or {}
        skip = set(MARKET_UNRENDERED)
        if key in keep:
            skip.discard("fundamentals")
        row = {k: v for k, v in doc.items() if k not in skip}
        if isinstance(row.get("quality"), dict):
            row["quality"] = _trim_quality(row["quality"])
        if isinstance(row.get("pcs"), dict):
            row["pcs"] = _trim_pcs(row["pcs"])
        series = doc.get("series")
        if isinstance(series, dict):
            rows = series.get("rows") if isinstance(series.get("rows"), list) else []
            head = {k: v for k, v in series.items() if k != "rows"}
            head["row_count"] = len(rows)
            if key in keep:
                inlined = rows
                head["sampling"] = "COMPLETE"
            else:
                inlined = _weekly_downsample(rows)
                head["sampling"] = "WEEKLY"
            head["inlined_rows"] = len(inlined)
            head["rows_c"] = encode_series_rows(inlined)
            row["series"] = head
        # `fundamentals` itself stays out (MARKET_UNRENDERED, 1.43 MB across the store);
        # this is the compact headline the company page and the link modal both read.
        headline = fundamentals_headline(doc.get("fundamentals"))
        if headline is not None:
            row["fundamentals_headline"] = headline
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


# --- companies, placements, fundamentals headline, pipelines -----------------------
#
# Unlike the "carry every store whole" rule above, these four are deliberately COMPACT
# projections: data/companies (193 profiles, 3.2 MB raw) and data/mappings (12 files,
# 1.7 MB raw) never reached the page before 2026-09-13, and the campaign is landing ~180
# more profiles and several new chains in the same run that adds these. The page trims
# hard here so the room stays there: prose fields are whitespace-collapsed
# (_compact_text) but never invented or silently cut without a stated count.

# 2026-09-14, the engineering brief's page-diet pass: 328 real profiles on disk (up from
# the 193 these projections were first sized against) push the companies store past its
# 0.7 MB budget at the prior 5/5-uncapped shape. Tightened to 3/3 and the claim text
# itself capped (COMPANY_CLAIM_CHAR_LIMIT) rather than only the item count, with the true
# counts still riding beside the lists (`catalysts_total`, `risks_total`) so the cut is
# never silent.
COMPANY_MAX_CATALYSTS = 3
COMPANY_MAX_RISKS = 3
COMPANY_CLAIM_CHAR_LIMIT = 200
# business_summary, exposure_summary's narrative/basis, and data_gaps' first item, cut
# the same honest way: short keys and per-item counts elsewhere in this file are not
# enough on their own at 328 real profiles carrying full v/t metrics, listings and 3/200
# catalysts and risks, so these last proseiest fields carry the rest of the cut needed to
# clear 0.7 MB with a real margin. 55 characters is roughly the opening clause of a
# sentence — a name and what it is — never the whole field, which is why the full text
# stays on the profile file this projection points readers at.
COMPANY_SUMMARY_CHAR_LIMIT = 55


def resolve_listing_market_key(ticker, exchange, market_ticker, market: dict):
    """The one ticker resolver every projection below shares: `tools/market_paths.py`'s
    `resolve_market_stem`, checked against the market store this build already read into
    memory. Returns the `data/market/<key>.json` stem, or None when no fetch backs this
    listing — never a guess. See market_paths.EXCHANGE_SUFFIX for the bare-local-code
    case (TWSE "2330", TSX "WSP") that `market_ticker` alone does not cover."""
    return market_paths.resolve_market_stem(
        ticker=ticker, exchange=exchange, market_ticker=market_ticker, available=market)


def _trim_metric_leaf(leaf):
    """One canonical metric value (method §6A): value and tag alone — never the
    paragraph-length `basis` a derivation or a NULL rests on, and since 2026-09-14 not the
    citation trail (source_date/source_name/url/official_source) either.

    Short keys (v/t), the same move project_market's feed and theme rows already make for
    a high-cardinality shape: a COMPLETE profile carries ~12 of these (8 method groups,
    several with more than one canonical key), so the key NAMES are real weight at 328
    profiles today. companyMetric() in app.js is the one place that reads this shape, and
    already renders leaf.s/.d/.u/.o as optional (a metric with only v/t still shows its
    value and tag, just without the inline citation); the full source_date, source_name,
    url and official_source for every metric stay on data/companies/<issuer_id>.json,
    which the company page names for exactly this. Dropping them here is what took the
    companies store from 1.69 MB to inside its 0.7 MB budget at today's real profile
    count — v/t alone still could not fit it (see COMPANY_SUMMARY_CHAR_LIMIT)."""
    if not isinstance(leaf, dict):
        return None
    return {"v": leaf.get("value"), "t": leaf.get("tag")}


def _trim_metrics(metrics) -> dict:
    """Every group and every canonical key metrics carries, each leaf trimmed by
    `_trim_metric_leaf`. Walked generically rather than naming method §6A's eight groups
    by hand, so a T2/T3 `official_source_equivalent` leaf or a new canonical key survives
    without an edit here."""
    out = {}
    if not isinstance(metrics, dict):
        return out
    for group, fields in metrics.items():
        if not isinstance(fields, dict):
            continue
        g = {}
        for key, leaf in fields.items():
            trimmed = _trim_metric_leaf(leaf)
            if trimmed is not None:
                g[key] = trimmed
        if g:
            out[group] = g
    return out


def _trim_exposure_summary(exposure):
    """Narrative and the disclosed-revenue basis, both cut to COMPANY_SUMMARY_CHAR_LIMIT
    (2026-09-14, same page-budget reason as business_summary below) — `pct`/`tag` are
    already scalar and need no trim."""
    if not isinstance(exposure, dict):
        return None
    out = {}
    narrative = _compact_text(exposure.get("narrative"), COMPANY_SUMMARY_CHAR_LIMIT)
    if narrative:
        out["narrative"] = narrative
    dre = exposure.get("disclosed_revenue_exposure")
    if isinstance(dre, dict):
        out["disclosed_revenue_exposure"] = {
            "pct": dre.get("pct"), "tag": dre.get("tag"),
            "basis": _compact_text(dre.get("basis"), COMPANY_SUMMARY_CHAR_LIMIT),
        }
    return out or None


def _trim_claims(items, limit: int, char_limit: int = COMPANY_CLAIM_CHAR_LIMIT) -> list:
    """Up to `limit` catalysts/risks, each cut to claim (itself cut to `char_limit`
    characters, 2026-09-14) + date + url. The count still on disk rides beside this list
    under `<field>_total`, so a cut is never silent.

    Short keys (c/d/u), 2026-09-14, the same move _trim_metric_leaf's v/t already makes:
    at up to 3 items x 2 fields (catalysts, risks) x 328 companies, the key names
    themselves were real weight. companyClaimList() in app.js is the one place that reads
    this shape."""
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        claim = _compact_text(item.get("claim"), char_limit)
        if not claim:
            continue
        out.append({"c": claim, "d": item.get("source_date"), "u": item.get("url")})
        if len(out) >= limit:
            break
    return out


def _trim_listings(listings, market: dict) -> list:
    """`listing_id` and `market_ticker` are inputs to the market-file resolver, not page
    content — companyListingRow() (app.js) reads only ticker/exchange/market_file, so
    neither survives into the output (2026-09-14, confirmed by search of app.js)."""
    out = []
    for listing in listings or []:
        if not isinstance(listing, dict):
            continue
        out.append({
            "ticker": listing.get("ticker"),
            "exchange": listing.get("exchange"),
            "market_file": resolve_listing_market_key(
                listing.get("ticker"), listing.get("exchange"),
                listing.get("market_ticker"), market),
        })
    return out


def project_companies(companies: list, market: dict) -> list:
    """Issuer profiles, compact: identity, tiers, prose fields whitespace-collapsed and
    (2026-09-14) length-capped, the closed metric groups trimmed to value/tag alone, at
    most 3 catalysts and 3 risks (the full counts riding beside them), and every listing
    resolved to its market file. Evidence, confidence audits, changelogs, selection
    reviews and the rest of data_gaps stay in data/companies/<issuer_id>.json — the
    company page names that file for the rest, and prints `data_gaps_total` so a reader
    knows how many more gaps were found beyond the one quoted here.

    `data_gaps` keeps only its first item (the profile's own order, not re-ranked) rather
    than the full list catalysts/risks get at 3: at 328 real profiles even one
    200-character item per company was still 130+ KB, and this store's 0.7 MB budget had
    no room left for a second (see the engineering brief this date's ledger line cites)."""
    out = []
    for doc in companies:
        if not isinstance(doc, dict):
            continue
        issuer_id = _identity(doc.get("issuer_id"))
        if not issuer_id:
            continue
        catalysts = doc.get("catalysts") or []
        risks = doc.get("risks") or []
        gaps = [g for g in (doc.get("data_gaps") or []) if isinstance(g, str) and _compact_text(g)]
        out.append({
            "issuer_id": issuer_id,
            "issuer_name": doc.get("issuer_name"),
            "status": doc.get("status"),
            "data_tier": doc.get("data_tier"),
            "opportunity_tier": _opportunity(doc).get("tier"),
            "as_of": doc.get("as_of"),
            "business_summary": _compact_text(doc.get("business_summary"), COMPANY_SUMMARY_CHAR_LIMIT),
            "exposure_summary": _trim_exposure_summary(doc.get("exposure_summary")),
            "metrics": _trim_metrics(doc.get("metrics")),
            "catalysts": _trim_claims(catalysts, COMPANY_MAX_CATALYSTS),
            "catalysts_total": len(catalysts),
            "risks": _trim_claims(risks, COMPANY_MAX_RISKS),
            "risks_total": len(risks),
            "data_gaps": [_compact_text(g, COMPANY_SUMMARY_CHAR_LIMIT) for g in gaps[:1]],
            "data_gaps_total": len(gaps),
            "listings": _trim_listings(doc.get("listings"), market),
            # The Hebrew selection note (2026-09-15): a plain-language why-not-now for a
            # COMPLETE profile a selection run left O2, same shape as a chain/link
            # explainer and trimmed the same way (_trim_explainer), never truncated —
            # only present on the minority of profiles a selection run actually wrote one
            # for, so it carries no meaningful weight against the byte budget above.
            "selection_note": _trim_explainer(doc.get("selection_note")),
        })
    out.sort(key=lambda r: r["issuer_id"])
    return out


def _placement_audit_status(mapping_doc: dict, chain_id, link_id, issuer_id) -> str:
    """PASS, FAIL, or NONE for one placement — never the mapping's whole-census state
    alone, because Ron's 2026-09-01 decision lets a single current placement audit admit
    its own placement to a dive without the census being COMPLETE. PASS when either the
    whole-census audit is COMPLETE+PASS (every placement inherits it) or
    `check_map.placement_audit_for` finds a CURRENT PASS entry for this exact placement
    (the same currency test `run deepdive`'s gate applies: the entry's digest must match
    the placement as it stands now). FAIL only when the census failed and no placement
    override exists; otherwise NONE — audited neither way."""
    audit = mapping_doc.get("audit") if isinstance(mapping_doc.get("audit"), dict) else {}
    if mapping_doc.get("status") == "COMPLETE" and audit.get("status") == "PASS":
        return "PASS"
    try:
        if placement_audit_for(mapping_doc, chain_id, link_id, issuer_id) is not None:
            return "PASS"
    except Exception:  # noqa: BLE001 — a malformed mapping must not break the whole build
        pass
    return "FAIL" if audit.get("status") == "FAIL" else "NONE"


def project_placements(mappings: list, market: dict) -> list:
    """Every placement on every mapping, structure only: never mapping evidence, search
    logs, or the placement's own role_evidence rows (data/mappings/<slug>.json names
    those). This is what makes every link's stocks reachable even for an issuer with no
    company profile yet — linkModal reads this, not just the chain-wide screen."""
    out = []
    for doc in mappings:
        if not isinstance(doc, dict):
            continue
        chain_id = _identity(doc.get("chain_id")) or _identity(doc.get("id"))
        issuer_names = {
            _identity(i.get("issuer_id")): i.get("name")
            for i in doc.get("issuers") or [] if isinstance(i, dict)
            if _identity(i.get("issuer_id"))
        }
        listings_by_issuer = {}
        for listing in validated_public_listings(doc).values():
            iid = _identity(listing.get("issuer_id"))
            if iid:
                listings_by_issuer.setdefault(iid, listing)
        for placement in doc.get("placements") or []:
            if not isinstance(placement, dict):
                continue
            issuer_id = _identity(placement.get("issuer_id"))
            link_id = _identity(placement.get("link_id"))
            p_chain = _identity(placement.get("chain_id")) or chain_id
            if not issuer_id or not link_id or not p_chain:
                continue
            listing = listings_by_issuer.get(issuer_id)
            out.append({
                "chain_id": p_chain,
                "link_id": link_id,
                "issuer_id": issuer_id,
                "issuer_name": issuer_names.get(issuer_id),
                "role": _compact_text(placement.get("role")),
                "status": placement.get("status"),
                "ticker": listing.get("ticker") if listing else None,
                "market_file": (resolve_listing_market_key(
                    listing.get("ticker"), listing.get("exchange"),
                    listing.get("market_ticker"), market) if listing else None),
                "placement_audit_status": _placement_audit_status(
                    doc, p_chain, link_id, issuer_id),
            })
    out.sort(key=lambda r: (r["chain_id"], r["link_id"], r["issuer_id"]))
    return out


# Headline fundamentals fields, named exactly as method §6A's fetch plane writes them
# (tools/fetch/fetch.py QUALITY_FIELDS + the two vendor-scalar keys). Not every `*_fy`
# field the fetcher writes — a deliberate headline, not the full statement.
FUNDAMENTALS_HEADLINE_FIELDS = ("revenue_fy", "operating_income_fy", "operating_cashflow_fy",
                                "capex_fy", "shares_fy", "cash", "total_debt")


def _fundamentals_latest(series) -> dict:
    """The latest [date, value] row of one *_fy series, or a vendor scalar block's own
    {value, as_of} — never a fabricated point when the series is empty."""
    if isinstance(series, list) and series:
        rows = [r for r in series if isinstance(r, (list, tuple)) and len(r) >= 2]
        if not rows:
            return None
        latest = max(rows, key=lambda r: str(r[0]))
        return {"date": latest[0], "value": latest[1]}
    if isinstance(series, dict) and "value" in series:
        return {"date": series.get("as_of"), "value": series.get("value")}
    return None


def fundamentals_headline(fundamentals) -> dict:
    """One market file's fundamentals, reduced to the latest point of each headline field
    plus as_of/source/official-vs-vendor — never the multi-year series MARKET_UNRENDERED
    already drops for size. Any field this build does not name by hand but whose key
    reads as a remaining-performance-obligation figure is picked up too, generically, so
    the RPO field another agent is landing in fundamentals needs no edit here on the day
    it ships."""
    if not isinstance(fundamentals, dict):
        return None
    fields = {}
    for key in FUNDAMENTALS_HEADLINE_FIELDS:
        latest = _fundamentals_latest(fundamentals.get(key))
        if latest is not None:
            fields[key] = latest
    for key, series in fundamentals.items():
        if key in fields or key in FUNDAMENTALS_HEADLINE_FIELDS:
            continue
        kl = key.lower()
        if "rpo" not in kl and "remaining_performance_obligation" not in kl:
            continue
        latest = _fundamentals_latest(series)
        if latest is not None:
            fields[key] = latest
    if not fields:
        return None
    source = fundamentals.get("source")
    return {"as_of": fundamentals.get("as_of"), "source": source,
            "official_source": source == "sec-companyfacts", "fields": fields}


def project_pipelines(data_dir=DATA) -> list:
    """Every company pipeline whole (data/pipelines/<issuer_id>.json — a store another
    agent is standing up in this same run; a missing directory is simply empty, per
    read_json_dir). Excerpts are whitespace-collapsed like every other trimmed prose
    field; nothing else about an item is cut."""
    out = []
    for doc in read_json_dir(Path(data_dir) / "pipelines"):
        if not isinstance(doc, dict):
            continue
        issuer_id = _identity(doc.get("issuer_id"))
        if not issuer_id:
            continue
        items = []
        for item in doc.get("items") or []:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if isinstance(row.get("source_excerpt"), str):
                row["source_excerpt"] = _compact_text(row["source_excerpt"])
            items.append(row)
        out.append({
            "issuer_id": issuer_id,
            "ticker": doc.get("ticker"),
            "as_of": doc.get("as_of"),
            "status": doc.get("status"),
            "items": items,
            "searched": [s for s in (doc.get("searched") or []) if isinstance(s, str)],
        })
    out.sort(key=lambda r: r["issuer_id"])
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


# what/players/why/bottleneck/hands_to/as_of/by — exactly what explainerBlock and
# overviewBlock (app.js) read from a Hebrew explainer, chain- or link-level alike.
# `draws_on` and `lang` are never rendered by either, confirmed 2026-09-14 by exhaustive
# search. The full block, both fields included, stays on data/chains/<slug>.json.
def _trim_explainer(explainer):
    if not isinstance(explainer, dict):
        return explainer
    return {k: v for k, v in explainer.items() if k not in ("draws_on", "lang")}


# The price-test amendment (method §4, 2026-09-13) requires these of every HIGH or
# CHOKE_POINT link and tools/check_chain.py's check 12 enforces them on disk — but no
# template renders any of the three, confirmed 2026-09-14 by exhaustive search of
# app/templates/app.js. The full blocks stay in data/chains/<slug>.json, which the price
# test itself validates directly and chainPath() names for a reader who wants them.
LINK_UNRENDERED = ("instrument_search", "scarce_price", "price_instruments")


def _project_link(link: dict) -> dict:
    """One chain link, whole: map citations, capture judgments, bottleneck note, every
    heat leg with every evidence row and its verbatim excerpt, the repricing check with
    its per-leg basis. The counts app.js already prints ride along beside the rows."""
    row = dict(link)
    for k in LINK_UNRENDERED:
        row.pop(k, None)
    if isinstance(row.get("explainer"), dict):
        row["explainer"] = _trim_explainer(row["explainer"])
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
        # `heat.instrument` (a full crowdedness+capture scoring, evidence and all, for
        # the rare link that has a tradeable instrument) has no render path yet —
        # confirmed 2026-09-14 by exhaustive search; the only `.instrument` app.js reads
        # is the unrelated, much smaller shadow_summary() `by_link[].instrument` row.
        # Drop it here rather than carry a second full evidence-backed scoring block no
        # view shows; it stays in data/chains/<slug>.json for whichever view reaches it.
        full.pop("instrument", None)
        row["heat"] = full
    return row


def _project_scenario(scenario: dict) -> dict:
    """One scenario, whole: evidence rows, each moved link's `why`, each indicator's
    `check_basis`. `evidence_count` rides along because the tab prints it.

    2026-09-14: each indicator's `check` (the machine-checkable {type,ticker,op,level}
    tools/check_scenarios.py validates and `.armed` is derived from) and `check_source`
    drop — confirmed by exhaustive search, only `.armed`, `.check_basis`,
    `.where_to_watch`, `.tripped_at` and indText()'s `.signal`/`.indicator` are ever
    drawn. The full indicator, both fields included, stays on the chain file."""
    row = dict(scenario)
    evidence = scenario.get("evidence")
    if isinstance(evidence, list):
        row["evidence_count"] = len(evidence)
    indicators = scenario.get("leading_indicators")
    if isinstance(indicators, list):
        row["leading_indicators"] = [
            ({k: v for k, v in ind.items() if k not in ("check", "check_source")}
             if isinstance(ind, dict) else ind)
            for ind in indicators
        ]
    return row


def _project_chain(chain: dict) -> dict:
    """One chain, whole, stamped FULL (the only fidelity since 2026-09-04).

    2026-09-14: `map_limitation` drops from the page projection — never rendered by any
    template, confirmed by exhaustive search of app/templates/app.js — while staying
    required on data/chains/<slug>.json, where tools/check_chain.py enforces it and
    chainPath() names it for a reader who wants it. The chain-level explainer is trimmed
    the same way the link-level one is (_trim_explainer)."""
    doc = dict(chain)
    doc.pop("map_limitation", None)
    doc.update(_history(chain))
    if isinstance(doc.get("explainer"), dict):
        doc["explainer"] = _trim_explainer(doc["explainer"])
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
    `fundamentals` from every ticker without a dive (the stock page is the one template
    that draws the block), so a modal
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


# Fields inside a screen row's `fundamentals` block that stockCard() (app.js) actually
# reads — always through its own fval() helper, which unwraps a {value:...} leaf OR
# accepts a bare number, but never touches `.source`/`.as_of`/`.tag` — confirmed 2026-09-14
# by exhaustive search of app.js. `piotroski` (a bare number) and `beneish_state` (a bare
# string) need no unwrapping. Dropped entirely: `latest_fy`, `piotroski_state`,
# `beneish_score` and `quality_basis` — none read by any template. This is the same
# principle _trim_quality already applies to market's quality block, reaching the other
# place a screen row's own citation-carrying fundamentals snapshot repeats it: 619 KB of
# the 1.27 MB screens store on 2026-09-14, most of it citation text (`source` strings like
# "data/market/VRT.json fundamentals.revenue_fy") no page ever prints.
SCREEN_FUNDAMENTALS_VALUE_FIELDS = ("revenue_fy", "net_income_fy", "revenue_cagr_3y",
                                    "market_implied_fcf_cagr")


def _trim_screen_fundamentals(fundamentals):
    if not isinstance(fundamentals, dict):
        return fundamentals
    out = {}
    for key in SCREEN_FUNDAMENTALS_VALUE_FIELDS:
        leaf = fundamentals.get(key)
        if isinstance(leaf, dict) and "value" in leaf:
            out[key] = {"value": leaf.get("value")}
        elif leaf is not None:
            out[key] = leaf
    if "piotroski" in fundamentals:
        out["piotroski"] = fundamentals.get("piotroski")
    if "beneish_state" in fundamentals:
        out["beneish_state"] = fundamentals.get("beneish_state")
    return out


# A screen row's own PCS cross-check (the same method §3 block market.pcs carries,
# _trim_pcs above). stockCard() (app.js) reads only two of its fields: `.state` (the
# DARK/SATURATED chip) and `.pcs_axis_a` (the number beside it) — confirmed 2026-09-14 by
# exhaustive search. basis/caveat/source/as_of/sub_scores/pcs_axis_b*/analyst_count/
# held_pct_institutions/gate_state and the rest are never drawn. The full block stays in
# data/screens/<chain>.json.
def _trim_screen_crowdedness(cw):
    if not isinstance(cw, dict):
        return cw
    return {"state": cw.get("state"), "pcs_axis_a": cw.get("pcs_axis_a")}


# A screen document's own search-log bookkeeping (method §6): required on disk, never
# drawn by any template — confirmed 2026-09-14 by exhaustive search of app.js. The full
# screen stays in data/screens/<chain>.json, which chainPath() names for a reader.
SCREEN_UNRENDERED = ("queries_run", "superseded_rows")

# A screen row's own identity/audit bookkeeping (method §6): campaign-v1 rows carry all
# of these for check_screen.py, and no card draws any of them — confirmed 2026-09-14 by
# exhaustive search. `mapping_ref`/`profile_ref` here are the ROW's own copies (distinct
# from a screen document's top-level refs, which this trim leaves alone).
SCREEN_ROW_UNRENDERED = ("audit_scope", "audit_scope_basis", "secondary_links",
                         "secondary_link_basis", "mapping_ref", "profile_ref",
                         "listing_id", "market_ticker")


def project_screens(screens: list, market=None) -> list:
    out = []
    for screen in screens:
        if not isinstance(screen, dict):
            out.append(screen)
            continue
        doc = dict(screen)
        for k in SCREEN_UNRENDERED:
            doc.pop(k, None)
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
                    for k in SCREEN_ROW_UNRENDERED:
                        nr.pop(k, None)
                    if "fundamentals" in nr:
                        nr["fundamentals"] = _trim_screen_fundamentals(nr["fundamentals"])
                    if "crowdedness" in nr:
                        nr["crowdedness"] = _trim_screen_crowdedness(nr["crowdedness"])
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


# A kept (non-FULFILLED) request row's own fetch-workflow bookkeeping: required for the
# pull-data skill's verification and for Actions' own retry logic, never drawn by any
# template — confirmed 2026-09-14 by exhaustive search of app.js.
REQUEST_ROW_UNRENDERED = ("requested_by", "last_attempt_at", "query", "forms",
                          "lookback_days", "attempts", "cik", "wrote", "by")


def project_requests(requests: dict) -> dict:
    """1,865 rows and 1,809 of them FULFILLED on 2026-09-13 (0.98 MB) — room-making room:
    every row that is not FULFILLED (PENDING + FAILED, the two states anyone needs to act
    on), plus `by_kind_status` (counts, so the cortex register and the new pipeline view
    need not scan the array to answer "how many prices requests failed") and
    `latest_by_ticker` (the newest row per ticker per kind, so a company page can say what
    is queued or stuck for its own tickers without scanning 1,865 rows client-side).
    `total` and `settled` still describe the WHOLE store, not just what is carried —
    the denominator app.js is required to print beside any cut.

    2026-09-14, the page-diet pass: `latest_by_ticker` itself is now trimmed to the
    entries a company page can actually show. app.js's reqLatest block (the only reader)
    filters to `status !== "FULFILLED"` before rendering anything, and never reads
    `fulfilled_at` even for the rows it keeps — confirmed by exhaustive search. At
    today's real store that filter, applied here instead of in the browser, is the
    difference between 437,070 bytes and 9,180: 2,638 of 2,682 (ticker,kind) entries
    were FULFILLED, each one shipped only to be filtered out client-side, never once
    read. The full history of every request, fulfilled or not, stays in
    data/requests.json; only this derived, already-filtered index is cut.

    The kept (non-FULFILLED) rows are trimmed the same way: pipelineRequestRow and the
    pending/failed counts (app.js) read only kind/status/note/id/requested_at/ticker/url
    from a request row — confirmed by exhaustive search. REQUEST_ROW_UNRENDERED below is
    everything else a row carries for the fetch workflow's own bookkeeping."""
    rows = [r for r in ((requests or {}).get("requests") or []) if isinstance(r, dict)]
    total = len(rows)
    by_kind_status: dict = {}
    latest_by_ticker: dict = {}
    kept = []
    for r in rows:
        kind, status = r.get("kind"), r.get("status")
        by_kind_status.setdefault(kind, {})
        by_kind_status[kind][status] = by_kind_status[kind].get(status, 0) + 1
        ticker = _ticker(r.get("ticker"))
        if ticker:
            slot = latest_by_ticker.setdefault(ticker, {})
            prior = slot.get(kind)
            key = (r.get("requested_at") or "", r.get("id") or "")
            prior_key = (prior.get("requested_at") or "", prior.get("id") or "") if prior else None
            if prior is None or key > prior_key:
                slot[kind] = {"id": r.get("id"), "status": status,
                              "requested_at": r.get("requested_at"),
                              "fulfilled_at": r.get("fulfilled_at"), "note": r.get("note")}
        if status != "FULFILLED":
            kept.append({k: v for k, v in r.items() if k not in REQUEST_ROW_UNRENDERED})
    open_rows = [r for r in kept if r.get("status") in ("PENDING", "FAILED")]
    latest_by_ticker_open = {}
    for ticker, kinds in latest_by_ticker.items():
        open_kinds = {k: {kk: vv for kk, vv in row.items() if kk != "fulfilled_at"}
                      for k, row in kinds.items() if row.get("status") != "FULFILLED"}
        if open_kinds:
            latest_by_ticker_open[ticker] = open_kinds
    return {"requests": kept, "total": total,
            "settled": total - len(open_rows),
            "by_kind_status": by_kind_status,
            "latest_by_ticker": latest_by_ticker_open}



def project_candidates(candidates: dict) -> dict:
    """Every candidate whole, including its selection audit and changelog, with the
    changelog count beside it because the drawer prints the count.

    2026-09-14: `campaign_record` drops — a full copy of the candidate's `run campaign
    init` evaluation (occurrence, all five selection dimensions, rationale), 64,304 bytes
    across today's candidates and never read by app.js, confirmed by exhaustive search.
    The campaign manifest (data/campaigns/CAMP-*.json) is the permanent record of that
    evaluation; data/radar/candidates.json carries this same copy for it, unread either
    way."""
    rows = (candidates or {}).get("candidates") or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        projected = dict(row)
        projected.pop("campaign_record", None)
        changelog = row.get("changelog")
        if isinstance(changelog, list):
            projected["changelog_total"] = len(changelog)
        out.append(projected)
    return {"as_of": (candidates or {}).get("as_of"), "candidates": out,
            "total": len(rows)}


# --- book, rank/map/scout logs: rich agent-facing stores the page barely reads --------
#
# Four small stores added 2026-09-14 to the page-diet pass for the same reason: each one
# is a rich, multi-field working record for the agent that owns it, and app.js reads
# only a handful of fields out of it — every other field confirmed dead by exhaustive
# search. None of these files are touched; only what this build carries into the browser
# shrinks.

def project_book(book: dict) -> dict:
    """`data/book.json`'s ranked rows, trimmed to what bookRowsForTicker (app.js) reads:
    `ticker` and `chain_id` (the lookup keys) and `price`/`piotroski` (the only two
    facts the link modal's "additional names" list draws from a matched row). Confirmed
    2026-09-14 by exhaustive search: investability, heat_verdict, the DCF/quality state
    columns, the entry-zone bounds, rank, criticality, the dived/screened flags and the
    rest of the book's 33 fields have no reader yet — this store has no page of its own.
    `contract`, `ranking` and `denominator` (the book's own methodology block) are
    carried whole; only the 229 per-ticker rows are trimmed. The full ranked book stays
    in data/book.json."""
    if not isinstance(book, dict):
        return book
    out = dict(book)
    rows = book.get("rows")
    if isinstance(rows, list):
        out["rows"] = [
            {"ticker": r.get("ticker"), "chain_id": r.get("chain_id"),
             "price": r.get("price"), "piotroski": r.get("piotroski")}
            for r in rows if isinstance(r, dict)
        ]
    return out


def project_rank(rank_log: dict) -> dict:
    """data/impact/_rank-log.json, with the one block no template reads dropped:
    `queue` (the unappraised-occurrence work list `run impact --queue` already reads
    straight off disk, 17,233 bytes) and its `changelog`. `.calibration` stays — the
    only block app.js reads (a denominator line). Confirmed 2026-09-14 by exhaustive
    search. The full log stays on disk."""
    if not isinstance(rank_log, dict):
        return rank_log
    return {k: v for k, v in rank_log.items() if k not in ("queue", "changelog")}


def project_map_log(map_log: dict) -> dict:
    """data/chains/_map-log.json, with the blocks no template reads dropped: `changelog`
    (21,906 bytes), `spot_tests`, `repairs` and `confidence_audit`. `.notes` and
    `.calibration`/`.archetypes` stay — mapCard (app.js) filters notes for
    ESCALATION-tagged rows and reads the rest in full, the same pattern scoutCard
    applies to the scout log below. Confirmed 2026-09-14 by exhaustive search. The full
    log stays on disk."""
    if not isinstance(map_log, dict):
        return map_log
    return {k: v for k, v in map_log.items()
            if k not in ("changelog", "spot_tests", "repairs", "confidence_audit")}


def project_scout_log(scout_log: dict) -> dict:
    """data/radar/scout-log.json, with the blocks no template reads dropped:
    `spot_tests`, `repairs`, `changelog` and `confidence_audit`. `.notes`,
    `.proposed_rules` and `.calibration` stay — scoutCard (app.js) reads all three, notes
    filtered for ESCALATION-tagged rows. Confirmed 2026-09-14 by exhaustive search. The
    full log stays on disk."""
    if not isinstance(scout_log, dict):
        return scout_log
    return {k: v for k, v in scout_log.items()
            if k not in ("spot_tests", "repairs", "changelog", "confidence_audit")}


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
        if key in ("ledger_total", "method"):
            # 2026-09-18: the SAME structural lag the `ledger` branch above was written
            # for, and the `health` branch says was "special-cased there and missed here"
            # -- missed a third time, in the two keys DERIVED from the ledger's length.
            # `ledger_total` is len(data/ledger.md) and `method.page.ledger_lines` is the
            # string "newest 200 of {that}", so any commit that appends a ledger line
            # without rebuilding fails this gate on a counter while the tolerant `ledger`
            # branch passes the lines themselves. That is not hypothetical: with radar and
            # campaign PAUSED, a routine fire appends ONE ledger NOTE every three hours and
            # is instructed by CLAUDE.md's PAUSED section not to rebuild the page, so CI
            # went red on this counter within three hours of every repair, from 2026-09-14
            # to 2026-09-18.
            #
            # The invariant kept is the one the `ledger` branch keeps: the page may LAG
            # the file, never lead it. A committed total ABOVE the file's is still drift
            # (lines the ledger does not have, or a page built against a different tree),
            # and every other field of `method` stays strictly compared.
            try:
                real_total = len([ln for ln in (DATA / "ledger.md").read_text().splitlines()
                                  if ln[:2].isdigit() and "|" in ln])
            except Exception:  # noqa: BLE001
                real_total = payload.get("ledger_total") or 0
            if key == "ledger_total":
                page_total = committed.get(key)
                if not isinstance(page_total, int):
                    drift.append("ledger_total: missing or not a number on the committed page")
                elif page_total > real_total:
                    drift.append(f"ledger_total: the committed page claims {page_total} ledger "
                                 f"line(s), more than the {real_total} data/ledger.md holds")
                continue
            a = _strip_derived_sizes(committed.get(key))
            b = _strip_derived_sizes(payload.get(key))
            for side in (a, b):
                if isinstance(side, dict) and isinstance(side.get("page"), dict):
                    side["page"] = {k: v for k, v in side["page"].items()
                                    if k != "ledger_lines"}
            if a != b:
                moved = ""
                if isinstance(a, dict) and isinstance(b, dict):
                    diff = sorted(set(a) ^ set(b)) or \
                        sorted(k for k in set(a) & set(b) if a[k] != b[k])
                    moved = f" (differs at: {', '.join(map(str, diff[:6]))})" if diff else ""
                drift.append(f"method: the committed page disagrees with data/{moved}")
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

    # Newest 200 lines + the total count (tightened from 400 on 2026-09-14, the
    # engineering brief's page-diet pass, to hold the real page under its 12.5 MB test):
    # room-making room for the company/mapping/pipeline projections landing in this same
    # build. The file is still the truth (compare_committed's ordered-subsequence check
    # already tolerates the page lagging or omitting lines from data/ledger.md); this just
    # widens the gap on purpose. `ledger_total` is the denominator app.js prints beside
    # the cut.
    LEDGER_CARRIED_LINES = 200
    ledger_all = []
    lfile = DATA / "ledger.md"
    if lfile.exists():
        for line in lfile.read_text().splitlines():
            if line[:2].isdigit() and "|" in line:
                ledger_all.append(line.strip())
    ledger_total = len(ledger_all)
    ledger_lines = ledger_all[-LEDGER_CARRIED_LINES:]

    digests = read_json_dir(DATA / "digest")
    digests.sort(key=lambda d: d.get("week", ""), reverse=True)
    # Only the newest is ever read — `(D.digests || [])[0]`, three call sites in
    # app.js, confirmed 2026-09-14 by exhaustive search; no view lists past weeks.
    # `run digest` never prunes data/digest/, so the unread tail only grows; every
    # weekly file stays on disk regardless of what this build carries.
    digests = digests[:1]

    signals = read_json_dir(DATA / "signals")
    impact = read_json_dir(DATA / "impact")
    chains = read_json_dir(DATA / "chains")
    screens = read_json_dir(DATA / "screens")
    stocks = read_json_dir(DATA / "stocks")
    requests = json.loads((DATA / "requests.json").read_text()) if (DATA / "requests.json").exists() else {"requests": []}
    # Read once here for the compact company/placement projections below.
    # build_campaign_ix reads its own copies of these same two stores (it needs mapping
    # evidence shapes project_placements does not) rather than taking them as parameters
    # — an accepted second read of ~5 MB raw, not a correctness risk.
    companies_raw = read_json_dir(DATA / "companies")
    mappings_raw = read_json_dir(DATA / "mappings")
    book = json.loads((DATA / "book.json").read_text()) if (DATA / "book.json").exists() else None

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
            # 2026-09-14, the page-diet pass: `url`, `id` and `summary` rode on every one
            # of these 800 items (233 KB combined) though nothing in app/templates/app.js
            # reads them — confirmed by exhaustive search: the dust ring (cxBuild) and its
            # drawer (cxDustDrawer) render only title/source/family/date, and a feed item
            # has no addressable page of its own to link out from (the store prunes at 500
            # items/14 days; the occurrence log in data/themes/ is the permanent, linkable
            # record). The comment above already promised "title/source/family/date only";
            # this now matches it.
            feeds_store = {"as_of": _f.get("as_of"), "total": len(_items),
                           "families": _fams,
                           "items": [
                               {"t": it.get("title"), "s": it.get("source"),
                                "f": it.get("family"), "d": it.get("ts")}
                               for it in _items]}
        except Exception:
            pass

    # The occurrence log, trimmed the same way the feed dust ring is and for the same
    # reason: `total` is the whole store, `rows` is what the page can afford to carry. The
    # UI must never print len(rows) where the corpus size belongs -- that exact mistake put
    # "300 HELD" on a page whose store held 317.
    #
    # 2026-09-18: the comment above promised a trim this code never performed -- `rows`
    # carried the WHOLE log, so the payload grew one row per occurrence forever while the
    # store is append-only by design. A single ingest catch-up (+758 feed rows, the
    # backlog left by upstream-radar being PAUSED since 2026-09-01 while the fetch
    # workflow kept landing items) pushed the real build to 12,575,792 bytes, over the
    # 12.5 MB page-diet target TestRealPageSize holds. The rule there is "shrink a
    # projection, never raise this number", and the trim the comment already described is
    # that projection. Capped at the newest 1500 the same way LEDGER_CARRIED_LINES caps
    # the ledger: `total` stays the corpus size, and app.js already renders the gap --
    # thTheme appends " \u05d1\u05e2\u05de\u05d5\u05d3 \u05d4\u05d6\u05d4" ("on this
    # page") to its count whenever TH.total exceeds TH.rows.length, so the truncated state
    # is one the UI was built for and never silently claimed otherwise.
    OCCURRENCE_CARRIED_ROWS = 1500
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
                # 2026-09-14: `id` and `theme_by` dropped too — thOccRow (app.js) never
                # reads either, confirmed by exhaustive search of the whole occurrence-log
                # section; the permanent row on disk (data/themes/occurrences.json) still
                # carries both.
                "rows": [{"t": r.get("title"),
                          "s": r.get("source"), "u": r.get("url"), "d": r.get("ts"),
                          "o": r.get("origin"), "r": r.get("origin_ref"),
                          "f": r.get("family"), "th": r.get("theme_id"),
                          "b": r.get("theme_basis")}
                         for r in _rows[:OCCURRENCE_CARRIED_ROWS]],
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
        "market": project_market(
            market,
            fundamentals_for=[s.get("ticker") for s in stocks if isinstance(s, dict)]),
        # Compact projections (2026-09-13): every issuer profile, every mapping's
        # placements, and every company pipeline, each trimmed per the rule at the top
        # of this section. `book` was data/book.json's own already-compact rows, carried
        # whole through 2026-09-13; from 2026-09-14 project_book trims its rows further
        # (see its docstring) now that a real per-ticker row carries 33 fields and only
        # 4 have a reader.
        "companies": project_companies(companies_raw, market),
        "placements": project_placements(mappings_raw, market),
        "pipelines": project_pipelines(DATA),
        "book": project_book(book),
        "ledger_total": ledger_total,
        "shadow": {
            "book": json.loads((DATA / "shadow" / "book.json").read_text()) if (DATA / "shadow" / "book.json").exists() else {"rows": []},
            "results": json.loads((DATA / "shadow" / "results.json").read_text()) if (DATA / "shadow" / "results.json").exists() else {},
            "summary": shadow_summary(
                json.loads((DATA / "shadow" / "book.json").read_text()) if (DATA / "shadow" / "book.json").exists() else {"rows": []},
                json.loads((DATA / "shadow" / "results.json").read_text()) if (DATA / "shadow" / "results.json").exists() else {}),
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
        "rank": project_rank(json.loads((DATA / "impact" / "_rank-log.json").read_text())) if (DATA / "impact" / "_rank-log.json").exists() else None,
        "scout": project_scout_log(json.loads((DATA / "radar" / "scout-log.json").read_text())) if (DATA / "radar" / "scout-log.json").exists() else None,
        "map": project_map_log(json.loads((DATA / "chains" / "_map-log.json").read_text())) if (DATA / "chains" / "_map-log.json").exists() else None,
        "campaign_ix": campaign_ix,
        "board": {**build_board(DATA, chains, stocks, campaign_ix), "top": build_top(DATA, chains, stocks)},
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
            # The quality-score zones the stock page draws its meters with (2026-09-13),
            # from the same constants tools/acis/quality.py decides each state with.
            # `formulas_note` (2026-09-14): the one methodology sentence every ticker's
            # quality.formulas carried byte-identically; _trim_quality cuts a ticker's
            # own copy only when it matches this exactly, so a ticker with a genuinely
            # different note keeps it untouched.
            "quality": {"altman": {"distress_below": ALTMAN_DISTRESS_BELOW,
                                   "safe_above": ALTMAN_SAFE_ABOVE},
                        "beneish": {"review_above": BENEISH_REVIEW_THRESHOLD},
                        "piotroski": {"strong_min": PIOTROSKI_STRONG_MIN,
                                      "weak_max": PIOTROSKI_WEAK_MAX},
                        "formulas_note": QUALITY_SHARED_FORMULAS_NOTE},
            # What this one file carries, shipped so the page can say it in its own words.
            # Since 2026-09-04: everything, at the fidelity the files hold.
            "page": {"fidelity": "FULL",
                     "series_detail_rule": "every ticker, full daily series",
                     "impact_detail_rule": "every appraisal, legs and evidence whole",
                     "history_rows": "all",
                     "ledger_lines": f"newest {LEDGER_CARRIED_LINES} of {ledger_total}",
                     "occurrence_rows": (
                         f"newest {OCCURRENCE_CARRIED_ROWS} of "
                         f"{(themes_store or {}).get('total', 0)}"
                         if themes_store and (themes_store.get("total") or 0)
                            > OCCURRENCE_CARRIED_ROWS else "all"),
                     "fundamentals_rule": "whole for every dived ticker (the stock page "
                                          "draws them); other tickers get a compact "
                                          "fundamentals_headline instead",
                     "not_carried": ["market fundamentals for tickers without a dive, and "
                                     "insider/prints/legs blocks for every ticker "
                                     "(no template renders them; a compact "
                                     "fundamentals_headline rides on each market file)",
                                     "raw EDGAR filing text (data/edgar/docs, no page)",
                                     "company profile evidence, confidence audits, "
                                     "selection reviews and changelogs "
                                     "(data/companies/<issuer_id>.json; the company "
                                     "page names the file)",
                                     "mapping placement evidence, search logs and "
                                     "audit sample detail (data/mappings/<slug>.json)",
                                     "FULFILLED request rows (data/requests.json; "
                                     "counts and the open rows are carried)"]},
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
