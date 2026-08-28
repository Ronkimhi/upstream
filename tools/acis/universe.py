"""
Universe building.

Rewritten 2026-08-28 (F13): the universe is built from SEC data, where
SIC codes actually exist, then enriched via yfinance.

Why: the original path read `info.get("sic")` from yfinance, which never
supplies a `sic` key, so every row fell through to a GICS sector-string
fallback that wholesale-excluded "information technology". COHR, LITE,
AAOI and FN could never enter the universe. The pipeline was structurally
blind to Ron's stated hunting ground (bug-ledger 2026-08-28, F13).

Build order:
  1. SEC company_tickers.json (all registrants with tickers)
  2. per-CIK data.sec.gov/submissions/ JSON, which carries `sic` and the
     exchange, filtered by the ACTIVE PROFILE's SIC targets FIRST (this
     cuts the enrichment fan-out by ~50x)
  3. yfinance enrichment (cap, ADV, GICS context) on the SIC survivors

finviz (F13): out of the critical path. `finviz_crosscheck()` remains as
an OPTIONAL sanity diff with a canary check and a cached-last-good
[STALE] fallback; its absence never blocks a run.

Rule 21: every stage logs examined / kept / errored.
"""
import json
import logging
import os
import time

import pandas as pd
import requests
import yfinance as yf

from acis.config import (
    SEC_COMPANY_TICKERS_URL, SEC_SUBMISSIONS_URL,
    EDGAR_USER_AGENT, EDGAR_DELAY_SECONDS,
)
from acis.utils import RateLimiter

logger = logging.getLogger("acis.universe")
_yf_rate_limiter = RateLimiter(delay_seconds=0.5)
_sec_rate_limiter = RateLimiter(delay_seconds=EDGAR_DELAY_SECONDS)

SEC_HEADERS = {"User-Agent": EDGAR_USER_AGENT}
US_EXCHANGES = {"NYSE", "Nasdaq", "NASDAQ", "NYSE American", "NYSE Arca", "AMEX", "CBOE"}


def fetch_company_tickers(session=None):
    """SEC company_tickers.json -> list of {cik, ticker, title}."""
    _sec_rate_limiter.wait()
    sess = session or requests
    resp = sess.get(SEC_COMPANY_TICKERS_URL, headers=SEC_HEADERS, timeout=30)
    resp.raise_for_status()
    raw = resp.json()
    rows = []
    for entry in raw.values():
        rows.append({
            "cik": int(entry["cik_str"]),
            "ticker": str(entry["ticker"]).upper(),
            "title": entry.get("title", ""),
        })
    logger.info("SEC company_tickers: %d registrants", len(rows))
    return rows


