#!/usr/bin/env python3
"""`run book` — rank every chain-anchored listed name by what the market is pricing in.

Why this exists. Until 2026-09-08 the machine had no output stage between a dive and the
reader. It produced 14 FINAL verdicts, 13 of them WATCH, and nothing anywhere ranked them
or the 149 other chain-anchored names that already carried a solved reverse DCF, a
Piotroski/Beneish/Altman read and a fresh price. Ron's question ("where do I invest") had
no surface to land on, so the answer was always "run another dive", which is the most
expensive way to ask a cheap question. The book is the cheap question: it reads what is
already on disk and says which names are worth a dive, in order.

What this is NOT. Every number here is COPIED from a file, never computed by this tool
beyond arithmetic that is declared in the row itself (52-week position, distance from a
dive's own zone). The book states no verdict: `verdict` is only ever echoed from a
`data/stocks/` file that a dive and a red team already closed. A row with no dive is a
CANDIDATE, which means "worth the dive", not "buy". That distinction is the whole contract
of this file, and method §7's vocabulary still belongs exclusively to Stocky.

The venue rule (CLAUDE.md) holds unchanged: this reads `data/`, fetches nothing, and
invents no price.
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import date, datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "book.json"

# Link heat ordered the way method §3 orders it: the money corner and the links nobody has
# mapped are where an un-crowded position can still exist, and the crowded end is where it
# cannot. A name's link is the first thing that decides whether it is worth reading at all.
HEAT_RANK = {"UNDISCOVERED": 0, "QUIET": 1, "EMERGING": 2, None: 3,
             "CROWDED": 4, "OVER_CROWDED": 5}


def _load(path: pathlib.Path):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001 - a broken file is skipped and counted, never fatal
        return None


def _links():
    """(chain_id, link_id) -> link facts, including its heat verdict and money-corner flag."""
    out = {}
    for path in sorted((DATA / "chains").glob("*.json")):
        if path.name.startswith("_"):
            continue
        chain = _load(path)
        if not chain:
            continue
        for link in chain.get("links") or []:
            heat = link.get("heat") or {}
            out[(chain.get("id"), link.get("id"))] = {
                "chain_id": chain.get("id"),
                "chain_title": chain.get("title"),
                "link_id": link.get("id"),
                "link_name": link.get("name"),
                "heat_verdict": heat.get("verdict"),
                "money_corner": bool(heat.get("money_corner")),
                "criticality": (link.get("bottleneck") or {}).get("criticality"),
                "investability": link.get("investability"),
            }
    return out


def _placements():
    """ticker -> [(chain_id, link_id, issuer_id)], via each mapping's own listings block."""
    out = {}
    for path in sorted((DATA / "mappings").glob("*.json")):
        mapping = _load(path)
        if not mapping:
            continue
        by_issuer = {}
        for listing in mapping.get("listings") or []:
            ticker = listing.get("market_ticker") or listing.get("ticker")
            if ticker:
                by_issuer.setdefault(listing.get("issuer_id"), ticker.upper())
        for placement in mapping.get("placements") or []:
            if placement.get("status") != "ACTIVE":
                continue
            ticker = by_issuer.get(placement.get("issuer_id"))
            if ticker:
                out.setdefault(ticker, []).append(
                    (mapping.get("chain_id"), placement.get("link_id"),
                     placement.get("issuer_id")))
    return out


def _screened():
    """(chain_id, link_id, ticker) already carried on a screen shortlist."""
    out = set()
    for path in sorted((DATA / "screens").glob("*.json")):
        screen = _load(path)
        if not screen:
            continue
        for rows in (screen.get("buckets") or {}).values():
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                ticker = row.get("market_ticker") or row.get("ticker")
                if ticker:
                    out.add((row.get("chain_id"), row.get("link_id"), ticker.upper()))
    return out


def _dives():
    """(ticker, chain_id) -> the closed verdict, echoed, never recomputed."""
    out = {}
    for path in sorted((DATA / "stocks").glob("*.json")):
        if path.name.startswith("_"):
            continue
        dive = _load(path)
        if not dive:
            continue
        verdict = dive.get("verdict")
        verdict = verdict.get("call") if isinstance(verdict, dict) else verdict
        zone = dive.get("would_buy_zone") or dive.get("entry_zone") or {}
        out[((dive.get("ticker") or "").upper(), dive.get("chain_id"))] = {
            "verdict": verdict,
            "status": dive.get("status"),
            "clock": dive.get("clock"),
            "zone_low": zone.get("low") if isinstance(zone, dict) else None,
            "zone_high": zone.get("high") if isinstance(zone, dict) else None,
            "dive_ref": f"data/stocks/{path.name}",
        }
    return out


def _price(market: dict):
    rows = ((market.get("series") or {}).get("rows")) or []
    if not rows:
        return None, None
    last = rows[-1]
    return (last[1] if len(last) > 1 else None), (str(last[0])[:10] if last else None)


