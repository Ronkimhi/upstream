#!/usr/bin/env python3
"""Upstream data-plane fetcher. Runs ONLY in GitHub Actions (or manually).

Duties:
  1. Consume PENDING rows in data/requests.json by kind:
     prices | fundamentals | pcs | edgar_fts | edgar_doc
  2. On cron runs (--cron or GITHUB_EVENT_NAME=schedule), additionally:
     refresh price series for every non-ARCHIVED deep-dive ticker,
     evaluate armed scenario indicator checks (trips -> data/indicators.json),
     run the shadow +90d sweep, prune old FULFILLED requests.
  3. verified_zero discipline: an empty result is written only when the
     AAPL control probe succeeded in the same run; otherwise the request FAILS.

No Anthropic calls, no absolute paths, loud degradation. Stdlib + requests
(+ yfinance best-effort via acis.dual_source).
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import requests  # noqa: E402

from acis.config import (  # noqa: E402
    EDGAR_USER_AGENT, EDGAR_DELAY_SECONDS, SEC_COMPANY_TICKERS_URL,
    SEC_SUBMISSIONS_URL, SEC_COMPANYFACTS_URL, STOOQ_DAILY_CSV_URL,
)
from acis.dual_source import dual_source_price, _stooq_symbol  # noqa: E402

DATA = ROOT / "data"
SEC_HEADERS = {"User-Agent": EDGAR_USER_AGENT}
FTS_URL = "https://efts.sec.gov/LATEST/search-index"
MAX_DOC_CHARS = 60_000
NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")

_last_edgar = [0.0]


def edgar_wait():
    dt = time.time() - _last_edgar[0]
    if dt < EDGAR_DELAY_SECONDS:
        time.sleep(EDGAR_DELAY_SECONDS - dt)
    _last_edgar[0] = time.time()


def jload(p, default):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default


def jdump(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, indent=1))


def safe_name(t):
    return str(t).replace(".", "-").replace("/", "-")


# ---------------------------------------------------------------- control probe
_control = {"checked": False, "ok": False}


def control_ok():
    """One stooq AAPL probe per run: proves the price plumbing is alive so an
    empty result elsewhere can be stamped verified_zero instead of lying."""
    if not _control["checked"]:
        _control["checked"] = True
        try:
            r = requests.get(STOOQ_DAILY_CSV_URL.format(symbol="aapl.us"),
                             headers=SEC_HEADERS, timeout=30)
            _control["ok"] = r.status_code == 200 and r.text.startswith("Date") and len(r.text) > 2000
        except Exception:
            _control["ok"] = False
        print(f"control probe (stooq AAPL): {'OK' if _control['ok'] else 'FAILED'}")
    return _control["ok"]


# ---------------------------------------------------------------- tickers/cik
_company_tickers = None


def cik_for(ticker):
    global _company_tickers
    base = ticker.split(".")[0].upper().replace("-", "")
    if _company_tickers is None:
        edgar_wait()
        try:
            r = requests.get(SEC_COMPANY_TICKERS_URL, headers=SEC_HEADERS, timeout=60)
            _company_tickers = {v["ticker"].upper().replace("-", ""): v["cik_str"]
                                for v in r.json().values()} if r.status_code == 200 else {}
        except Exception:
            _company_tickers = {}
    return _company_tickers.get(base) or _company_tickers.get(ticker.upper().replace(".", "-"))


def tier_for(ticker, cik):
    if cik and "." not in ticker:
        return "T1"
    if cik:
        return "T2"
    return "T3"


# ---------------------------------------------------------------- prices
def fetch_series(ticker):
    """36-month close series: daily last 24mo, weekly before. stooq first,
    yfinance fallback. Returns (rows, source) or (None, None)."""
    rows, source = None, None
    try:
        r = requests.get(STOOQ_DAILY_CSV_URL.format(symbol=_stooq_symbol(ticker)),
                         headers=SEC_HEADERS, timeout=30)
        if r.status_code == 200 and r.text.startswith("Date"):
            lines = r.text.strip().splitlines()[1:]
            rows = []
            for ln in lines:
                p = ln.split(",")
                if len(p) >= 5 and p[0] and p[4] not in ("", "0"):
                    rows.append([p[0], round(float(p[4]), 4)])
            source = "stooq"
    except Exception as e:
        print(f"  stooq series failed for {ticker}: {e}")
    if not rows:
        try:
            import yfinance as yf
            h = yf.Ticker(ticker).history(period="3y", auto_adjust=False)
            if h is not None and not h.empty:
                rows = [[str(i.date()), round(float(v), 4)]
                        for i, v in h["Close"].dropna().items()]
                source = "yfinance"
        except Exception as e:
            print(f"  yfinance series failed for {ticker}: {e}")
    if not rows:
        return None, None
    cutoff36 = (NOW.replace(tzinfo=None) - __import__("datetime").timedelta(days=365 * 3)).strftime("%Y-%m-%d")
    cutoff24 = (NOW.replace(tzinfo=None) - __import__("datetime").timedelta(days=365 * 2)).strftime("%Y-%m-%d")
    rows = [r_ for r_ in rows if r_[0] >= cutoff36]
    old = [r_ for r_ in rows if r_[0] < cutoff24]
    recent = [r_ for r_ in rows if r_[0] >= cutoff24]
    weekly = [r_ for i, r_ in enumerate(old) if i % 5 == 0]
    return weekly + recent, source


def do_prices(ticker):
    cik = cik_for(ticker)
    ds = dual_source_price(ticker)
    rows, source = fetch_series(ticker)
    status = ds["status"].replace("-", "_")
    if status == "NO_DATA" and rows:
        status = "SINGLE_SOURCE"
    if status == "NO_DATA":
        if not control_ok():
            raise RuntimeError("price sources unreachable and control probe failed")
        m = {"ticker": ticker, "fetched_at": NOW.isoformat(), "tier": tier_for(ticker, cik),
             "price_status": "VERIFIED_ZERO",
             "probe": {"control_ticker": "AAPL", "control_ok": True, "checked_at": NOW.isoformat()},
             "prints": [], "series": None, "fundamentals": None, "pcs": None}
        jdump(DATA / "market" / f"{safe_name(ticker)}.json", m)
        return [f"data/market/{safe_name(ticker)}.json"]
    prev = jload(DATA / "market" / f"{safe_name(ticker)}.json", {})
    m = {
        "ticker": ticker, "fetched_at": NOW.isoformat(), "tier": tier_for(ticker, cik),
        "cik": cik, "price_status": status,
        "prints": ds["detail"].get("prints", []),
        "series": {"interval": "1d(24mo)+1w(prior)", "rows": rows, "source": source, "as_of": rows[-1][0]} if rows else prev.get("series"),
        "week52": {"low": min(r_[1] for r_ in rows[-252:]), "high": max(r_[1] for r_ in rows[-252:])} if rows else prev.get("week52"),
        "fundamentals": prev.get("fundamentals"),
        "pcs": prev.get("pcs"),
    }
    jdump(DATA / "market" / f"{safe_name(ticker)}.json", m)
    return [f"data/market/{safe_name(ticker)}.json"]


# ---------------------------------------------------------------- fundamentals
# Each field: (candidate us-gaap concepts in preference order, shape, unit).
#   shape "duration" = income statement / cash flow, needs a start and an end
#   shape "instant"  = balance sheet, a point in time
# The shape used to be inferred by testing concept membership in two hardcoded lists,
# which silently mis-filtered every balance-sheet concept added after those two. It is
# now declared per field, because the analyst needs eighteen of these, not four.
FACT_MAP = {
    "revenue": (["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                 "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
                "duration", "USD"),
    "net_income": (["NetIncomeLoss"], "duration", "USD"),
    "cash": (["CashAndCashEquivalentsAtCarryingValue"], "instant", "USD"),
    "total_debt": (["LongTermDebtNoncurrent", "LongTermDebt"], "instant", "USD"),
    # --- widened 2026-08-29 for the analyst (Piotroski / Beneish / Altman / reverse DCF)
    "gross_profit": (["GrossProfit"], "duration", "USD"),
    "cost_of_revenue": (["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold",
                         "CostOfServices"], "duration", "USD"),
    "operating_income": (["OperatingIncomeLoss"], "duration", "USD"),
    "operating_cashflow": (["NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
                           "duration", "USD"),
    "capex": (["PaymentsToAcquirePropertyPlantAndEquipment",
               "PaymentsToAcquireProductiveAssets"], "duration", "USD"),
    "depreciation": (["DepreciationDepletionAndAmortization",
                      "DepreciationAmortizationAndAccretionNet",
                      "DepreciationAndAmortization", "Depreciation"], "duration", "USD"),
    "sga": (["SellingGeneralAndAdministrativeExpense",
             "GeneralAndAdministrativeExpense"], "duration", "USD"),
    "total_assets": (["Assets"], "instant", "USD"),
    "current_assets": (["AssetsCurrent"], "instant", "USD"),
    "current_liabilities": (["LiabilitiesCurrent"], "instant", "USD"),
    "total_liabilities": (["Liabilities"], "instant", "USD"),
    "retained_earnings": (["RetainedEarningsAccumulatedDeficit"], "instant", "USD"),
    "equity": (["StockholdersEquity",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
               "instant", "USD"),
    "receivables": (["AccountsReceivableNetCurrent",
                     "ReceivablesNetCurrent"], "instant", "USD"),
    "inventory": (["InventoryNet"], "instant", "USD"),
    "ppe_net": (["PropertyPlantAndEquipmentNet"], "instant", "USD"),
    "shares": (["CommonStockSharesOutstanding", "CommonStockSharesIssued",
                "WeightedAverageNumberOfDilutedSharesOutstanding"], "instant", "shares"),
    # Same concepts as total_debt, kept as its own field because Piotroski and Beneish
    # both take a long-term-debt SERIES while total_debt is stored as a single latest row.
    "long_term_debt": (["LongTermDebtNoncurrent", "LongTermDebt"], "instant", "USD"),
}

# The annual series the quality block consumes. Must stay in step with
# `acis.quality.compute_quality`'s series_fields: a name here that quality does not read
# is dead weight, and a name quality reads that is missing here silently downgrades every
# score that needed it to PENDING_DATA. `tools/check_analyst.py` asserts the two agree.
QUALITY_FIELDS = ("revenue", "net_income", "operating_income", "gross_profit",
                  "cost_of_revenue", "operating_cashflow", "capex", "depreciation",
                  "sga", "total_assets", "current_assets", "current_liabilities",
                  "total_liabilities", "retained_earnings", "equity", "receivables",
                  "inventory", "ppe_net", "shares", "long_term_debt")


def do_fundamentals(ticker):
    cik = cik_for(ticker)
    if not cik:
        raise RuntimeError(f"no SEC CIK for {ticker} (T3 name: cite fundamentals from filings/IR via web, tagged INFERRED)")
    edgar_wait()
    r = requests.get(SEC_COMPANYFACTS_URL.format(cik=int(cik)), headers=SEC_HEADERS, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"companyfacts HTTP {r.status_code} for {ticker}")
    facts = r.json().get("facts", {})
    gaap = facts.get("us-gaap", {})
    dei = facts.get("dei", {})

    def pick(key, annual):
        """Latest 8 periods for one field. Instant facts have no duration to test."""
        names, shape, unit = FACT_MAP[key]
        for n in names:
            src = dei if (n == "EntityCommonStockSharesOutstanding") else gaap
            vals = src.get(n, {}).get("units", {}).get(unit) or []
            keep = {}
            for v in vals:
                form_ok = v.get("form") == "10-K" if annual else v.get("form") in ("10-Q", "10-K")
                if not form_ok:
                    continue
                if shape == "instant":
                    keep[v["end"]] = v["val"]
                    continue
                try:
                    frame_days = (datetime.fromisoformat(v["end"])
                                  - datetime.fromisoformat(v["start"])).days if "start" in v else None
                except Exception:
                    frame_days = None
                dur_ok = (frame_days and frame_days > 300) if annual \
                    else (frame_days and 60 < frame_days < 120)
                if dur_ok:
                    keep[v["end"]] = v["val"]
            if keep:
                return sorted(keep.items())[-8:]
        return []

    f = {
        "source": "sec-companyfacts", "cik": cik, "as_of": TODAY,
        "revenue_fy": pick("revenue", True),
        "revenue_q": pick("revenue", False),
        "net_income_fy": pick("net_income", True),
        "cash": pick("cash", False)[-1:] or None,
        "total_debt": pick("total_debt", False)[-1:] or None,
    }
    # Annual series for the quality block. A field with no rows stays absent rather than
    # empty, so `quality` names it as missing instead of scoring it as zero (method §1).
    found, attempted = [], []
    for key in QUALITY_FIELDS:
        attempted.append(key)
        rows = pick(key, True)
        if rows:
            f[f"{key}_fy"] = rows
            found.append(key)
    f["coverage"] = {"annual_fields_found": len(found), "annual_fields_attempted": len(attempted),
                     "missing": [k for k in attempted if k not in found]}
    if not f["revenue_fy"] and not f["revenue_q"]:
        if not control_ok():
            raise RuntimeError("empty companyfacts and control probe failed")
        f["verified_zero"] = {"note": "no revenue concepts found", "control_ok": True}
    path = DATA / "market" / f"{safe_name(ticker)}.json"
    m = jload(path, {"ticker": ticker, "price_status": "NO_DATA", "prints": [], "series": None, "pcs": None})
    m["fundamentals"] = f
    m["fetched_at"] = NOW.isoformat()
    m.setdefault("tier", tier_for(ticker, cik))
    jdump(path, m)
    return [f"data/market/{safe_name(ticker)}.json"]


# ---------------------------------------------------------------- pcs
def do_pcs(ticker):
    from acis.crowdedness import compute_pcs
    res = compute_pcs(ticker)
    if res["health"]["fetched"] == 0:
        raise RuntimeError("PCS: zero attention fields fetched — FAILED run, not quietness")
    path = DATA / "market" / f"{safe_name(ticker)}.json"
    m = jload(path, {"ticker": ticker, "price_status": "NO_DATA", "prints": [], "series": None, "fundamentals": None})
    m["pcs"] = res
    m["fetched_at"] = NOW.isoformat()
    m.setdefault("tier", tier_for(ticker, cik_for(ticker)))
    jdump(path, m)
    return [f"data/market/{safe_name(ticker)}.json"]


# ---------------------------------------------------------------- quality
def do_quality(ticker):
    """Piotroski / Beneish / Altman / reverse-DCF implied growth for one name.

    Pure computation over what is already on disk: it fetches nothing. It therefore
    fails loudly rather than quietly when `fundamentals` has not been widened yet,
    because a quality block computed from four line items would be confidently wrong.
    """
    from acis.quality import compute_quality
    path = DATA / "market" / f"{safe_name(ticker)}.json"
    m = jload(path, None)
    if not m:
        raise RuntimeError(f"no data/market/{safe_name(ticker)}.json — request prices and "
                           "fundamentals first")
    fund = m.get("fundamentals")
    if not fund:
        raise RuntimeError("no fundamentals block — request kind 'fundamentals' first")

    def last_val(rows):
        return rows[-1][1] if rows else None

    shares = last_val(fund.get("shares_fy"))
    price = None
    if m.get("series") and m["series"].get("rows"):
        price = m["series"]["rows"][-1][1]
    elif m.get("prints"):
        price = m["prints"][0].get("close")
    market_cap = shares * price if (shares and price) else None
    debt, cash = last_val(fund.get("total_debt")), last_val(fund.get("cash"))
    ev = None
    if market_cap is not None and debt is not None and cash is not None:
        ev = market_cap + debt - cash

    q = compute_quality(fund, market_cap=market_cap, enterprise_value=ev, as_of=TODAY)
    q["price_used"] = {"value": price, "source": (m.get("series") or {}).get("source"),
                       "as_of": (m.get("series") or {}).get("as_of")}
    q["shares_used"] = {"value": shares,
                        "as_of": fund.get("shares_fy", [[None]])[-1][0] if fund.get("shares_fy") else None}
    scored = sum(1 for k in ("piotroski", "beneish", "altman")
                 if q[k].get("score") is not None)
    if scored == 0 and q["reverse_dcf"].get("implied_fcf_cagr") is None:
        # Not an exception: a thin filer is a real state. But it must read as a failure
        # to score, never as a clean pass over nothing (method §9).
        q["state"] = "NOTHING_SCORED"
        print(f"  quality {ticker}: 0 of 4 scores computed — "
              f"missing {fund.get('coverage', {}).get('missing')}")
    else:
        q["state"] = "SCORED"
    m["quality"] = q
    m["fetched_at"] = NOW.isoformat()
    jdump(path, m)
    print(f"  quality {ticker}: {scored}/3 scores + reverse DCF "
          f"{q['reverse_dcf'].get('state')}")
    return [f"data/market/{safe_name(ticker)}.json"]


# ---------------------------------------------------------------- insider (Form 4)
def do_insider(ticker, lookback_days=365):
    """Form 4 insider transactions via edgartools.

    Adopted 2026-08-29 (see docs/analyst-sources.md). This is the one place edgartools
    earns its install: hand-parsing ownership XML is exactly the work it exists to remove.

    The extraction is deliberately defensive. Its object shape has not been exercised
    against a live filing in this repo yet, so when the expected attributes are absent it
    writes a `shape_probe` naming what the object actually had, instead of guessing a
    mapping and silently recording wrong numbers. The first Actions run either returns
    data or tells us precisely what to bind to.
    """
    cik = cik_for(ticker)
    if not cik:
        raise RuntimeError(f"no SEC CIK for {ticker} (Form 4 is a US-filer surface)")
    try:
        import edgar
    except ImportError as e:
        raise RuntimeError(f"edgartools not installed in this job: {e}")
    edgar.set_identity(EDGAR_USER_AGENT)
    cutoff = (NOW.replace(tzinfo=None)
              - __import__("datetime").timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    # Resolution diagnostics. The 2026-08-29 first live run returned zero rows in ~2ms,
    # which is far too fast to have touched SEC: the loop body never ran, and the only
    # probe I had fired inside it, so the result was an uninformative silent zero. The
    # resolution steps are now recorded separately from the parse steps.
    diag = {"company_resolved": None, "filings_listed": None, "parse_errors": []}
    try:
        company = edgar.Company(ticker)
        diag["company_resolved"] = str(getattr(company, "name", None) or company)[:120]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"edgartools could not resolve {ticker}: {e}")
    try:
        filings = list(company.get_filings(form="4"))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"edgartools Form 4 listing failed for {ticker}: {e}")
    diag["filings_listed"] = len(filings)

    rows, probe = [], None
    for filing in filings[:40]:
        fdate = str(getattr(filing, "filing_date", "") or "")
        if fdate and fdate < cutoff:
            break
        try:
            ob = filing.obj()
        except Exception as e:  # noqa: BLE001
            diag["parse_errors"].append(f"{fdate}: {str(e)[:120]}")
            print(f"  Form 4 parse failed ({fdate}): {e}")
            continue
        # Bound 2026-08-29 against the shape probe the previous run wrote: `market_trades`
        # EXISTS on the object but is None for filings with no open-market trade, so the
        # first binding read as "no data" on 40 consecutive filings that parsed fine.
        # These are the real accessors, tried most-specific first.
        txns, via = None, None
        for name in ("common_stock_purchases", "common_stock_sales", "market_trades"):
            v = getattr(ob, name, None)
            if v is None:
                continue
            if hasattr(v, "__len__") and len(v) == 0:
                continue
            txns, via = v, name
            break
        if txns is None:
            for meth in ("get_transaction_activities", "to_dataframe"):
                fn = getattr(ob, meth, None)
                if callable(fn):
                    try:
                        v = fn()
                    except Exception as e:  # noqa: BLE001
                        diag["parse_errors"].append(f"{fdate} {meth}: {str(e)[:100]}")
                        continue
                    if v is not None and (not hasattr(v, "__len__") or len(v)):
                        txns, via = v, meth
                        break
        if txns is None:
            if probe is None:
                probe = sorted(a for a in dir(ob) if not a.startswith("_"))[:40]
            continue
        diag.setdefault("bound_via", {})
        diag["bound_via"][via] = diag["bound_via"].get(via, 0) + 1
        try:
            recs = txns.to_dict("records") if hasattr(txns, "to_dict") else list(txns)
        except Exception as e:  # noqa: BLE001
            diag["parse_errors"].append(f"{fdate} to_dict: {str(e)[:100]}")
            recs = []
        for t in recs:
            rows.append({"filing_date": fdate, "via": via,
                         "insider": str(getattr(ob, "insider_name", None)
                                        or getattr(ob, "reporting_owner_name", None) or "")[:120],
                         "raw": {k: (str(v)[:60] if v is not None else None)
                                 for k, v in list(dict(t).items())[:12]}})
    # verified_zero needs a control on the SAME plane. stooq-AAPL proves prices work and
    # says nothing about whether EDGAR ownership data is reachable, so it cannot certify
    # an empty Form 4 result. Probe EDGAR with a filer that always has Form 4s.
    if not rows and diag["filings_listed"]:
        raise RuntimeError(
            f"{ticker}: {diag['filings_listed']} Form 4 filings listed and "
            f"{min(len(filings), 40)} examined, but 0 transaction rows extracted. That is an "
            f"EXTRACTION failure, not a verified zero — a company with filings has trades. "
            f"shape probe: {probe} | diagnostics: {diag}")
    edgar_control = None
    if not rows:
        try:
            edgar_control = len(list(edgar.Company("AAPL").get_filings(form="4"))) > 0
        except Exception as e:  # noqa: BLE001
            diag["parse_errors"].append(f"edgar control probe: {str(e)[:120]}")
            edgar_control = False
        print(f"  EDGAR control probe (AAPL Form 4): {'OK' if edgar_control else 'FAILED'}")
        if not edgar_control:
            raise RuntimeError(
                f"zero Form 4 rows for {ticker} AND the EDGAR control probe failed — "
                f"cannot tell an empty result from a dead path. diagnostics: {diag}")
    out = {"ticker": ticker, "cik": cik, "fetched_at": NOW.isoformat(),
           "window": [cutoff, TODAY], "row_count": len(rows), "rows": rows[:200],
           "diagnostics": diag,
           "health": {"filings_examined": min(len(filings), 40),
                      **({"verified_zero": True, "probe": "edgar-AAPL-form4-ok"}
                         if (not rows and edgar_control) else {})},
           **({"shape_probe": probe} if probe else {})}
    path = DATA / "market" / f"{safe_name(ticker)}.json"
    m = jload(path, {"ticker": ticker, "price_status": "NO_DATA", "prints": [],
                     "series": None, "fundamentals": None, "pcs": None})
    m["insider"] = out
    m["fetched_at"] = NOW.isoformat()
    m.setdefault("tier", tier_for(ticker, cik))
    jdump(path, m)
    print(f"  insider {ticker}: {len(rows)} transaction row(s)"
          + (f" | SHAPE PROBE written: {probe}" if probe else ""))
    return [f"data/market/{safe_name(ticker)}.json"]


# ---------------------------------------------------------------- EDGAR FTS
def do_edgar_fts(query, forms=None, lookback_days=365):
    edgar_wait()
    params = {"q": query, "forms": forms or "10-K,10-Q,8-K"}
    start = (NOW.replace(tzinfo=None) - __import__("datetime").timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    params["dateRange"] = "custom"
    params["startdt"] = start
    params["enddt"] = TODAY
    r = requests.get(FTS_URL, params=params, headers=SEC_HEADERS, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"EDGAR FTS HTTP {r.status_code} (endpoint drift? smoke.yml pins this)")
    js = r.json()
    hits = []
    for h in js.get("hits", {}).get("hits", []):
        src = h.get("_source", {})
        names = src.get("display_names", [])
        adsh = h.get("_id", "").split(":")[0]
        ciks = src.get("ciks") or []
        roots = src.get("root_forms") or []
        tick = None
        if names:
            mt = re.search(r"\(([A-Z][A-Z0-9.\-]{0,9})\)\s+\(CIK", names[0])
            tick = mt.group(1) if mt else None
        hits.append({
            "entity_name": names[0].split("  (")[0].strip() if names else None,
            "cik": int(ciks[0]) if ciks else None,
            "ticker": tick,
            "form": roots[0] if roots else src.get("file_type"),
            "file_type": src.get("file_type"),
            "filing_date": src.get("file_date"),
            "accession": adsh,
        })
    total = js.get("hits", {}).get("total", {})
    n = total.get("value", len(hits)) if isinstance(total, dict) else len(hits)
    if not hits and not control_ok():
        raise RuntimeError("zero FTS hits and control probe failed — cannot stamp verified_zero")
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:60]
    out = {"query": query, "forms": params["forms"], "window": [start, TODAY],
           "fetched_at": NOW.isoformat(), "hit_total": n, "hits": hits[:100],
           "health": {"status_code": r.status_code, "hit_count": len(hits),
                      **({"verified_zero": True, "probe": "stooq-AAPL-ok"} if not hits else {})}}
    jdump(DATA / "edgar" / "fts" / f"{slug}.json", out)
    return [f"data/edgar/fts/{slug}.json"]


# ---------------------------------------------------------------- EDGAR doc
def _strip_html(html):
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&#?[a-zA-Z0-9]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def do_edgar_doc(ticker, lookback_days=200):
    cik = cik_for(ticker)
    if not cik:
        raise RuntimeError(f"no SEC CIK for {ticker}")
    edgar_wait()
    r = requests.get(SEC_SUBMISSIONS_URL.format(cik=int(cik)), headers=SEC_HEADERS, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"submissions HTTP {r.status_code}")
    recent = r.json().get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    items = recent.get("items", [""] * len(forms))
    cutoff = (NOW.replace(tzinfo=None) - __import__("datetime").timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    cand = None
    for i, f in enumerate(forms):
        if dates[i] >= cutoff and f == "8-K" and "2.02" in str(items[i] if i < len(items) else ""):
            cand = i
            break
    if cand is None:
        for i, f in enumerate(forms):
            if f == "10-Q" and dates[i] >= cutoff:
                cand = i
                break
    if cand is None:
        raise RuntimeError(f"no earnings document in {lookback_days}d window (fail closed — no doc, no nuggets)")
    url = "https://www.sec.gov/Archives/edgar/data/{}/{}/{}".format(int(cik), accs[cand].replace("-", ""), docs[cand])
    edgar_wait()
    dr = requests.get(url, headers=SEC_HEADERS, timeout=60)
    if dr.status_code != 200:
        raise RuntimeError(f"document HTTP {dr.status_code}")
    text = _strip_html(dr.text)[:MAX_DOC_CHARS]
    # An 8-K primary doc is often just the cover; the earnings text lives in
    # the EX-99 press-release exhibit. Upgrade to it when the cover is thin.
    if forms[cand] == "8-K" and len(text) < 8000:
        try:
            base = "https://www.sec.gov/Archives/edgar/data/{}/{}".format(int(cik), accs[cand].replace("-", ""))
            edgar_wait()
            idx = requests.get(base + "/index.json", headers=SEC_HEADERS, timeout=60).json()
            exhibits = [it["name"] for it in idx.get("directory", {}).get("item", [])
                        if re.search(r"ex.{0,7}?99", it.get("name", ""), re.I) and it["name"].endswith((".htm", ".html"))]
            if not exhibits:  # index.json is flaky (sometimes truncated) — parse the dir listing
                edgar_wait()
                listing = requests.get(base + "/", headers=SEC_HEADERS, timeout=60).text
                names = set(re.findall(r'href="[^"]*/([^"/]+\.htm[l]?)"', listing))
                exhibits = [n for n in sorted(names) if re.search(r"ex.{0,7}?99", n, re.I)]
            best = ""
            for name in exhibits[:4]:
                edgar_wait()
                er = requests.get(base + "/" + name, headers=SEC_HEADERS, timeout=60)
                if er.status_code == 200:
                    et = _strip_html(er.text)[:MAX_DOC_CHARS]
                    if len(et) > len(best):
                        best, url = et, base + "/" + name
            if len(best) > len(text):
                text = best
        except Exception as e:
            print(f"  EX-99 upgrade failed for {ticker} (keeping cover doc): {e}")
    if len(text) < 500:
        raise RuntimeError("document too short — stub page, fail closed")
    out = {"ticker": ticker, "cik": cik, "fetched_at": NOW.isoformat(),
           "form": forms[cand], "filing_date": dates[cand], "accession": accs[cand],
           "url": url, "chars": len(text), "text": text}
    jdump(DATA / "edgar" / "docs" / f"{safe_name(ticker)}.json", out)
    return [f"data/edgar/docs/{safe_name(ticker)}.json"]


# ---------------------------------------------------------------- cron duties
def last_close(ticker):
    m = jload(DATA / "market" / f"{safe_name(ticker)}.json", None)
    if m and m.get("series") and m["series"].get("rows"):
        return m["series"]["rows"][-1][1], m["series"]["rows"][-1][0]
    rows, _src = fetch_series(ticker)
    if rows:
        return rows[-1][1], rows[-1][0]
    return None, None


def close_at(ticker, date):
    m = jload(DATA / "market" / f"{safe_name(ticker)}.json", None)
    rows = (m or {}).get("series", {}).get("rows") if m and m.get("series") else None
    if not rows:
        rows, _src = fetch_series(ticker)
    if not rows:
        return None
    best = None
    for r_ in rows:
        if r_[0] <= date:
            best = r_[1]
        else:
            break
    return best


def eval_indicators(trips):
    inds = jload(DATA / "indicators.json", {"trips": []})
    seen = {(t["chain"], t["scenario"], t["indicator"]) for t in inds["trips"]}
    for cf in sorted((DATA / "chains").glob("*.json")):
        c = jload(cf, {})
        for sc in c.get("scenarios", []):
            for ind in sc.get("leading_indicators", []):
                chk = ind.get("check")
                if not (chk and ind.get("armed")):
                    continue
                key = (c["id"], sc["id"], ind["indicator"])
                if key in seen or ind.get("tripped_at"):
                    continue
                px, asof = last_close(chk["ticker"])
                if px is None:
                    print(f"  indicator {key}: no price for {chk['ticker']} — skipped loudly")
                    continue
                op = chk["op"]
                hit = (op == ">=" and px >= chk["level"]) or (op == "<=" and px <= chk["level"]) or \
                      (op == ">" and px > chk["level"]) or (op == "<" and px < chk["level"])
                if hit:
                    trip = {"chain": c["id"], "scenario": sc["id"], "indicator": ind["indicator"],
                            "ticker": chk["ticker"], "op": op, "level": chk["level"],
                            "seen": px, "as_of": asof, "tripped_at": TODAY}
                    inds["trips"].append(trip)
                    trips.append(trip)
                    print(f"  INDICATOR TRIPPED: {trip}")
    jdump(DATA / "indicators.json", inds)


def shadow_sweep():
    book = jload(DATA / "shadow" / "book.json", {"rows": []})
    results = jload(DATA / "shadow" / "results.json", {})
    changed = 0
    for row in book.get("rows", []):
        if row["id"] in results or row.get("review_at", "9999") > TODAY:
            continue
        tk_then = (row.get("spot") or {}).get("value")
        tk_now, _ = last_close(row["ticker"])
        spy_then = close_at("SPY", row["verdict_date"])
        spy_now, _ = last_close("SPY")
        if None in (tk_then, tk_now, spy_then, spy_now):
            print(f"  shadow {row['id']}: missing a leg (loud skip, retried next cron)")
            continue
        delta = round(((tk_now / tk_then) - (spy_now / spy_then)) * 100, 1)
        call = "RIGHT" if delta < -2 else ("WRONG" if delta > 2 else "MIXED")
        results[row["id"]] = {"ticker_return_90d": round((tk_now / tk_then - 1) * 100, 1),
                              "spy_return_90d": round((spy_now / spy_then - 1) * 100, 1),
                              "delta_pct": delta, "call": call,
                              "computed_at": NOW.isoformat(), "price_status": "best-effort"}
        changed += 1
    if changed:
        jdump(DATA / "shadow" / "results.json", results)
    print(f"shadow sweep: {changed} row(s) graded")


def refresh_dive_tickers(counts):
    tickers = set()
    for sf in sorted((DATA / "stocks").glob("*.json")):
        st = jload(sf, {})
        if st.get("status") != "ARCHIVED" and not st.get("fixture"):
            tickers.add(st["ticker"])
    tickers.add("SPY")  # shadow benchmark stays fresh
    for t in sorted(tickers):
        try:
            do_prices(t)
            counts["refreshed"] += 1
        except Exception as e:
            counts["errors"] += 1
            print(f"  cron refresh failed for {t}: {e}")


# ---------------------------------------------------------------- main
def main():
    is_cron = "--cron" in sys.argv or os.environ.get("GITHUB_EVENT_NAME") == "schedule"
    req_path = DATA / "requests.json"
    reqs = jload(req_path, {"version": 1, "requests": []})
    counts = {"processed": 0, "fulfilled": 0, "failed": 0, "refreshed": 0, "errors": 0}
    trips = []

    for req in reqs.get("requests", []):
        if req.get("status") != "PENDING":
            continue
        counts["processed"] += 1
        try:
            k = req["kind"]
            if k == "prices":
                wrote = do_prices(req["ticker"])
            elif k == "fundamentals":
                wrote = do_fundamentals(req["ticker"])
            elif k == "pcs":
                wrote = do_pcs(req["ticker"])
            elif k == "quality":
                wrote = do_quality(req["ticker"])
            elif k == "insider":
                wrote = do_insider(req["ticker"], req.get("lookback_days") or 365)
            elif k == "edgar_fts":
                wrote = do_edgar_fts(req["query"], req.get("forms"), req.get("lookback_days") or 365)
            elif k == "edgar_doc":
                wrote = do_edgar_doc(req["ticker"], req.get("lookback_days") or 200)
            else:
                raise RuntimeError(f"unknown kind {k}")
            req["status"] = "FULFILLED"
            req["wrote"] = wrote
            req["fulfilled_at"] = NOW.isoformat()
            counts["fulfilled"] += 1
            print(f"FULFILLED {req['id']} ({k} {req.get('ticker') or req.get('query')})")
        except Exception as e:
            req["status"] = "FAILED"
            req["note"] = str(e)[:300]
            counts["failed"] += 1
            print(f"FAILED {req['id']}: {e}")

    feeds_due = is_cron or "--feeds" in sys.argv or \
        os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    feeds_summary = None
    if feeds_due:
        try:
            from feeds import run_feeds
            feeds_summary = run_feeds()
        except Exception as e:  # feeds must never block the market/EDGAR plane
            counts["errors"] += 1
            feeds_summary = {"error": str(e)[:200]}
            print(f"feeds: run FAILED: {e}")

    if is_cron:
        refresh_dive_tickers(counts)
        eval_indicators(trips)
        shadow_sweep()
        cutoff = (NOW.replace(tzinfo=None) - __import__("datetime").timedelta(days=30)).isoformat()
        before = len(reqs["requests"])
        reqs["requests"] = [r_ for r_ in reqs["requests"]
                            if not (r_.get("status") == "FULFILLED" and (r_.get("fulfilled_at") or "9999") < cutoff)]
        if len(reqs["requests"]) != before:
            print(f"pruned {before - len(reqs['requests'])} old FULFILLED request(s)")

    jdump(req_path, reqs)

    health = jload(DATA / "health" / "actions.json", {})
    fetch_h = health.setdefault("fetch", {"last_run": None, "runs": []})
    fetch_h["last_run"] = NOW.isoformat()
    fetch_h["runs"] = (fetch_h.get("runs", []) + [{
        "ts": NOW.isoformat(), "event": os.environ.get("GITHUB_EVENT_NAME", "manual"),
        **counts, "trips": len(trips)}])[-14:]
    if feeds_summary is not None:
        health["feeds"] = {"last_run": NOW.isoformat(), "summary": feeds_summary}
    jdump(DATA / "health" / "actions.json", health)

    if trips:
        (ROOT / "trips.txt").write_text("\n".join(
            f"{t['chain']} / {t['scenario']}: {t['indicator']} — {t['ticker']} {t['op']} {t['level']} (seen {t['seen']} as of {t['as_of']})"
            for t in trips))

    print(f"fetch summary: processed={counts['processed']} fulfilled={counts['fulfilled']} "
          f"failed={counts['failed']} cron_refreshed={counts['refreshed']} errors={counts['errors']} trips={len(trips)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
