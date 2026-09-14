#!/usr/bin/env python3
"""The one rule for where a ticker's market and EDGAR files live.

`tools/fetch/fetch.py` writes `data/market/<safe_name(ticker)>.json`, mapping `.` and `/`
to `-` (so `1072.HK` is `1072-HK.json`). Until 2026-09-01 every reader re-implemented that
rule or skipped it: check_impact looked up `data/market/<ticker>.json` verbatim, so a
dotted ticker was "missing" even when its file was on disk, and a coverage census counted
81 of 142 misses that were this artifact and not a gap at all.

This module is the reader-side definition. fetch.py keeps its own `safe_name` because the
fetcher runs in GitHub Actions with a different sys.path; `tools/tests/test_market_paths.py`
asserts the two agree on every ticker shape in the repo, so the rule can only drift loudly.
"""
from pathlib import Path


def safe_name(ticker) -> str:
    return str(ticker).replace(".", "-").replace("/", "-")


def market_path(data: Path, ticker) -> Path:
    """`data/market/<T>.json` for a ticker as written on disk."""
    return Path(data) / "market" / f"{safe_name(ticker)}.json"


def edgar_doc_path(data: Path, ticker) -> Path:
    return Path(data) / "edgar" / "docs" / f"{safe_name(ticker)}.json"


# A mapping listing's `ticker` carries the fetcher's own suffix for most non-US names
# (e.g. "2899.HK", "6501.T") because that is the identity evidence the listing verified
# against. Some listings instead carry a bare local exchange code with no dot anywhere in
# the record (TWSE "2330", TSX "WSP") — `market_ticker` is meant to carry the fetched form
# for those, but 168 of 370 listings on 2026-09-13 have no `market_ticker` at all. This
# table lets `resolve_market_stem` recover the fetcher's suffix from the listing's
# `exchange` field alone. Every entry is derived empirically from the mappings already on
# disk: for exchanges with at least one dotted ticker or market_ticker in the corpus, the
# suffix is that dotted form's own extension (majority vote where a share-class ticker like
# "BRK.B" or "MOG.A" adds noise); SGX and KOSPI carry no dotted example in the mappings but
# are confirmed directly against the market files a fetch actually wrote (AJBU-SI.json,
# 005930-KS.json, 000660-KS.json). Never used to invent a file: resolve_market_stem only
# returns a candidate that is actually present in `available`.
EXCHANGE_SUFFIX = {
    "ASX": "AX",
    "BSE": "BJ",  # Beijing Stock Exchange, as this corpus's mapping data spells it
    "BUCHAREST STOCK EXCHANGE": "RO",
    "EPA": "PA", "EURONEXT": "PA", "EURONEXT MILAN": "MI", "EURONEXT PARIS": "PA",
    "EURONEXT STAR MILAN": "MI",
    "HEL": "HE",
    "HKEX": "HK", "THE STOCK EXCHANGE OF HONG KONG LIMITED": "HK",
    "KOSDAQ": "KQ", "KOSPI": "KS",
    "LSE": "L",
    "NASDAQ FIRST NORTH GROWTH MARKET": "ST",
    "NSE": "NS", "NATIONAL STOCK EXCHANGE OF INDIA LIMITED": "NS",
    "NATIONAL STOCK EXCHANGE OF INDIA LTD.": "NS",
    "SGX": "SI",
    "SIX": "SW", "SIX SWISS EXCHANGE": "SW",
    "SSE": "SS",
    "STO": "ST",
    "SZSE": "SZ",
    "TSE": "T",
    "TSX": "TO",
    "TWSE": "TW",
    "VIE": "VI",
    "WSE": "WA",
    "XETR": "DE", "XETRA": "DE", "XETRA FRANKFURT": "DE",
}


def market_stem_candidates(ticker=None, exchange=None, market_ticker=None) -> list:
    """Every filename this listing could resolve to, most authoritative first.

    1. `safe_name(market_ticker)` — the mapping's own record of the fetched form.
    2. `safe_name(ticker)` — correct as-is for a US listing and for a non-US listing whose
       `ticker` already carries the exchange suffix (see EXCHANGE_SUFFIX's docstring).
    3. `safe_name(ticker) + "-" + suffix` — only when `ticker` carries no dot or slash of
       its own and `exchange` resolves to a known suffix: the bare-local-code case
       (TWSE "2330", TSX "WSP").
    """
    out = []
    if market_ticker:
        out.append(safe_name(market_ticker))
    if ticker:
        out.append(safe_name(ticker))
        suffix = EXCHANGE_SUFFIX.get(str(exchange or "").strip().upper())
        if suffix and "." not in str(ticker) and "/" not in str(ticker):
            out.append(f"{safe_name(ticker)}-{suffix}")
    # De-dupe, preserving order: market_ticker and ticker are frequently identical.
    seen, dedup = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            dedup.append(c)
    return dedup


def resolve_market_stem(ticker=None, exchange=None, market_ticker=None, available=None):
    """The `data/market/<stem>.json` stem this listing's file was actually fetched under,
    or None. `available` is anything supporting `in` — the in-memory dict app/build.py
    already loaded (keyed by stem), a set of stems, or a `data/market` directory Path (the
    last checked as a filesystem existence test). Only ever returns a candidate `available`
    actually contains: never fabricates a file that was not fetched.
    """
    candidates = market_stem_candidates(ticker, exchange, market_ticker)
    if isinstance(available, Path):
        for c in candidates:
            if (available / f"{c}.json").exists():
                return c
        return None
    for c in candidates:
        if available is not None and c in available:
            return c
    return None