def build():
    links, placements, screened, dives = _links(), _placements(), _screened(), _dives()
    rows, skipped = [], {"no_placement": 0, "no_reverse_dcf": 0, "no_price": 0,
                         "no_link_record": 0}

    for path in sorted((DATA / "market").glob("*.json")):
        market = _load(path)
        if not market:
            continue
        ticker = (market.get("ticker") or "").upper()
        if ticker not in placements:
            skipped["no_placement"] += 1
            continue
        quality = market.get("quality") or {}
        rdcf = quality.get("reverse_dcf") or {}
        implied = rdcf.get("implied_fcf_cagr")
        if implied is None:
            skipped["no_reverse_dcf"] += 1
            continue
        price, price_as_of = _price(market)
        if price is None:
            skipped["no_price"] += 1
            continue

        week52 = market.get("week52") or {}
        low, high = week52.get("low"), week52.get("high")
        position = None
        if isinstance(low, (int, float)) and isinstance(high, (int, float)) and high > low:
            position = round(100 * (price - low) / (high - low))

        piotroski = quality.get("piotroski") or {}
        beneish = quality.get("beneish") or {}
        altman = quality.get("altman") or {}

        for chain_id, link_id, issuer_id in placements[ticker]:
            link = links.get((chain_id, link_id))
            if not link:
                skipped["no_link_record"] += 1
                continue
            dive = dives.get((ticker, chain_id)) or {}
            zone_high = dive.get("zone_high")
            above_zone = None
            if isinstance(zone_high, (int, float)) and zone_high > 0:
                above_zone = round(100 * (price - zone_high) / zone_high, 1)

            rows.append({
                "ticker": ticker,
                "issuer_id": issuer_id,
                "chain_id": chain_id,
                "link_id": link_id,
                "link_name": link["link_name"],
                "heat_verdict": link["heat_verdict"],
                "money_corner": link["money_corner"],
                "criticality": link["criticality"],
                "investability": link["investability"],
                # Expectations. horizon_spread is the 5y/7y/10y sensitivity band: a gap
                # smaller than its own spread is not a gap (method §7), so the book carries
                # the spread beside the number rather than letting a reader forget it.
                "implied_fcf_cagr": implied,
                "horizon_spread": rdcf.get("horizon_spread"),
                "implied_by_horizon": rdcf.get("implied_by_horizon"),
                "reverse_dcf_tag": (rdcf.get("assumptions") or {}).get("tag"),
                "piotroski": piotroski.get("score"),
                "piotroski_state": piotroski.get("state"),
                "beneish_state": beneish.get("state"),
                "altman_state": altman.get("state"),
                "price": price,
                "price_as_of": price_as_of,
                "week52_position": position,
                "data_tier": market.get("tier"),
                "price_status": market.get("price_status"),
                "quality_as_of": quality.get("as_of"),
                "screened": (chain_id, link_id, ticker) in screened,
                "dived": bool(dive),
                "verdict": dive.get("verdict"),
                "clock": dive.get("clock"),
                "zone_low": dive.get("zone_low"),
                "zone_high": zone_high,
                "pct_above_zone_high": above_zone,
                "dive_ref": dive.get("dive_ref"),
                "market_ref": f"data/market/{path.name}",
            })

    def sort_key(row):
        # Money corner and un-crowded first, then what the market is pricing in, then
        # quality. A name with no Piotroski sorts last within its band rather than being
        # dropped: a missing score is a data gap, not evidence of a bad business.
        return (
            0 if row["money_corner"] else 1,
            HEAT_RANK.get(row["heat_verdict"], 3),
            row["implied_fcf_cagr"],
            -(row["piotroski"] if isinstance(row["piotroski"], int) else -1),
            row["ticker"],
        )

    rows.sort(key=sort_key)
    for i, row in enumerate(rows, 1):
        row["rank"] = i

    return {
        "id": "BOOK",
        "as_of": date.today().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": "tools/book.py",
        "contract": ("Ranked candidates, not verdicts. Every number is copied from the "
                     "file named in market_ref, dive_ref or the chain's heat block. A row "
                     "without a verdict has never been dived and is a candidate for one, "
                     "not a recommendation. method §7's vocabulary belongs to run deepdive."),
        "ranking": ("money corner first, then link heat (UNDISCOVERED before CROWDED), "
                    "then lowest implied FCF CAGR, then Piotroski."),
        "denominator": {
            "rows": len(rows),
            "distinct_tickers": len({r["ticker"] for r in rows}),
            "dived": sum(1 for r in rows if r["dived"]),
            "screened": sum(1 for r in rows if r["screened"]),
            "skipped": skipped,
        },
        "rows": rows,
    }


def main() -> int:
    book = build()
    OUT.write_text(json.dumps(book, indent=1) + "\n")
    d = book["denominator"]
    print(f"book: {d['rows']} rows / {d['distinct_tickers']} distinct tickers "
          f"({d['dived']} dived, {d['screened']} screened) -> {OUT.relative_to(ROOT)}")
    print(f"  skipped: {d['skipped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
