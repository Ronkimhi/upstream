#!/usr/bin/env python3
"""Bake-off: which free price sources actually answer from the Actions venue?

The dual-source guarantee in `docs/method.md` section 1 has never been met. Stooq was the
designated second source and answers every request from GitHub Actions with an HTML robots
page, whatever User-Agent it carries — verified twice on 2026-08-29. That failure was
invisible for the repo's whole life because both legs swallowed their errors.

The question "which free source works" cannot be answered from a session: the answer
depends on the IP the request comes from, and the only IP that matters is the runner's.
So this script asks the question FROM the runner and writes down what it saw. It is a
diagnostic, not a fetcher: it writes `data/health/source_bakeoff.json` and never touches
`data/market/`.

Each candidate reports, for one known-liquid US ticker:
  answered      — did we get a usable close price at all
  close / date  — what it said, so two sources can be compared for AGREEMENT, not just
                  liveness (a source that answers with a wrong number is worse than one
                  that refuses)
  status/reason — why not, in the source's own words, when it did not answer
  needs_key     — whether this candidate is gated behind a signup

Run: python3 tools/fetch/source_bakeoff.py [--ticker AAPL] [--dry-run]
Exit 0 always: this reports, it never gates.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import requests  # noqa: E402

from acis.config import STOOQ_DAILY_CSV_URL, STOOQ_USER_AGENT  # noqa: E402

NOW = datetime.now(timezone.utc)
UA = {"User-Agent": "UpstreamResearch/1.0 (+mailto:ronkkimhi@gmail.com)"}
TIMEOUT = 30


def _num(x):
    try:
        v = float(x)
        return v if v == v and v > 0 else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- candidates
# Each returns (close, date, note). Raising is fine; the runner records it.

def c_stooq(t):
    """Incumbent second source. Recorded so its refusal stays evidenced, not remembered."""
    r = requests.get(STOOQ_DAILY_CSV_URL.format(symbol=f"{t.lower()}.us"),
                     headers={"User-Agent": STOOQ_USER_AGENT}, timeout=TIMEOUT)
    if r.status_code != 200 or not r.text.startswith("Date"):
        return None, None, f"http {r.status_code}, body starts {r.text.strip()[:60]!r}"
    last = r.text.strip().splitlines()[-1].split(",")
    return _num(last[4]), last[0], "daily CSV"


def c_yahoo_chart(t):
    """Yahoo's own chart endpoint. NOT independent of yfinance — same upstream provider.
    Included only as the control: if this fails, the runner's network is the problem, not
    the candidate's policy."""
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}",
                     params={"range": "5d", "interval": "1d"}, headers=UA, timeout=TIMEOUT)
    if r.status_code != 200:
        return None, None, f"http {r.status_code}"
    res = (r.json().get("chart") or {}).get("result") or []
    if not res:
        return None, None, "no result block"
    ts = res[0].get("timestamp") or []
    closes = ((res[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    pairs = [(a, b) for a, b in zip(ts, closes) if b is not None]
    if not pairs:
        return None, None, "no non-null closes"
    epoch, close = pairs[-1]
    return _num(close), datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d"), \
        "CONTROL ONLY — same provider as yfinance, not an independent second source"


def c_nasdaq(t):
    """Nasdaq's own public quote API. No key. Genuinely independent of Yahoo — it is the
    exchange's own distribution — which is what makes it worth probing even though it is
    undocumented and known to be picky about headers."""
    last_err = None
    for _ in (1, 2):   # one read timeout is not evidence of a policy block
        try:
            r = requests.get(f"https://api.nasdaq.com/api/quote/{t}/historical",
                             params={"assetclass": "stocks", "limit": 5},
                             headers={**UA, "Accept": "application/json"}, timeout=TIMEOUT)
            break
        except requests.RequestException as e:
            last_err = e
    else:
        return None, None, f"{type(last_err).__name__} twice: {str(last_err)[:90]}"
    if r.status_code != 200:
        return None, None, f"http {r.status_code}"
    rows = (((r.json() or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
    if not rows:
        return None, None, f"no rows: {json.dumps(r.json())[:120]}"
    row = rows[0]
    close = _num(str(row.get("close", "")).replace("$", "").replace(",", ""))
    # Nasdaq returns MM/DD/YYYY.
    d = str(row.get("date", ""))
    iso = None
    if d.count("/") == 2:
        mm, dd, yy = d.split("/")
        iso = f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}"
    return close, iso, "no key; exchange's own distribution"


def c_stockanalysis(t):
    """stockanalysis.com's JSON used by its own charts. No key. Independent aggregator.
    Undocumented, so treated as best-effort: if it answers with a number that agrees with
    the primary, it is a usable corroborating print; if it drifts or dies, the leg
    diagnostics say so rather than the number silently vanishing."""
    r = requests.get(f"https://stockanalysis.com/api/symbol/s/{t.lower()}/history",
                     params={"range": "1M", "period": "Daily"}, headers=UA, timeout=TIMEOUT)
    if r.status_code != 200:
        return None, None, f"http {r.status_code}"
    js = r.json() or {}
    data = js.get("data") or js.get("result") or []
    if not isinstance(data, list) or not data:
        return None, None, f"unexpected shape: {json.dumps(js)[:120]}"
    # Select by MAX DATE, never by array position. The first version of this took
    # data[-1] and the 2026-08-30 bake-off duly reported a close of 232.56 dated
    # 2025-08-28 — a year stale and 27% away from the real price, presented as a
    # successful answer. An undocumented endpoint owes us no ordering guarantee, and a
    # positional guess against one is how a wrong number gets to look like corroboration.
    best = None
    for row in data:
        if isinstance(row, dict):
            close, date = _num(row.get("c") or row.get("close")), str(row.get("t") or row.get("date"))[:10]
        elif isinstance(row, list) and len(row) >= 2:
            close, date = _num(row[-1]), str(row[0])[:10]
        else:
            continue
        if close is None or not date:
            continue
        if best is None or date > best[1]:
            best = (close, date)
    if best is None:
        return None, None, f"no dated close in {len(data)} row(s): {str(data[:1])[:100]}"
    return best[0], best[1], "no key; undocumented"


def c_alphavantage(t):
    key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not key:
        return None, None, "no ALPHAVANTAGE_API_KEY secret set"
    r = requests.get("https://www.alphavantage.co/query",
                     params={"function": "TIME_SERIES_DAILY", "symbol": t,
                             "outputsize": "compact", "apikey": key},
                     headers=UA, timeout=TIMEOUT)
    if r.status_code != 200:
        return None, None, f"http {r.status_code}"
    js = r.json()
    series = js.get("Time Series (Daily)")
    if not series:
        return None, None, f"no series: {json.dumps(js)[:120]}"
    day = sorted(series)[-1]
    return _num(series[day].get("4. close")), day, "free tier 25 req/day"


def c_finnhub(t):
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return None, None, "no FINNHUB_API_KEY secret set"
    r = requests.get("https://finnhub.io/api/v1/quote",
                     params={"symbol": t, "token": key}, headers=UA, timeout=TIMEOUT)
    if r.status_code != 200:
        return None, None, f"http {r.status_code}"
    js = r.json()
    # `c` is the current/last close, `t` its unix timestamp.
    if not js.get("c"):
        return None, None, f"no close in payload: {json.dumps(js)[:120]}"
    stamp = js.get("t")
    return _num(js["c"]), (datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d")
                           if stamp else None), "free tier 60 req/min"


def c_twelvedata(t):
    key = os.environ.get("TWELVEDATA_API_KEY")
    if not key:
        return None, None, "no TWELVEDATA_API_KEY secret set"
    r = requests.get("https://api.twelvedata.com/time_series",
                     params={"symbol": t, "interval": "1day", "outputsize": 5,
                             "apikey": key}, headers=UA, timeout=TIMEOUT)
    if r.status_code != 200:
        return None, None, f"http {r.status_code}"
    js = r.json()
    vals = js.get("values")
    if not vals:
        return None, None, f"no values: {json.dumps(js)[:120]}"
    return _num(vals[0].get("close")), vals[0].get("datetime"), "free tier 800 req/day"


def c_tiingo(t):
    key = os.environ.get("TIINGO_API_KEY")
    if not key:
        return None, None, "no TIINGO_API_KEY secret set"
    r = requests.get(f"https://api.tiingo.com/tiingo/daily/{t}/prices",
                     params={"token": key}, headers={**UA, "Content-Type": "application/json"},
                     timeout=TIMEOUT)
    if r.status_code != 200:
        return None, None, f"http {r.status_code}"
    js = r.json()
    if not isinstance(js, list) or not js:
        return None, None, f"no rows: {json.dumps(js)[:120]}"
    return _num(js[-1].get("close")), str(js[-1].get("date", ""))[:10], "free tier 50 symbols/hr"


def c_fmp(t):
    key = os.environ.get("FMP_API_KEY")
    if not key:
        return None, None, "no FMP_API_KEY secret set"
    r = requests.get(f"https://financialmodelingprep.com/api/v3/historical-price-full/{t}",
                     params={"serietype": "line", "timeseries": 5, "apikey": key},
                     headers=UA, timeout=TIMEOUT)
    if r.status_code != 200:
        return None, None, f"http {r.status_code}"
    hist = (r.json() or {}).get("historical") or []
    if not hist:
        return None, None, "no historical rows"
    return _num(hist[0].get("close")), hist[0].get("date"), "free tier 250 req/day"


CANDIDATES = [
    ("stooq", False, c_stooq),
    ("nasdaq", False, c_nasdaq),
    ("stockanalysis", False, c_stockanalysis),
    ("yahoo_chart_CONTROL", False, c_yahoo_chart),
    ("alphavantage", True, c_alphavantage),
    ("finnhub", True, c_finnhub),
    ("twelvedata", True, c_twelvedata),
    ("tiingo", True, c_tiingo),
    ("fmp", True, c_fmp),
]


def main():
    argv = sys.argv[1:]
    ticker = argv[argv.index("--ticker") + 1] if "--ticker" in argv else "AAPL"
    out = {"ticker": ticker, "ran_at": NOW.isoformat(),
           "venue": os.environ.get("GITHUB_ACTIONS") and "github-actions" or "local",
           "candidates": {}}
    for name, needs_key, fn in CANDIDATES:
        try:
            close, date, note = fn(ticker)
        except Exception as e:  # noqa: BLE001
            close, date, note = None, None, f"{type(e).__name__}: {str(e)[:140]}"
        # A source that answers with a STALE price is the dangerous case: it looks like
        # corroboration and is not. Anything older than a week is recorded as answered
        # but not usable, with the age said out loud.
        age = None
        if date:
            try:
                age = (NOW.date() - datetime.strptime(date, "%Y-%m-%d").date()).days
            except ValueError:
                age = None
        usable = close is not None and (age is None or age <= 7)
        if close is not None and age is not None and age > 7:
            note = f"STALE: answered with a close {age} days old — not usable as a second print"
        out["candidates"][name] = {"answered": close is not None, "usable": usable,
                                   "close": close, "date": date, "age_days": age,
                                   "needs_key": needs_key, "note": note}
        print(f"{('ANSWERED' if usable else 'STALE   ') if close is not None else 'no      '}  {name:22} "
              f"{('close ' + str(close) + ' @ ' + str(date)) if close is not None else ''} "
              f"{'' if close is not None else '— ' + str(note)[:110]}")

    answered = {k: v for k, v in out["candidates"].items()
                if v.get("usable") and not k.endswith("_CONTROL")}
    out["summary"] = {
        "independent_sources_answering": sorted(answered),
        "count": len(answered),
        # Agreement matters more than liveness: a source that answers with the wrong
        # number is worse than one that refuses, because it looks like corroboration.
        "spread_pct": (round((max(v["close"] for v in answered.values())
                              - min(v["close"] for v in answered.values()))
                             / max(v["close"] for v in answered.values()) * 100, 3)
                       if len(answered) > 1 else None),
    }
    print(f"\nindependent sources answering: {out['summary']['count']} "
          f"{out['summary']['independent_sources_answering']}"
          + (f" · spread {out['summary']['spread_pct']}%"
             if out["summary"]["spread_pct"] is not None else ""))

    if "--dry-run" not in argv:
        p = ROOT / "data" / "health" / "source_bakeoff.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=1))
        print(f"wrote {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
