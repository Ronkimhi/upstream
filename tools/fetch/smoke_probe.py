#!/usr/bin/env python3
"""Weekly liveness probes for every external source Upstream depends on.

Required probes fail the run (exit 1 -> smoke.yml opens an issue).
Warn-only probes report but never fail (sources known to be flaky from
datacenter IPs, or T3 best-effort sources).

Also the radar staleness sentinel: once health/sessions.json says the radar
routine is LIVE, the newest RADAR line in data/ledger.md must be <= 3 weekdays
old — Actions is the loud sentinel for the LLM venue.

Usage: python3 tools/fetch/smoke_probe.py [--dry-run]
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import requests  # noqa: E402

from acis.config import (  # noqa: E402
    EDGAR_USER_AGENT, SEC_COMPANY_TICKERS_URL, SEC_SUBMISSIONS_URL,
    SEC_COMPANYFACTS_URL, STOOQ_DAILY_CSV_URL,
)

H = {"User-Agent": EDGAR_USER_AGENT}
NOW = datetime.now(timezone.utc)
results = {}


def probe(name, required, fn):
    try:
        ok, note = fn()
    except Exception as e:  # noqa: BLE001
        ok, note = False, f"{type(e).__name__}: {e}"[:200]
    results[name] = {"ok": bool(ok), "required": required, "note": note,
                     "checked_at": NOW.isoformat()}
    print(f"{'PASS' if ok else ('FAIL' if required else 'WARN')}  {name}: {note}")
    return ok


def p_company_tickers():
    r = requests.get(SEC_COMPANY_TICKERS_URL, headers=H, timeout=60)
    n = len(r.json()) if r.status_code == 200 else 0
    return n > 8000, f"{n} registrants"


def p_submissions():
    r = requests.get(SEC_SUBMISSIONS_URL.format(cik=320193), headers=H, timeout=60)
    sic = r.json().get("sic") if r.status_code == 200 else None
    return bool(sic), f"AAPL sic={sic}"


def p_fts():
    r = requests.get("https://efts.sec.gov/LATEST/search-index",
                     params={"q": '"co-packaged optics"', "forms": "10-K,10-Q,8-K"},
                     headers=H, timeout=60)
    if r.status_code != 200:
        return False, f"HTTP {r.status_code} — ENDPOINT DRIFT, update FTS_URL in fetch.py"
    total = r.json().get("hits", {}).get("total", {})
    n = total.get("value", 0) if isinstance(total, dict) else 0
    return n > 0, f"{n} hits for control query"


def p_companyfacts():
    r = requests.get(SEC_COMPANYFACTS_URL.format(cik=320193), headers=H, timeout=60)
    ok = r.status_code == 200 and "Revenues" in str(list(r.json().get("facts", {}).get("us-gaap", {}).keys())[:400])
    return ok, "AAPL revenue concepts present" if ok else f"HTTP {r.status_code} or concepts missing"


def p_stooq():
    r = requests.get(STOOQ_DAILY_CSV_URL.format(symbol="aapl.us"), headers=H, timeout=30)
    if r.status_code != 200 or not r.text.startswith("Date"):
        return False, f"HTTP {r.status_code}"
    last = r.text.strip().splitlines()[-1].split(",")[0]
    age = (NOW.replace(tzinfo=None) - datetime.strptime(last, "%Y-%m-%d")).days
    return age <= 7, f"last bar {last} ({age}d old)"


def p_gdelt():
    r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc",
                     params={"query": "Vertiv", "mode": "artlist", "format": "json", "maxrecords": 5},
                     headers=H, timeout=45)
    return r.status_code == 200, f"HTTP {r.status_code}"


def p_apewisdom():
    r = requests.get("https://apewisdom.io/api/v1.0/filter/wallstreetbets/page/1", headers=H, timeout=30)
    return r.status_code == 200 and "results" in r.text, f"HTTP {r.status_code}"


def p_stocktwits():
    r = requests.get("https://api.stocktwits.com/api/2/streams/symbol/AAPL.json", headers=H, timeout=30)
    return r.status_code in (200, 404), f"HTTP {r.status_code} (200 and 404 are both semantic)"


def p_pytrends():
    from pytrends.request import TrendReq
    t = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
    t.build_payload(["AAPL stock"], timeframe="today 3-m")
    df = t.interest_over_time()
    return df is not None and not df.empty, f"{0 if df is None else len(df)} rows"


def p_yfinance():
    import yfinance as yf
    h = yf.Ticker("AAPL").history(period="5d")
    return h is not None and not h.empty, f"{0 if h is None else len(h)} rows"


def p_yfinance_t3():
    import yfinance as yf
    h = yf.Ticker("6501.T").history(period="5d")
    return h is not None and not h.empty, f"Hitachi 6501.T {0 if h is None else len(h)} rows"


def p_radar_sentinel():
    sess = json.loads((ROOT / "data" / "health" / "sessions.json").read_text())
    status = sess.get("routine_status", {}).get("radar")
    if status != "LIVE":
        return True, f"radar routine_status={status} — sentinel armed only when LIVE"
    newest = None
    for line in (ROOT / "data" / "ledger.md").read_text().splitlines():
        if "| RADAR" in line and line[:2].isdigit():
            newest = line[:10]
    if not newest:
        return False, "routine LIVE but no RADAR ledger line exists"
    d = datetime.strptime(newest, "%Y-%m-%d")
    weekdays = sum(1 for i in range(1, (NOW.replace(tzinfo=None) - d).days + 1)
                   if (d + timedelta(days=i)).weekday() < 5)
    return weekdays <= 3, f"newest RADAR {newest} ({weekdays} weekdays ago)" + \
        ("" if weekdays <= 3 else " — RADAR ROUTINE HAS GONE SILENT")


def main():
    required = [
        ("sec_company_tickers", p_company_tickers),
        ("sec_submissions", p_submissions),
        ("edgar_fts", p_fts),
        ("sec_companyfacts", p_companyfacts),
        ("stooq", p_stooq),
        ("gdelt", p_gdelt),
        ("radar_sentinel", p_radar_sentinel),
    ]
    warn_only = [
        ("apewisdom", p_apewisdom),
        ("stocktwits", p_stocktwits),
        ("pytrends", p_pytrends),
        ("yfinance_us", p_yfinance),
        ("yfinance_t3", p_yfinance_t3),
    ]
    ok = True
    for name, fn in required:
        ok = probe(name, True, fn) and ok
    for name, fn in warn_only:
        probe(name, False, fn)

    if "--dry-run" not in sys.argv:
        hp = ROOT / "data" / "health" / "actions.json"
        health = json.loads(hp.read_text()) if hp.exists() else {}
        health["smoke"] = {"last_run": NOW.isoformat(), "probes": results}
        hp.write_text(json.dumps(health, indent=1))

    fails = [n for n, r in results.items() if r["required"] and not r["ok"]]
    print(f"smoke: {'OK' if not fails else 'FAILED: ' + ', '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
