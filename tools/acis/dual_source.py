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
import re
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


STOOQ_HEADERS = {
    "User-Agent": STOOQ_USER_AGENT,
    "Accept": "text/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_price_stooq(ticker):
    """Returns (print|None, leg). See fetch_price_yf for why the leg exists."""
    url = STOOQ_DAILY_CSV_URL.format(symbol=_stooq_symbol(ticker))
    try:
        resp = requests.get(url, timeout=30, headers=STOOQ_HEADERS)
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


def fetch_price_stockanalysis(ticker):
    """Second source, adopted 2026-08-30. Returns (print|None, leg).

    Chosen on evidence from the venue that matters rather than from documentation: the
    `bakeoff` workflow probed seven candidates from a GitHub Actions runner and this was
    the only key-free, non-Yahoo source that answered with a current price. It agreed
    with the primary to the cent on the same session (319.70 vs 319.70001, 2026-08-28),
    which is the property a second source exists to provide.

    Two honest caveats, recorded because they bear on how much this print is worth:
    the endpoint is undocumented, so it can change shape without notice — hence
    selecting the row by MAX DATE rather than by position, after the bake-off's first
    parser silently returned a year-old close that looked like corroboration — and the
    terms of use for programmatic access are not explicit, so this stays at the repo's
    natural volume (tens of requests on a weekday) and must not be scaled up without
    Ron reading them. If it stops answering, `price_status` returns to SINGLE_SOURCE with
    the reason on the file, which is exactly the behaviour that made stooq's decade-long
    silence visible in the first place.
    """
    url = f"https://stockanalysis.com/api/symbol/s/{ticker.lower()}/history"
    try:
        resp = requests.get(url, params={"range": "1M", "period": "Daily"},
                            headers={"User-Agent": STOOQ_USER_AGENT}, timeout=30)
        if resp.status_code != 200:
            return None, {"source": "stockanalysis", "answered": False,
                          "reason": f"http {resp.status_code}"}
        js = resp.json() or {}
        rows = js.get("data") or js.get("result") or []
        if not isinstance(rows, list) or not rows:
            return None, {"source": "stockanalysis", "answered": False,
                          "reason": f"no rows in payload: {str(js)[:80]}"}
        best = None
        for row in rows:
            if isinstance(row, dict):
                close, date = row.get("c", row.get("close")), row.get("t") or row.get("date")
            elif isinstance(row, list) and len(row) >= 2:
                close, date = row[-1], row[0]
            else:
                continue
            try:
                close = float(close)
            except (TypeError, ValueError):
                continue
            # A missing date must never become the STRING "None". `str(None)[:10]` is
            # truthy, so the earlier guard let a dateless row through carrying a
            # plausible-looking date — the exact defect class this repo spent a day
            # removing. An undated price is not a price we can compare to anything.
            date = str(date)[:10] if isinstance(date, (str, int, float)) else ""
            if close <= 0 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                continue
            if best is None or date > best["date"]:
                best = {"close": close, "date": date, "source": "stockanalysis"}
        if best is None:
            return None, {"source": "stockanalysis", "answered": False,
                          "reason": f"no dated close among {len(rows)} row(s)"}
        return best, {"source": "stockanalysis", "answered": True, "reason": None}
    except Exception as e:  # noqa: BLE001
        logger.debug("stockanalysis price failed for %s: %s", ticker, e)
        return None, {"source": "stockanalysis", "answered": False,
                      "reason": f"{type(e).__name__}: {str(e)[:120]}"}


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
    # Second source, in preference order. stockanalysis is the one that answers this
    # venue (bake-off, 2026-08-30); stooq is kept as a fallback rather than deleted
    # because its refusal is a property of the runner's IP, not of the code, and a
    # different venue may well get a different answer. Both legs are always reported,
    # so "the second source did not answer" can never again mean silence.
    sa_print, sa_leg = fetch_price_stockanalysis(ticker)
    second, second_leg_name = (sa_print, "stockanalysis")
    stooq_print, stooq_leg = (None, {"source": "stooq", "answered": False,
                                     "reason": "not attempted: stockanalysis answered"})
    if second is None:
        stooq_print, stooq_leg = fetch_price_stooq(ticker)
        second = stooq_print
        second_leg_name = "stooq"
    legs = {"yfinance": yf_leg, "stockanalysis": sa_leg, "stooq": stooq_leg,
            "second_source_used": second_leg_name if second is not None else None}
    status, detail = compare_prints(yf_print, second, legs=legs)
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
