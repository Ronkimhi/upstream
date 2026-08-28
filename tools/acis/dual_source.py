"""Dual-source price/fundamentals module (F16, decision-standards.md 2.5).

Any number that can directly trigger a capital action (a kill-row price
check, a DARK-boundary admission, an OPEN-REAL reprice) is fetched from
two independent sources. Disagreement above the tolerance is
PRICE-DISPUTED: both prints are returned and the caller escalates; the
module never averages, never picks a winner, never acts silently.

Sources: yfinance (primary) + Stooq daily CSV (secondary) for prices;
EDGAR companyfacts XBRL as the fundamentals second source.
Rule 21: every call reports which sources answered.
"""
import csv
import io
import logging
from datetime import datetime

import requests

from acis.config import (
    STOOQ_DAILY_CSV_URL, DUAL_SOURCE_DISAGREEMENT_PCT, EDGAR_USER_AGENT,
)

logger = logging.getLogger("acis.dual_source")


def _stooq_symbol(ticker):
    t = ticker.lower()
    if "." not in t:
        return t + ".us"
    return t


def fetch_price_yf(ticker):
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="5d", auto_adjust=False)
        if hist is None or hist.empty:
            return None
        last = hist.dropna(subset=["Close"]).iloc[-1]
        return {"close": float(last["Close"]),
                "date": str(last.name.date()),
                "source": "yfinance"}
    except Exception as e:
        logger.debug("yfinance price failed for %s: %s", ticker, e)
        return None


def fetch_price_stooq(ticker):
    try:
        url = STOOQ_DAILY_CSV_URL.format(symbol=_stooq_symbol(ticker))
        resp = requests.get(url, timeout=30,
                            headers={"User-Agent": EDGAR_USER_AGENT})
        if resp.status_code != 200 or not resp.text.startswith("Date"):
            return None
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        if not rows:
            return None
        last = rows[-1]
        return {"close": float(last["Close"]),
                "date": last["Date"],
                "source": "stooq"}
    except Exception as e:
        logger.debug("stooq price failed for %s: %s", ticker, e)
        return None


def compare_prints(a, b, tolerance_pct=DUAL_SOURCE_DISAGREEMENT_PCT):
    """Pure comparison. Returns (status, detail):
    AGREED (both, within tolerance) / DISPUTED (both, beyond it) /
    SINGLE-SOURCE (one answered) / NO-DATA (neither)."""
    if a is None and b is None:
        return "NO-DATA", {"prints": []}
    if a is None or b is None:
        only = a or b
        return "SINGLE-SOURCE", {"prints": [only],
                                 "note": "one source answered; a capital "
                                         "action needs a second print or a "
                                         "human confirm"}
    base = max(abs(a["close"]), 1e-9)
    diff_pct = abs(a["close"] - b["close"]) / base * 100.0
    detail = {"prints": [a, b], "diff_pct": round(diff_pct, 2)}
    if diff_pct > tolerance_pct:
        return "DISPUTED", detail
    return "AGREED", detail


def dual_source_price(ticker):
    """Fetch both prints and compare. Returns
    {ticker, status, close (only when AGREED), detail, as_of}."""
    yf_print = fetch_price_yf(ticker)
    stooq_print = fetch_price_stooq(ticker)
    status, detail = compare_prints(yf_print, stooq_print)
    result = {
        "ticker": ticker,
        "status": status,
        "close": (yf_print or {}).get("close") if status == "AGREED" else None,
        "detail": detail,
        "as_of": datetime.now().strftime("%Y-%m-%d"),
    }
    logger.info("dual-source %s: %s (%s)", ticker, status,
                detail.get("diff_pct", "n/a"))
    return result
