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
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import requests  # noqa: E402

from acis.config import (  # noqa: E402
    EDGAR_USER_AGENT, SEC_COMPANY_TICKERS_URL, SEC_SUBMISSIONS_URL,
    SEC_COMPANYFACTS_URL, STOOQ_DAILY_CSV_URL, STOOQ_USER_AGENT,
)

H = {"User-Agent": EDGAR_USER_AGENT}
# stooq rejects the EDGAR agent with an HTML robots page (2026-08-29). A liveness probe
# sending the wrong header reports the endpoint dead when it is the request that is wrong,
# which is the failure mode a smoke test exists to prevent.
H_STOOQ = {"User-Agent": STOOQ_USER_AGENT}
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
    # Retry a 5xx once before calling it drift. On this probe's first live run
    # (2026-08-29) it reported "HTTP 500 — ENDPOINT DRIFT, update FTS_URL" in the same run
    # where GDELT returned 429, while a working FTS result sat in data/edgar/fts/ from
    # hours earlier — so the endpoint had not drifted, SEC was shedding load. A liveness
    # probe that names a permanent cause for a transient failure teaches the reader to
    # ignore it, which is worse than not probing. A 4xx is still reported immediately:
    # that one really does mean the request shape is wrong.
    last = None
    for attempt in (1, 2):
        r = requests.get("https://efts.sec.gov/LATEST/search-index",
                         params={"q": '"co-packaged optics"', "forms": "10-K,10-Q,8-K"},
                         headers=H, timeout=60)
        last = r
        if r.status_code == 200:
            break
        if r.status_code < 500:
            return False, (f"HTTP {r.status_code} — the request shape is being refused, "
                           f"check params/User-Agent against fetch.py FTS_URL")
        if attempt == 1:
            time.sleep(5)
    r = last
    if r.status_code != 200:
        return False, (f"HTTP {r.status_code} twice with a 5s gap — SEC is erroring, not "
                       f"necessarily drifted. Compare against data/edgar/fts/ timestamps "
                       f"before changing FTS_URL. Body starts {r.text.strip()[:80]!r}")
    total = r.json().get("hits", {}).get("total", {})
    n = total.get("value", 0) if isinstance(total, dict) else 0
    return n > 0, f"{n} hits for control query"


def p_companyfacts():
    r = requests.get(SEC_COMPANYFACTS_URL.format(cik=320193), headers=H, timeout=60)
    ok = r.status_code == 200 and "Revenues" in str(list(r.json().get("facts", {}).get("us-gaap", {}).keys())[:400])
    return ok, "AAPL revenue concepts present" if ok else f"HTTP {r.status_code} or concepts missing"


def p_stooq():
    r = requests.get(STOOQ_DAILY_CSV_URL.format(symbol="aapl.us"), headers=H_STOOQ, timeout=30)
    if r.status_code != 200 or not r.text.startswith("Date"):
        return False, f"HTTP {r.status_code}, body starts {r.text.strip()[:60]!r}"
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
        ("radar_sentinel", p_radar_sentinel),
    ]
    # REQUIRED means "if this is down the machine is broken". WARN means "known degraded,
    # written down, and nothing downstream silently pretends otherwise".
    #
    # stooq and gdelt moved to warn on 2026-08-29, the first time this file ever ran. Both
    # refuse the Actions venue from datacenter IPs — stooq answers every request with an
    # HTML robots page, gdelt rate-limits with 429 — and this file's own docstring already
    # carves out exactly that case. Leaving them REQUIRED would fail the weekly smoke run
    # forever on two conditions that are documented (docs/method.md section 1), that no
    # code change can fix, and that nothing downstream hides: prices come from yfinance,
    # every market file says SINGLE_SOURCE, and a dive resting on one is gate-required to
    # say so. An alarm that rings every week for a known reason trains its reader to
    # ignore the week it means something.
    warn_only = [
        ("stooq", p_stooq),
        ("gdelt", p_gdelt),
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
