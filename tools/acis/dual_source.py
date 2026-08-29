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
    STOOQ_DAILY_CSV_URL, DUAL_SOURCE_DISAGREEMENT_PCT, STOOQ_USER_AGENT,
)

logger = logging.getLogger("acis.dual_source")


def _stooq_symbol(ticker):
    t = ticker.lower()
    if "." not in t:
        return t + ".us"
    return t


def fetch_price_yf(ticker):
    """Returns (print|None, leg). The leg records WHY a source did not answer.

    Both legs used to swallow their failure into a debug log and return None, so
    a run where stooq never answered was indistinguishable on disk from a run
    where stooq agreed — every market file in the repo reads SINGLE_SOURCE with
    no recorded reason. A silent leg is an unfalsifiable second source.
    """
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="5d", auto_adjust=False)
        if hist is None or hist.empty:
            return None, {"source": "yfinance", "answered": False, "reason": "empty history"}
        last = hist.dropna(subset=["Close"]).iloc[-1]
        return ({"close": float(last["Close"]),
                 "date": str(last.name.date()),
                 "source": "yfinance"},
                {"source": "yfinance", "answered": True, "reason": None})
    except Exception as e:
        logger.debug("yfinance price failed for %s: %s", ticker, e)
        return None, {"source": "yfinance", "answered": False,
                      "reason": f"{type(e).__name__}: {str(e)[:120]}"}


def fetch_price_stooq(ticker):
    """Returns (print|None, leg). See fetch_price_yf for why the leg exists."""
    url = STOOQ_DAILY_CSV_URL.format(symbol=_stooq_symbol(ticker))
    try:
        resp = requests.get(url, timeout=30,
                            headers={"User-Agent": STOOQ_USER_AGENT})
        if resp.status_code != 200:
            return None, {"source": "stooq", "answered": False,
                          "reason": f"http {resp.status_code}", "symbol": _stooq_symbol(ticker)}
        if not resp.text.startswith("Date"):
            # Stooq answers 200 with a plain-text body ("No data" / a throttle notice)
            # for an unknown symbol or a rate limit, which the old code read as a
            # generic failure. The body's first line is the diagnosis.
            return None, {"source": "stooq", "answered": False,
                          "reason": f"non-CSV body: {resp.text.strip()[:80]!r}",
                          "symbol": _stooq_symbol(ticker)}
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        if not rows:
            return None, {"source": "stooq", "answered": False, "reason": "CSV had no rows",
                          "symbol": _stooq_symbol(ticker)}
        last = rows[-1]
        return ({"close": float(last["Close"]),
                 "date": last["Date"],
                 "source": "stooq"},
                {"source": "stooq", "answered": True, "reason": None,
                 "symbol": _stooq_symbol(ticker)})
    except Exception as e:
        logger.debug("stooq price failed for %s: %s", ticker, e)
        return None, {"source": "stooq", "answered": False,
                      "reason": f"{type(e).__name__}: {str(e)[:120]}",
                      "symbol": _stooq_symbol(ticker)}


def _days_apart(a, b):
    try:
        da = datetime.strptime(a["date"], "%Y-%m-%d").date()
        db = datetime.strptime(b["date"], "%Y-%m-%d").date()
        return abs((da - db).days)
    except Exception:
        return None


def compare_prints(a, b, tolerance_pct=DUAL_SOURCE_DISAGREEMENT_PCT, legs=None):
    """Pure comparison. Returns (status, detail):
    AGREED (both, SAME SESSION, within tolerance) / DISPUTED (both, same session,
    beyond it) / SINGLE-SOURCE (one answered, or the two are for different dates)
    / NO-DATA (neither).

    The date check is the substantive addition: two prints for different sessions
    are not two readings of one number. Comparing them either invents a dispute
    (an overnight move beyond tolerance) or certifies agreement between two prices
    that were never the same price. Stooq routinely lags yfinance by a session, so
    this is the normal case, not the edge case. When the dates differ the honest
    status is SINGLE-SOURCE on the fresher print, with the offset recorded — we do
    not have a second reading of today's close, and saying so is the whole point.
    """
    detail = {"prints": [], "legs": legs or {}}
    if a is None and b is None:
        return "NO-DATA", detail
    if a is None or b is None:
        only = a or b
        detail["prints"] = [only]
        detail["note"] = ("one source answered; a capital action needs a second "
                          "print or a human confirm")
        return "SINGLE-SOURCE", detail
    offset = _days_apart(a, b)
    detail["prints"] = [a, b]
    detail["date_offset_days"] = offset
    if offset != 0:
        fresher = a if a["date"] >= b["date"] else b
        detail["prints"] = [fresher, (b if fresher is a else a)]
        detail["note"] = (f"prints are {offset if offset is not None else 'an unknown number of'} "
                          f"day(s) apart ({a['source']} {a['date']} vs {b['source']} {b['date']}); "
                          "not a same-session second reading")
        return "SINGLE-SOURCE", detail
    base = max(abs(a["close"]), 1e-9)
    diff_pct = abs(a["close"] - b["close"]) / base * 100.0
    detail["diff_pct"] = round(diff_pct, 2)
    if diff_pct > tolerance_pct:
        return "DISPUTED", detail
    return "AGREED", detail


def dual_source_price(ticker):
    """Fetch both prints and compare. Returns
    {ticker, status, close (only when AGREED), detail, as_of}."""
    yf_print, yf_leg = fetch_price_yf(ticker)
    stooq_print, stooq_leg = fetch_price_stooq(ticker)
    legs = {"yfinance": yf_leg, "stooq": stooq_leg}
    status, detail = compare_prints(yf_print, stooq_print, legs=legs)
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
