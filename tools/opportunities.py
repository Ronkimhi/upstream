#!/usr/bin/env python3
"""`run` nothing: the homepage's Top 3, computed once and copied everywhere else.

Method section 8 (2026-09-13) gives every chain link a size: impact times capture times
un-crowdedness, on the best-scored EXPRESSION of that link (the issuer basket, or a
listed price instrument such as a futures ETF when the link has one). This module ranks
every link on disk by that size and names the best object the machine can point Ron at
for it right now: a closed verdict when one exists, otherwise the furthest stage the
funnel has actually reached (a drafted dive, a selected O1, a screen row, a mapped
placement, or just an example ticker).

What this is NOT. Nothing here fetches, invents, or solves anything. Every number in a
row is COPIED from a file named beside it in `refs` or in `best`: the chain's own heat
block, an impact appraisal, a stock file's own zone, a market file's own last close and
its own reverse-DCF solve. `tools/check_opportunities.py` recomputes this module's own
`rank()` from disk and refuses a row whose number disagrees with its source, so a stale
or hand-edited copy on the page is machine-detectable, not just reviewable.

A row with no dive is a candidate for one, never a recommendation: `best.stage` says
plainly how far the funnel got (VERDICT, DIVE_DRAFT, O1_QUEUED, SCREENED, MAPPED, LEAD,
or FUND for a scored price instrument), and `lines` renders that state in the two-sentence
shape Ron reads, built through one number formatter so the wording can never say a
number the row does not itself carry.

Run: python3 tools/opportunities.py [--root PATH]
Writes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from heat_score import band_for, link_size, score_from  # noqa: E402

VERDICT_RANK = {"INVESTABLE": 0, "WATCH": 1, "TOO_LATE": 2}


# -- disk loaders (read-only; a missing directory is empty, never an error) -----------


def _read_json(path) -> dict | None:
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001 - a broken file is skipped, never fatal here
        return None


def _read_json_dir(folder) -> list:
    folder = Path(folder)
    out = []
    if not folder.exists():
        return out
    for path in sorted(folder.glob("*.json")):
        if path.name.startswith("_"):
            continue
        doc = _read_json(path)
        if isinstance(doc, dict):
            out.append(doc)
    return out


def _market_index(market_dir) -> dict:
    """ticker (both the dashed filename stem and the file's own dotted ticker) -> Path."""
    market_dir = Path(market_dir)
    index: dict = {}
    if not market_dir.exists():
        return index
    for path in sorted(market_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        index[path.stem.upper()] = path
        doc = _read_json(path)
        if isinstance(doc, dict):
            ticker = doc.get("ticker")
            if isinstance(ticker, str) and ticker:
                index[ticker.upper()] = path
    return index


def _newest_digest(digest_dir) -> dict | None:
    docs = [d for d in _read_json_dir(digest_dir) if d.get("week")]
    if not docs:
        return None
    return max(docs, key=lambda d: str(d.get("week")))


def _current_campaign_id(campaigns_dir):
    ids = [d.get("id") for d in _read_json_dir(campaigns_dir) if d.get("id")]
    return max(ids) if ids else None


def load_inputs(root, chains=None, stocks=None) -> dict:
    """Everything `rank()` needs, read once. `chains`/`stocks` accept the same list
    shape `app/build.py`'s own `read_json_dir` already produces, so a caller that has
    already loaded them for the rest of the page need not read disk twice."""
    root = Path(root)
    data = root / "data"
    chain_list = chains if chains is not None else _read_json_dir(data / "chains")
    stock_list = stocks if stocks is not None else _read_json_dir(data / "stocks")
    chain_map = {c["id"]: c for c in chain_list if isinstance(c, dict) and c.get("id")}
    stock_list = [s for s in stock_list if isinstance(s, dict)]

    impact_map = {}
    for occ in _read_json_dir(data / "impact"):
        occ_id = occ.get("occurrence_id")
        if occ_id:
            impact_map[occ_id] = occ

    mapping_map = {}
    for mapping in _read_json_dir(data / "mappings"):
        chain_id = mapping.get("chain_id")
        if chain_id:
            mapping_map[chain_id] = mapping

    return {
        "root": root,
        "chains": chain_map,
        "stocks": stock_list,
        "companies": _read_json_dir(data / "companies"),
        "screens": _read_json_dir(data / "screens"),
        "impact": impact_map,
        "mappings": mapping_map,
        "market_index": _market_index(data / "market"),
        "digest": _newest_digest(data / "digest"),
        "campaign_id": _current_campaign_id(data / "campaigns"),
    }


# -- number formatting: the one path any digit reaches a line through -----------------


def fmt_num(value):
    """Integers without decimals, floats with at most one."""
    if value is None or isinstance(value, bool):
        return "n/a"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        rounded = round(value, 1)
        if rounded == int(rounded):
            return str(int(rounded))
        return f"{rounded:.1f}"
    return str(value)


def fmt_money(value):
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"${value:,.2f}"


# -- expressions: the two ways a link can be owned -------------------------------------


def expressions(link, listed=False) -> list:
    """The scoreable ways to express this link: the issuer basket, always; a listed
    price instrument, only when the link names one. Each carries the three heat scores
    behind its own size and never a defaulted one: `heat_score.link_size` returns None
    on any None input, so a NULL score means the expression cannot rank, it is not
    silently dropped to zero."""
    if not isinstance(link, dict):
        return []
    heat = link.get("heat")
    heat = heat if isinstance(heat, dict) else {}

    impact = score_from(heat, "impact")
    issuer_crowd = score_from(heat, "crowdedness")
    issuer_capture = score_from(heat, "capture")
    out = []
    # An UNINVESTABLE link (method section 4: the order book you cannot buy, ARCH-003) has
    # no issuer expression to own, so its size would be an opportunity nobody can take.
    # It can still rank through a price instrument (ARCH-008), which is the whole point,
    # or when the census, a screen or a dive found a listed name on it anyway (`listed`):
    # the label is Atlas's prior, the placement is evidence.
    if link.get("investability") != "UNINVESTABLE" or listed:
        out.append({
            "expression": "ISSUERS",
            "impact": impact,
            "crowdedness": issuer_crowd,
            "capture": issuer_capture,
            "size": link_size(impact, issuer_crowd, issuer_capture),
            "verdict": heat.get("verdict"),
            "unrated": False,
        })

    instruments = link.get("price_instruments")
    if isinstance(instruments, list) and instruments:
        inst = heat.get("instrument")
        inst = inst if isinstance(inst, dict) else None
        inst_crowd = score_from(inst, "crowdedness") if inst else None
        inst_capture = score_from(inst, "capture") if inst else None
        inst_verdict = inst.get("verdict") if inst else None
        if inst is not None and inst_crowd is not None and inst_capture is not None \
                and inst_verdict is not None:
            out.append({
                "expression": "INSTRUMENT",
                "impact": impact,
                "crowdedness": inst_crowd,
                "capture": inst_capture,
                "size": link_size(impact, inst_crowd, inst_capture),
                "verdict": inst_verdict,
                "unrated": False,
            })
        else:
            out.append({
                "expression": "INSTRUMENT",
                "impact": impact,
                "crowdedness": None,
                "capture": None,
                "size": None,
                "verdict": None,
                "unrated": True,
            })
    return out


def _unrankable_reason(link) -> str:
    heat = link.get("heat") if isinstance(link, dict) else None
    if not isinstance(heat, dict):
        return "heat block missing"
    impact = score_from(heat, "impact")
    crowd = score_from(heat, "crowdedness")
    capture = score_from(heat, "capture")
    if impact is None and crowd is None and capture is None:
        return "NULL heat"
    if link.get("investability") == "UNINVESTABLE" and not link.get("price_instruments"):
        return "UNINVESTABLE: no listed name, no instrument"
    return "no expression scored"


# -- market lookups: a ticker's own last price and its own reverse-DCF solve ----------


def _market_doc(inputs, ticker):
    if not ticker:
        return None, None
    path = inputs["market_index"].get(str(ticker).upper())
    if not path:
        return None, None
    return _read_json(path), path


def _price_info(inputs, ticker):
    doc, path = _market_doc(inputs, ticker)
    if not isinstance(doc, dict):
        return None
    rows = ((doc.get("series") or {}).get("rows")) or []
    if not rows or not isinstance(rows[-1], list) or len(rows[-1]) < 2:
        return None
    value = rows[-1][1]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return {"value": value, "as_of": (doc.get("series") or {}).get("as_of"),
            "ref": f"data/market/{path.name}"}


def _implied_fcf_cagr(inputs, ticker):
    doc, path = _market_doc(inputs, ticker)
    if not isinstance(doc, dict):
        return None, None
    ref = f"data/market/{path.name}"
    value = ((doc.get("quality") or {}).get("reverse_dcf") or {}).get("implied_fcf_cagr")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value, ref
    return None, ref


def _zone_state(price_value, low, high):
    if not isinstance(price_value, (int, float)) or isinstance(price_value, bool):
        return None, None
    if not isinstance(low, (int, float)) or isinstance(low, bool):
        return None, None
    if not isinstance(high, (int, float)) or isinstance(high, bool):
        return None, None
    if low <= price_value <= high:
        return "IN_ZONE", None
    if price_value > high:
        return "ABOVE", round((price_value / high - 1) * 100, 1)
    return "BELOW", None


def _sorted_by_cagr_then_ticker(items, cagr_of, ticker_of):
    """`items` sorted ascending by implied FCF CAGR, a missing solve sorting last, ties
    broken by ticker. The one ordering rule the SCREENED and MAPPED tiers both use."""
    return sorted(items, key=lambda it: (cagr_of(it) is None, cagr_of(it) or 0.0,
                                          ticker_of(it) or ""))


# -- the ISSUERS resolution ladder: VERDICT > DIVE_DRAFT > O1_QUEUED > SCREENED > -----
# -- MAPPED > LEAD ---------------------------------------------------------------------


def _dives_for_link(inputs, chain_id, link_id):
    return [d for d in inputs["stocks"]
            if d.get("chain_id") == chain_id and d.get("link_id") == link_id]


def _dive_updated(dive):
    return str(dive.get("updated_at") or dive.get("as_of") or "")


def _best_verdict_dive(dives):
    finals = [d for d in dives if d.get("status") == "FINAL" and d.get("verdict") in VERDICT_RANK]
    if not finals:
        return None
    best_rank = min(VERDICT_RANK[d.get("verdict")] for d in finals)
    tied = [d for d in finals if VERDICT_RANK[d.get("verdict")] == best_rank]
    tied.sort(key=lambda d: str(d.get("ticker") or ""))
    tied.sort(key=_dive_updated, reverse=True)
    return tied[0]


def _best_draft_dive(dives):
    drafts = [d for d in dives if d.get("status") and d.get("status") != "FINAL"]
    if not drafts:
        return None
    drafts.sort(key=lambda d: str(d.get("ticker") or ""))
    drafts.sort(key=_dive_updated, reverse=True)
    return drafts[0]


def _object_from_dive(dive, inputs, stage):
    ticker = dive.get("ticker")
    verdict = dive.get("verdict")
    price = _price_info(inputs, ticker)
    cagr, cagr_ref = _implied_fcf_cagr(inputs, ticker)

    zone, zone_state, pct_above = None, None, None
    zone_block, zone_kind = None, None
    if verdict == "INVESTABLE":
        zone_block, zone_kind = dive.get("entry_zone"), "entry"
    elif verdict == "WATCH":
        zone_block, zone_kind = dive.get("would_buy_zone"), "would_buy"
    if isinstance(zone_block, dict):
        low, high = zone_block.get("low"), zone_block.get("high")
        if isinstance(low, (int, float)) and not isinstance(low, bool) \
                and isinstance(high, (int, float)) and not isinstance(high, bool):
            zone = {"kind": zone_kind, "low": low, "high": high}
            zone_state, pct_above = _zone_state(price.get("value") if price else None, low, high)

    return {
        "stage": stage, "ticker": ticker, "name": dive.get("name"),
        "ref": f"data/stocks/{ticker}__{dive.get('chain_id')}.json",
        "verdict": verdict, "clock": dive.get("clock"), "status": dive.get("status"),
        "zone": zone, "price": price, "zone_state": zone_state,
        "pct_above_zone_high": pct_above,
        "implied_fcf_cagr": cagr, "implied_ref": cagr_ref,
    }


def _profile_ticker(profile, listing_id):
    for listing in profile.get("listings") or []:
        if isinstance(listing, dict) and listing_id and listing.get("listing_id") == listing_id:
            return listing.get("market_ticker") or listing.get("ticker")
    listings = profile.get("listings") or []
    if listings and isinstance(listings[0], dict):
        return listings[0].get("market_ticker") or listings[0].get("ticker")
    return None


def _best_o1_profile(inputs, chain_id, link_id):
    candidates = []
    for profile in inputs["companies"]:
        if profile.get("status") != "COMPLETE" or profile.get("opportunity_tier") != "O1":
            continue
        handoff = (profile.get("selection_basis") or {}).get("screen_handoff") or {}
        if handoff.get("chain_id") == chain_id and handoff.get("link_id") == link_id:
            candidates.append(profile)
    if not candidates:
        return None
    candidates.sort(key=lambda p: str(p.get("issuer_id") or ""))
    return candidates[0]


def _screen_ref(chain_id, screen):
    scenario_id = screen.get("scenario_id")
    if scenario_id:
        return f"data/screens/{chain_id}__{scenario_id}.json"
    return f"data/screens/{chain_id}.json"


def _best_screen_row(inputs, chain_id, link_id):
    """A screen row on this link, from any bucket of any screen file for the chain."""
    rows = []
    for screen in inputs["screens"]:
        if screen.get("chain_id") != chain_id:
            continue
        for bucket_rows in (screen.get("buckets") or {}).values():
            for row in bucket_rows if isinstance(bucket_rows, list) else []:
                if isinstance(row, dict) and row.get("link_id") == link_id:
                    rows.append((row, screen))
    if not rows:
        return None, None, None
    def ticker_of(item):
        return item[0].get("market_ticker") or item[0].get("ticker")
    def cagr_of(item):
        cagr, _ = _implied_fcf_cagr(inputs, ticker_of(item))
        return cagr
    ordered = _sorted_by_cagr_then_ticker(rows, cagr_of, ticker_of)
    row, screen = ordered[0]
    return row, ticker_of((row, screen)), _screen_ref(chain_id, screen)


def _issuer_name(mapping, issuer_id):
    for issuer in mapping.get("issuers") or []:
        if isinstance(issuer, dict) and issuer.get("issuer_id") == issuer_id:
            return issuer.get("name")
    return None


def _mapped_candidates(inputs, chain_id, link_id):
    """(ticker, placement) for every ACTIVE placement on this link with a resolvable
    listing ticker, via the mapping's own listings block (book.py's own pattern)."""
    mapping = inputs["mappings"].get(chain_id)
    if not isinstance(mapping, dict):
        return []
    by_issuer = {}
    for listing in mapping.get("listings") or []:
        if isinstance(listing, dict) and listing.get("issuer_id"):
            ticker = listing.get("market_ticker") or listing.get("ticker")
            if ticker:
                by_issuer.setdefault(listing["issuer_id"], ticker)
    out = []
    for placement in mapping.get("placements") or []:
        if placement.get("link_id") != link_id or placement.get("status") != "ACTIVE":
            continue
        ticker = by_issuer.get(placement.get("issuer_id"))
        if ticker:
            out.append((ticker, placement))
    return out


def _issuers_best(chain, link, inputs) -> dict:
    chain_id, link_id = chain.get("id"), link.get("id")

    verdict_dive = _best_verdict_dive(_dives_for_link(inputs, chain_id, link_id))
    if verdict_dive is not None:
        return _object_from_dive(verdict_dive, inputs, "VERDICT")

    draft_dive = _best_draft_dive(_dives_for_link(inputs, chain_id, link_id))
    if draft_dive is not None:
        return _object_from_dive(draft_dive, inputs, "DIVE_DRAFT")

    profile = _best_o1_profile(inputs, chain_id, link_id)
    if profile is not None:
        handoff = (profile.get("selection_basis") or {}).get("screen_handoff") or {}
        ticker = _profile_ticker(profile, handoff.get("listing_id"))
        cagr, cagr_ref = _implied_fcf_cagr(inputs, ticker)
        return {
            "stage": "O1_QUEUED", "ticker": ticker, "name": profile.get("issuer_name"),
            "ref": f"data/companies/{profile.get('issuer_id')}.json",
            "verdict": None, "clock": chain.get("clock"), "status": profile.get("status"),
            "zone": None, "price": _price_info(inputs, ticker), "zone_state": None,
            "pct_above_zone_high": None, "implied_fcf_cagr": cagr, "implied_ref": cagr_ref,
        }

    row, ticker, screen_ref = _best_screen_row(inputs, chain_id, link_id)
    if row is not None:
        cagr, cagr_ref = _implied_fcf_cagr(inputs, ticker)
        return {
            "stage": "SCREENED", "ticker": ticker, "name": row.get("name"),
            "ref": screen_ref,
            "verdict": None, "clock": chain.get("clock"), "status": row.get("status"),
            "zone": None, "price": _price_info(inputs, ticker), "zone_state": None,
            "pct_above_zone_high": None, "implied_fcf_cagr": cagr, "implied_ref": cagr_ref,
        }

    mapped = _mapped_candidates(inputs, chain_id, link_id)
    if mapped:
        def ticker_of(item):
            return item[0]
        def cagr_of(item):
            cagr, _ = _implied_fcf_cagr(inputs, item[0])
            return cagr
        ticker, placement = _sorted_by_cagr_then_ticker(mapped, cagr_of, ticker_of)[0]
        cagr, cagr_ref = _implied_fcf_cagr(inputs, ticker)
        mapping = inputs["mappings"].get(chain_id) or {}
        return {
            "stage": "MAPPED", "ticker": ticker,
            "name": _issuer_name(mapping, placement.get("issuer_id")),
            "ref": f"data/mappings/{chain_id}.json",
            "verdict": None, "clock": chain.get("clock"), "status": None,
            "zone": None, "price": _price_info(inputs, ticker), "zone_state": None,
            "pct_above_zone_high": None, "implied_fcf_cagr": cagr, "implied_ref": cagr_ref,
            "mapped_count": len(mapped),
        }

    examples = link.get("example_tickers") or []
    lead_ticker = examples[0] if examples else None
    cagr, cagr_ref = (_implied_fcf_cagr(inputs, lead_ticker) if lead_ticker else (None, None))
    return {
        "stage": "LEAD", "ticker": lead_ticker, "name": None,
        "ref": (f"data/chains/{chain_id}.json#links/{link_id}/example_tickers/0"
                if lead_ticker else None),
        "verdict": None, "clock": chain.get("clock"), "status": None,
        "zone": None, "price": (_price_info(inputs, lead_ticker) if lead_ticker else None),
        "zone_state": None, "pct_above_zone_high": None,
        "implied_fcf_cagr": cagr, "implied_ref": cagr_ref,
    }


def best_object(chain, link, expression, inputs) -> dict:
    """The furthest stage the funnel has reached for one expression of one link. For
    ISSUERS this walks the resolution ladder in `_issuers_best`; for INSTRUMENT it names
    the fund itself and carries the issuer-basket object alongside it under `also`, so a
    reader always sees both what the fund is and what owning the stocks instead would mean."""
    issuers = _issuers_best(chain, link, inputs)
    if expression.get("expression") != "INSTRUMENT":
        return issuers

    instruments = link.get("price_instruments") or []
    first = instruments[0] if instruments and isinstance(instruments[0], dict) else {}
    ticker = first.get("ticker")
    rated = not expression.get("unrated")
    cagr, cagr_ref = (_implied_fcf_cagr(inputs, ticker) if ticker else (None, None))
    return {
        "stage": "FUND", "ticker": ticker, "name": first.get("kind"),
        "ref": f"data/chains/{chain.get('id')}.json#links/{link.get('id')}/price_instruments/0",
        "verdict": expression.get("verdict") if rated else None,
        "clock": chain.get("clock"), "status": None,
        "zone": None, "price": (_price_info(inputs, ticker) if ticker else None),
        "zone_state": None, "pct_above_zone_high": None,
        "implied_fcf_cagr": cagr, "implied_ref": cagr_ref,
        "rated": rated, "also": issuers,
    }


# -- lines: the two-sentence bottom line, built from the row's own fields only --------


def _clip_words(text, limit=60):
    if not text:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    return clipped.rsplit(" ", 1)[0] if " " in clipped else clipped


def _spaced(word):
    return str(word).replace("_", " ") if word else "UNRATED"


def _why_line(row):
    impact_s = fmt_num(row.get("impact"))
    capture_s = fmt_num(row.get("capture"))
    crowd_s = fmt_num(row.get("crowdedness"))
    if row["expression"] == "INSTRUMENT":
        issuer_crowd_s = fmt_num(row.get("issuer_crowdedness"))
        return (f"Moves hard ({impact_s} of 100), keeps the profit ({capture_s} of 100), "
                f"few have noticed the fund ({crowd_s} of 100; the stocks score {issuer_crowd_s}).")
    return (f"Moves hard ({impact_s} of 100), keeps the profit ({capture_s} of 100), "
            f"few have noticed ({crowd_s} of 100).")


def _stage_line(row):
    best = row.get("best") or {}
    stage = best.get("stage")
    ticker = best.get("ticker") or "It"

    if stage == "VERDICT":
        verdict = best.get("verdict")
        clock = best.get("clock")
        clock_s = f" ({clock})" if clock else ""
        if verdict == "TOO_LATE":
            return "Verdict TOO_LATE; in the shadow book."
        zone = best.get("zone")
        if not zone:
            if verdict == "WATCH":
                return f"Verdict WATCH{clock_s}; no buy zone drawn yet."
            return f"Verdict {verdict}{clock_s}; no zone drawn yet."
        low_s, high_s = fmt_money(zone.get("low")), fmt_money(zone.get("high"))
        zone_word = "Entry zone" if zone.get("kind") == "entry" else "Would buy at"
        price = best.get("price")
        lead = f"Verdict {verdict}{clock_s}. {zone_word} {low_s} to {high_s}"
        if not price or price.get("value") is None:
            return lead + "."
        price_s, date_s = fmt_money(price["value"]), price.get("as_of") or ""
        lead = f"{lead}; last close {price_s} on {date_s}"
        state = best.get("zone_state")
        if state == "IN_ZONE":
            return lead + (": in the zone now." if verdict == "WATCH" else ": in the zone.")
        if state == "ABOVE":
            pct_s = fmt_num(best.get("pct_above_zone_high"))
            return lead + f": above the zone by {pct_s}%."
        return lead + (": not there yet." if verdict == "WATCH" else ": below the zone.")

    if stage == "DIVE_DRAFT":
        return f"Dive drafted on {ticker}, no red team yet."

    if stage == "O1_QUEUED":
        return f"{ticker} is selected and waiting for a dive."

    if stage == "SCREENED":
        cagr = best.get("implied_fcf_cagr")
        if cagr is None:
            detail = "no valuation solve on file"
        else:
            detail = f"today's price needs {fmt_num(round(cagr * 100, 1))}% yearly cash-flow growth"
        return f"Not dived yet. Best name on the screen: {ticker} ({detail})."

    if stage == "FUND":
        instruments = row.get("instruments") or []
        holds = _clip_words(instruments[0].get("holds") if instruments else None)
        issuer_word = _spaced(row.get("issuer_verdict"))
        lead = (f"A fund holds this price directly: {ticker} ({holds}). "
                f"Stocks on the link are rated {issuer_word}")
        if best.get("rated"):
            return f"{lead}; the fund is rated {_spaced(best.get('verdict'))}."
        return f"{lead}; the fund is not rated yet."

    if stage == "MAPPED":
        count_s = fmt_num(best.get("mapped_count"))
        noun = "listed name" if count_s == "1" else "listed names"
        return f"Census done, nothing screened yet. {count_s} {noun} mapped; first: {ticker}."

    if stage == "LEAD":
        if not best.get("ticker"):
            return "No listed name on this link yet."
        return f"No census yet. Example name: {ticker}."

    return ""


def _next(row, inputs):
    best = row.get("best") or {}
    stage = best.get("stage")
    chain_id = row.get("chain_id")
    ticker = best.get("ticker")

    if stage == "VERDICT":
        return "Open the verdict", None
    if stage == "DIVE_DRAFT":
        cmd = f"run redteam {ticker} {chain_id}"
        return cmd, cmd
    if stage == "O1_QUEUED":
        cmd = f"run deepdive {ticker} {chain_id}"
        return cmd, cmd
    if stage == "SCREENED":
        camp = inputs.get("campaign_id")
        cmd = f"run selection {camp}" if camp else "run campaign init"
        return cmd, cmd
    if stage == "FUND":
        if not best.get("rated"):
            has_market = bool(ticker and inputs["market_index"].get(str(ticker).upper()))
            if not has_market:
                cmd = f"request data {ticker}"
                return cmd, cmd
        cmd = f"run heat {chain_id}"
        return cmd, cmd
    if stage == "MAPPED":
        cmd = f"run screen {chain_id}"
        return cmd, cmd
    if stage == "LEAD":
        cmd = f"run universe {chain_id}"
        return cmd, cmd
    return None, None


def _href(row):
    best = row.get("best") or {}
    if best.get("stage") == "VERDICT" and best.get("ticker"):
        return f"#/stock/{best['ticker']}/{row.get('chain_id')}"
    return f"#/chain/{row.get('chain_id')}"


def _adam_note(digest, chain_id, link_id, ticker):
    if not isinstance(digest, dict):
        return None
    for entry in digest.get("ranked") or []:
        ref = entry.get("ref") or {}
        if ref.get("type") == "chain_link" and ref.get("chain_id") == chain_id \
                and ref.get("link_id") == link_id:
            return {"week": digest.get("week"), "title": entry.get("title")}
        if ref.get("type") == "ticker" and ticker and ref.get("ticker") == ticker:
            return {"week": digest.get("week"), "title": entry.get("title")}
    return None


# -- rank: every link on disk, ranked or accounted for as unrankable -------------------


def _build_row(chain, link, expr, inputs) -> dict:
    chain_id, link_id = chain.get("id"), link.get("id")
    heat = link.get("heat") if isinstance(link.get("heat"), dict) else {}

    appraisal = inputs["impact"].get(chain.get("signal_id"))
    appraisal = appraisal if isinstance(appraisal, dict) else None
    impact_ref = (f"data/impact/{appraisal['id']}.json"
                  if appraisal and appraisal.get("id") else None)

    best = best_object(chain, link, expr, inputs)
    also = best.pop("also", None) if isinstance(best, dict) else None

    instruments = []
    for inst in link.get("price_instruments") or []:
        if not isinstance(inst, dict):
            continue
        inst_heat = heat.get("instrument") if isinstance(heat.get("instrument"), dict) else None
        rated = bool(inst_heat and score_from(inst_heat, "crowdedness") is not None
                     and score_from(inst_heat, "capture") is not None
                     and inst_heat.get("verdict") is not None)
        instruments.append({
            "ticker": inst.get("ticker"), "kind": inst.get("kind"), "holds": inst.get("holds"),
            "rated": rated, "verdict": inst_heat.get("verdict") if rated else None,
        })

    row = {
        "chain_id": chain_id, "chain_title": chain.get("title"),
        "link_id": link_id, "link_name": link.get("name"),
        "criticality": (link.get("bottleneck") or {}).get("criticality"),
        "investability": link.get("investability"),
        "expression": expr["expression"], "size": expr["size"],
        "impact": expr["impact"], "crowdedness": expr["crowdedness"], "capture": expr["capture"],
        "issuer_verdict": heat.get("verdict"),
        "issuer_crowdedness": score_from(heat, "crowdedness"),
        "instrument_verdict": ((heat.get("instrument") or {}).get("verdict")
                                if isinstance(heat.get("instrument"), dict) else None),
        "occurrence_impact_score": appraisal.get("impact_score") if appraisal else None,
        "occurrence_band": appraisal.get("impact_band") if appraisal else None,
        "refs": {"heat": f"data/chains/{chain_id}.json#links/{link_id}/heat",
                 "impact": impact_ref},
        "best": best, "also": also, "instruments": instruments,
    }
    next_text, next_command = _next(row, inputs)
    row["lines"] = {"headline": row["link_name"] or "This link", "why": _why_line(row),
                     "stage": _stage_line(row), "next": next_text}
    row["next_command"] = next_command
    row["href"] = _href(row)
    row["adam_note"] = _adam_note(inputs.get("digest"), chain_id, link_id,
                                   (row["best"] or {}).get("ticker"))
    return row


def _market_as_of(top):
    dates = []
    for row in top:
        for obj in (row.get("best"), row.get("also")):
            if isinstance(obj, dict):
                price = obj.get("price")
                if isinstance(price, dict) and price.get("as_of"):
                    dates.append(str(price["as_of"]))
    return max(dates) if dates else None


def rank(inputs) -> dict:
    chains = inputs["chains"]
    rows: list = []
    unrankable: list = []
    instruments_unrated: list = []
    instruments_total = 0
    links_total = 0

    for chain_id in sorted(chains):
        chain = chains[chain_id]
        for link in chain.get("links") or []:
            if not isinstance(link, dict) or not link.get("id"):
                continue
            links_total += 1
            link_id = link["id"]
            price_instruments = link.get("price_instruments")
            has_instruments = isinstance(price_instruments, list) and bool(price_instruments)
            if has_instruments:
                instruments_total += 1

            listed = bool(_dives_for_link(inputs, chain_id, link_id)) \
                or _best_screen_row(inputs, chain_id, link_id) is not None \
                or bool(_mapped_candidates(inputs, chain_id, link_id))
            exprs = expressions(link, listed=listed)
            for expr in exprs:
                if expr.get("expression") == "INSTRUMENT" and expr.get("unrated"):
                    first = price_instruments[0] if has_instruments and isinstance(
                        price_instruments[0], dict) else {}
                    instruments_unrated.append({"chain_id": chain_id, "link_id": link_id,
                                                 "ticker": first.get("ticker")})

            scored = [e for e in exprs if e.get("size") is not None]
            if not scored:
                unrankable.append({"chain_id": chain_id, "link_id": link_id,
                                    "reason": _unrankable_reason(link)})
                continue

            best_expr = max(scored, key=lambda e: e["size"])
            rows.append(_build_row(chain, link, best_expr, inputs))

    def occ_key(row):
        score = row["occurrence_impact_score"]
        return score if isinstance(score, (int, float)) else -1

    rows.sort(key=lambda r: (-r["size"], -occ_key(r), r["chain_id"], r["link_id"]))
    top = rows[:3]
    for i, row in enumerate(top, 1):
        row["rank"] = i

    return {
        "generated_by": "tools/opportunities.py",
        "contract": ("Ranked links, not verdicts. Every number is copied from the file "
                     "named beside it in refs or in best, and a row with no dive is a "
                     "candidate for one, never a recommendation."),
        "ranking": ("Sorted by link_size (impact times capture times un-crowdedness, "
                     "on 0..100, over the best-scored expression of each link), ties "
                     "broken by the occurrence's own impact score, then chain id, then "
                     "link id."),
        "market_as_of": _market_as_of(top),
        "top": top,
        "ranked_total": len(rows),
        "links_total": links_total,
        "chains_total": len(chains),
        "unrankable": unrankable,
        "unrankable_total": len(unrankable),
        "instruments_total": instruments_total,
        "instruments_unrated": instruments_unrated,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    args = ap.parse_args()
    root = Path(args.root).resolve()

    result = rank(load_inputs(root))

    if result["chains_total"] == 0:
        print("opportunities: SCOPE EMPTY, no chains on disk")
        return 0

    for row in result["top"]:
        best = row["best"] or {}
        print(f"{row['rank']}. size {row['size']} | {row['chain_id']}/{row['link_id']} "
              f"| {row['expression']} | best {best.get('stage')} {best.get('ticker')}")
        for key in ("headline", "why", "stage", "next"):
            print(f"   {row['lines'][key]}")

    print(f"links_total={result['links_total']} chains_total={result['chains_total']} "
          f"ranked_total={result['ranked_total']} unrankable_total={result['unrankable_total']}")
    if result["unrankable"]:
        reasons: dict = {}
        for item in result["unrankable"]:
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
        print(f"  unrankable reasons: {reasons}")
    print(f"instruments_total={result['instruments_total']} "
          f"instruments_unrated={len(result['instruments_unrated'])}")
    for item in result["instruments_unrated"]:
        print(f"  unrated: {item['chain_id']}/{item['link_id']} ({item.get('ticker')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