def fetch_submission_meta(cik, session=None):
    """Per-CIK submissions JSON -> {sic, sic_description, exchanges, name}.
    Returns None on error (caller counts it; Rule 21)."""
    _sec_rate_limiter.wait()
    sess = session or requests
    try:
        url = SEC_SUBMISSIONS_URL.format(cik=cik)
        resp = sess.get(url, headers=SEC_HEADERS, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json()
        sic = data.get("sic")
        return {
            "sic": int(sic) if sic not in (None, "", "0000") else None,
            "sic_description": data.get("sicDescription", ""),
            "exchanges": data.get("exchanges", []) or [],
            "name": data.get("name", ""),
        }
    except Exception as e:
        logger.debug("submissions fetch failed for CIK %s: %s", cik, e)
        return None


def yf_enrich_row(ticker_str):
    """yfinance cap/volume/GICS context for one ticker. None on failure.
    NOTE: no `sic` read here; SIC comes from SEC (F13)."""
    _yf_rate_limiter.wait()
    try:
        stock = yf.Ticker(ticker_str)
        info = stock.info
        if not info or "marketCap" not in info:
            return None
        return {
            "market_cap": info.get("marketCap"),
            "avg_volume": info.get("averageVolume",
                                   info.get("averageDailyVolume10Day")),
            "country": info.get("country", "Unknown"),
            "exchange": info.get("exchange", "Unknown"),
            "company_name": info.get("longName",
                                     info.get("shortName", ticker_str)),
            "sector_gics": info.get("sector", "Unknown"),
            "industry_gics": info.get("industry", "Unknown"),
        }
    except Exception as e:
        logger.warning("yfinance enrich failed for %s: %s", ticker_str, e)
        return None


def build_sec_universe(profile, session=None, max_tickers=None):
    """
    Build the US universe for a profile: SEC registrant list -> SIC filter
    (from real SEC-supplied codes) -> yfinance enrichment of survivors.
    Returns a DataFrame with a real `sic_code` column on every row.
    """
    registrants = fetch_company_tickers(session=session)
    if max_tickers:
        registrants = registrants[:max_tickers]

    examined = len(registrants)
    sic_kept, sic_errored = [], 0
    for reg in registrants:
        meta = fetch_submission_meta(reg["cik"], session=session)
        if meta is None:
            sic_errored += 1
            continue
        if meta["sic"] is None:
            continue
        if not any(str(x) in US_EXCHANGES or str(x).upper() in US_EXCHANGES
                   for x in meta["exchanges"]) and meta["exchanges"]:
            continue
        if profile.is_target_sic(meta["sic"]):
            sic_kept.append({**reg, **meta})

    logger.info(
        "SEC universe (%s): %d registrants examined, %d SIC-matched, %d fetch errors",
        profile.name, examined, len(sic_kept), sic_errored)
    if examined and sic_errored == examined:
        logger.error("SEC universe: EVERY submissions fetch errored. "
                     "Treat this run as FAILED, not as an empty universe.")

    rows, enrich_errors = [], 0
    for entry in sic_kept:
        info = yf_enrich_row(entry["ticker"])
        if info is None:
            enrich_errors += 1
            continue
        rows.append({
            "ticker": entry["ticker"],
            "company_name": info.get("company_name") or entry["name"],
            "cik": entry["cik"],
            "sic_code": entry["sic"],
            "sic_description": entry["sic_description"],
            "exchange": info.get("exchange", "US"),
            "market_cap": info.get("market_cap"),
            "sector_gics": info.get("sector_gics", "Unknown"),
            "industry_gics": info.get("industry_gics", "Unknown"),
            "avg_volume": info.get("avg_volume"),
            "country": info.get("country", "USA"),
        })

    logger.info("SEC universe (%s): %d enriched, %d enrichment errors",
                profile.name, len(rows), enrich_errors)
    return pd.DataFrame(rows)


def filter_registrants_by_sic(registrant_metas, profile):
    """Pure SIC filter over pre-fetched registrant metadata rows
    ({ticker, sic, ...}). Split out for offline testing (test_universe_sic)."""
    kept = []
    for meta in registrant_metas:
        sic = meta.get("sic")
        if sic is None:
            continue
        if profile.is_target_sic(sic):
            kept.append(meta)
    return kept


# --- finviz: demoted to optional cross-check (F13) ---

_FINVIZ_CACHE = os.path.join(".cache", "finviz_last_good.json")
FINVIZ_CANARY_TICKERS = {"AAPL", "MSFT"}  # if a broad US pull lacks these, the parse is broken


def finviz_crosscheck():
    """
    OPTIONAL diff source, never in the critical path. Pulls the finviz
    screener, canary-checks the parse, and falls back to the cached last
    good pull tagged [STALE] on any failure. Returns (tickers, note).
    """
    try:
        from finvizfinance.screener.overview import Overview
        screener = Overview()
        screener.set_filter(filters_dict={"Market Cap.": "+Small (over $300mln)",
                                          "Country": "USA"})
        df = screener.screener_view()
        if "Ticker" not in df.columns:
            raise ValueError("finviz parse: no 'Ticker' column (header drift)")
        tickers = set(df["Ticker"].astype(str))
        if not FINVIZ_CANARY_TICKERS & tickers:
            raise ValueError("finviz canary failed: %s absent from a broad US pull"
                             % sorted(FINVIZ_CANARY_TICKERS))
        os.makedirs(os.path.dirname(_FINVIZ_CACHE), exist_ok=True)
        with open(_FINVIZ_CACHE, "w") as fh:
            json.dump({"cached_at": time.strftime("%Y-%m-%d"),
                       "tickers": sorted(tickers)}, fh)
        return tickers, "fresh"
    except Exception as e:
        logger.warning("finviz cross-check unavailable (%s); trying cached last good", e)
        try:
            with open(_FINVIZ_CACHE) as fh:
                cached = json.load(fh)
            return set(cached["tickers"]), "[STALE] cached %s" % cached.get("cached_at")
        except Exception:
            return set(), "unavailable (no cache)"
