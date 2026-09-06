#!/usr/bin/env python3
"""Upstream data-plane fetcher. Runs ONLY in GitHub Actions (or manually).

Duties:
  1. Consume PENDING rows in data/requests.json by kind:
     prices | fundamentals | pcs | edgar_fts | edgar_doc
  2. On cron runs (--cron or GITHUB_EVENT_NAME=schedule), additionally:
     refresh price series for every non-ARCHIVED deep-dive ticker,
     evaluate armed scenario indicator checks (trips -> data/indicators.json),
     run the shadow +90d sweep, prune old FULFILLED requests.
  3. verified_zero discipline: an empty result is written only when a control probe
     ON THE SAME DATA PLANE succeeded in the same run; otherwise the request FAILS.
     Prices use a yfinance probe (the plane prices actually come from); every EDGAR
     surface (fundamentals, FTS, insider) uses its own edgar_*_ok() probe, because a
     price probe cannot certify EDGAR reachability.

No Anthropic calls, no absolute paths, loud degradation. Stdlib + requests
(+ yfinance best-effort via acis.dual_source).
"""
import dataclasses
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import requests  # noqa: E402

from acis.config import (  # noqa: E402
    EDGAR_USER_AGENT, EDGAR_DELAY_SECONDS, SEC_COMPANY_TICKERS_URL,
    SEC_SUBMISSIONS_URL, SEC_COMPANYFACTS_URL, STOOQ_DAILY_CSV_URL,
    STOOQ_USER_AGENT,
)
from acis.dual_source import dual_source_price, _stooq_symbol  # noqa: E402

DATA = ROOT / "data"
SEC_HEADERS = {"User-Agent": EDGAR_USER_AGENT}
FTS_URL = "https://efts.sec.gov/LATEST/search-index"
MAX_DOC_CHARS = 60_000
# The web evidence store (tools/evidence_store.py). A browser-like agent because the
# sources cited here are news sites, regulators and company IR pages, not EDGAR; the EDGAR
# agent string is reserved for data.sec.gov by SEC policy.
WEB_USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 UpstreamResearch/1.0")
MAX_WEB_CHARS = 200_000
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
_control = {"checked": False, "ok": False, "reason": None}


def control_ok():
    """One AAPL probe per run on the yfinance plane: proves the price plumbing is
    alive so an empty result elsewhere can be stamped verified_zero instead of lying.

    Probes yfinance because that is the plane prices are actually fetched from (the
    primary print leg and the only series leg on disk). The stooq probe this replaces
    (2026-09-01) could never pass once stooq began serving a JS proof-of-work challenge
    to every non-browser client, so any ticker where all legs came back empty failed as
    "price sources unreachable" even while yfinance was healthy — plain-US MMC among
    the casualties. Same lesson as the EDGAR probes: a zero is certified on the plane
    it was read from, and a dead probe fails closed, which is also why its breakage
    stays invisible until someone looks.
    """
    if not _control["checked"]:
        _control["checked"] = True
        try:
            import yfinance as yf
            hist = yf.Ticker("AAPL").history(period="5d")
            _control["ok"] = hist is not None and not hist.empty and "Close" in hist.columns
            _control["reason"] = None if _control["ok"] else "yfinance AAPL history came back empty"
        except Exception as e:  # noqa: BLE001
            _control["ok"] = False
            _control["reason"] = f"{type(e).__name__}: {str(e)[:120]}"
        print(f"control probe (yfinance AAPL): {'OK' if _control['ok'] else 'FAILED'}"
              + (f" — {_control['reason']}" if _control.get("reason") else ""))
    return _control["ok"]


# --------------------------------------------------- EDGAR-plane control probes
# A verified_zero must be certified on the SAME plane it was read from. control_ok()
# probes stooq for a PRICE and says nothing about whether SEC EDGAR is reachable, so it
# cannot certify an empty EDGAR result — on 2026-08-29 an insider run stamped
# verified_zero against a passing price probe while the extraction was in fact broken.
# Each SEC endpoint gets its own cached probe against a filer/query known to be non-empty.
_edgar_probes = {}


def _edgar_probe(key, fn, label, plane="EDGAR"):
    """One cached control probe per (plane, surface), printed with the plane it probed.

    `plane` exists because the yfinance statements fallback needs the same discipline on a
    different data plane, and a Yahoo probe printed as "EDGAR control probe" would be the
    exact class of misleading output the verified_zero rule exists to prevent.
    """
    if key not in _edgar_probes:
        try:
            _edgar_probes[key] = bool(fn())
        except Exception as e:  # noqa: BLE001
            _edgar_probes[key] = False
            print(f"  {plane} probe {label} raised: {str(e)[:120]}")
        print(f"  {plane} control probe ({label}): "
              f"{'OK' if _edgar_probes[key] else 'FAILED'}")
    return _edgar_probes[key]


def edgar_facts_ok():
    """AAPL companyfacts returns revenue — proves data.sec.gov XBRL is alive. Certifies an
    empty `fundamentals` result."""
    def probe():
        edgar_wait()
        r = requests.get(SEC_COMPANYFACTS_URL.format(cik=320193), headers=SEC_HEADERS, timeout=60)
        if r.status_code != 200:
            return False
        gaap = r.json().get("facts", {}).get("us-gaap", {})
        return bool(gaap.get("Revenues") or gaap.get("RevenueFromContractWithCustomerExcludingAssessedTax"))
    return _edgar_probe("facts", probe, "AAPL companyfacts")


def edgar_fts_ok():
    """A known-common FTS query returns hits — proves efts.sec.gov is alive. Certifies an
    empty full-text-search result."""
    def probe():
        edgar_wait()
        r = requests.get(FTS_URL, params={"q": "revenue", "forms": "10-K"},
                         headers=SEC_HEADERS, timeout=60)
        if r.status_code != 200:
            return False
        total = r.json().get("hits", {}).get("total", {})
        return (total.get("value", 0) if isinstance(total, dict) else 0) > 0
    return _edgar_probe("fts", probe, "FTS revenue query")


def edgar_forms_ok():
    """AAPL Form 4 count > 0 — proves the ownership/submissions path is alive. Certifies an
    empty `insider` result."""
    def probe():
        import edgar
        edgar.set_identity(EDGAR_USER_AGENT)
        return len(list(edgar.Company("AAPL").get_filings(form="4"))) > 0
    return _edgar_probe("forms", probe, "AAPL Form 4")


# ---------------------------------------------------------------- tickers/cik
_company_tickers = None
_cik_overrides = {}


def register_cik_override(ticker, cik):
    """An explicit `cik` on a request row wins over any table lookup. This is how a
    legitimate ADR-linked foreign listing (BP.L -> BP plc's 20-F filer) keeps its SEC
    leg now that a suffixed ticker can no longer infer one: the link is asserted by
    the session that queued the row, with evidence, never guessed from a prefix."""
    try:
        _cik_overrides[str(ticker).upper()] = int(cik)
    except (TypeError, ValueError):
        print(f"  ignoring non-numeric cik override for {ticker}: {cik!r}")


def cik_for(ticker):
    """SEC CIK for a ticker, or None.

    A dotted ticker is either a US share class (BRK.B — the SEC table spells it
    BRK-B) or a foreign listing's exchange suffix (ENR.DE). Only the exact
    class-share spelling may match. The old code stripped everything after the
    first "." and matched the stub against the SEC's US ticker table, so a foreign
    listing silently adopted an unrelated US filer's CIK (ENR.DE -> Energizer,
    BA.L -> Boeing) and do_fundamentals then wrote ANOTHER COMPANY's companyfacts
    into the foreign listing's file, tagged VERIFIED (backlog 2026-08-31,
    listing-identity-normalization). A suffixed ticker now resolves only via an
    explicit request-row `cik` override; with none, it gets no CIK and the vendor
    leg — the posture method §6 prescribes for T3 names.
    """
    global _company_tickers
    t = str(ticker).upper()
    if t in _cik_overrides:
        return _cik_overrides[t]
    if _company_tickers is None:
        edgar_wait()
        try:
            r = requests.get(SEC_COMPANY_TICKERS_URL, headers=SEC_HEADERS, timeout=60)
            _company_tickers = {}
            if r.status_code == 200:
                for v in r.json().values():
                    tk = v["ticker"].upper()
                    _company_tickers[tk] = v["cik_str"]                    # raw: BRK-B
                    _company_tickers.setdefault(tk.replace("-", ""), v["cik_str"])  # BRKB
        except Exception:
            _company_tickers = {}
    if "." in t:
        return _company_tickers.get(t.replace(".", "-"))
    return _company_tickers.get(t) or _company_tickers.get(t.replace("-", ""))


def tier_for(ticker, cik):
    if cik and "." not in ticker:
        return "T1"
    if cik:
        return "T2"
    return "T3"


# ---------------------------------------------------------------- prices
def fetch_series(ticker):
    """36-month close series: daily last 24mo, weekly before. yfinance first, stooq
    fallback. Returns (rows, source) or (None, None).

    Order flipped 2026-09-01. Stooq had been "primary" since the repo was created and had
    never once answered: it serves an HTML page to non-browser clients (smoke probe
    `stooq`, health/actions.json), so every series on disk already reads source
    "yfinance" and the primary was a 30-second timeout paid on every ticker. The print leg
    (acis/dual_source.py) made the same swap the same day.
    """
    rows, source = None, None
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
        try:
            # STOOQ_USER_AGENT, not SEC_HEADERS — see control_ok().
            r = requests.get(STOOQ_DAILY_CSV_URL.format(symbol=_stooq_symbol(ticker)),
                             headers={"User-Agent": STOOQ_USER_AGENT}, timeout=15)
            if r.status_code == 200 and r.text.startswith("Date"):
                lines = r.text.strip().splitlines()[1:]
                rows = []
                for ln in lines:
                    p = ln.split(",")
                    if len(p) >= 5 and p[0] and p[4] not in ("", "0"):
                        rows.append([p[0], round(float(p[4]), 4)])
                source = "stooq"
            else:
                print(f"  stooq series unavailable for {ticker}: http {r.status_code}, "
                      f"body starts {r.text.strip()[:60]!r}")
        except Exception as e:
            print(f"  stooq series failed for {ticker}: {e}")
    if not rows:
        return None, None
    cutoff36 = (NOW.replace(tzinfo=None) - __import__("datetime").timedelta(days=365 * 3)).strftime("%Y-%m-%d")
    cutoff24 = (NOW.replace(tzinfo=None) - __import__("datetime").timedelta(days=365 * 2)).strftime("%Y-%m-%d")
    rows = [r_ for r_ in rows if r_[0] >= cutoff36]
    old = [r_ for r_ in rows if r_[0] < cutoff24]
    recent = [r_ for r_ in rows if r_[0] >= cutoff24]
    weekly = [r_ for i, r_ in enumerate(old) if i % 5 == 0]
    return weekly + recent, source


PRICE_OWNED_KEYS = ("ticker", "fetched_at", "tier", "cik", "price_status",
                    "prints", "series", "week52", "probe")


def merge_price_update(prev, update):
    """Overlay a price refresh onto whatever the market file already holds.

    A prices run owns exactly the keys in PRICE_OWNED_KEYS. Everything else on the
    file — `quality`, `insider`, `fundamentals`, `pcs`, and any block added later —
    belongs to another request kind and survives untouched. Before 2026-08-29 this
    function did not exist: do_prices() rebuilt the dict from scratch carrying only
    `fundamentals` and `pcs` forward, so one prices refresh deleted the `quality`
    block that the dive gap table reads and the `insider` block behind it. That is
    the no-delete rule failing silently in the one venue no session can watch.
    Keys are carried by construction here, never by an enumerated allowlist, so a
    block invented next month is safe without touching this code.
    """
    out = dict(prev or {})
    for k, v in update.items():
        out[k] = v
    return out


def do_prices(ticker):
    cik = cik_for(ticker)
    ds = dual_source_price(ticker)
    rows, source = fetch_series(ticker)
    status = ds["status"].replace("-", "_")
    if status == "NO_DATA" and rows:
        status = "SINGLE_SOURCE"
    prev = jload(DATA / "market" / f"{safe_name(ticker)}.json", {})
    if status == "NO_DATA":
        if not control_ok():
            legs = ds.get("detail", {}).get("legs", {})
            leg_notes = "; ".join(f"{name}: {str(leg.get('note') or leg.get('status') or leg)[:80]}"
                                  for name, leg in legs.items()) or "no leg detail"
            raise RuntimeError(f"no price leg returned data ({leg_notes}) "
                               f"and the control probe failed ({_control.get('reason')})")
        update = {"ticker": ticker, "fetched_at": NOW.isoformat(), "tier": tier_for(ticker, cik),
                  "price_status": "VERIFIED_ZERO",
                  "probe": {"control_ticker": "AAPL", "plane": "yfinance",
                            "control_ok": True, "checked_at": NOW.isoformat()},
                  "prints": [], "series": None}
        jdump(DATA / "market" / f"{safe_name(ticker)}.json", merge_price_update(prev, update))
        return [f"data/market/{safe_name(ticker)}.json"]
    update = {
        "ticker": ticker, "fetched_at": NOW.isoformat(), "tier": tier_for(ticker, cik),
        "cik": cik, "price_status": status,
        "prints": ds["detail"].get("prints", []),
        "legs": ds["detail"].get("legs", {}),
    }
    if rows:
        update["series"] = {"interval": "1d(24mo)+1w(prior)", "rows": rows,
                            "source": source, "as_of": rows[-1][0]}
        update["week52"] = {"low": min(r_[1] for r_ in rows[-252:]),
                            "high": max(r_[1] for r_ in rows[-252:])}
    jdump(DATA / "market" / f"{safe_name(ticker)}.json", merge_price_update(prev, update))
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
    # Candidate lists widened 2026-09-04: seven screened shipping and defence filers
    # (DHT, FRONTLINE, SFL, TSAKOS, NORDIC-AMERICAN, TEEKAY-TANKERS, ELBIT) tag none of
    # the single cash/debt concepts this map carried, so net_debt_to_ebitda and the
    # Piotroski/Beneish inputs read NULL on profiles whose 10-K/20-F carries the numbers.
    "net_income": (["NetIncomeLoss", "ProfitLoss",
                    "NetIncomeLossAvailableToCommonStockholdersBasic"], "duration", "USD"),
    "cash": (["CashAndCashEquivalentsAtCarryingValue",
              "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
              "CashAndCashEquivalentsFairValueDisclosure"], "instant", "USD"),
    "total_debt": (["LongTermDebtNoncurrent", "LongTermDebt",
                    "LongTermDebtAndCapitalLeaseObligations",
                    "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                    "DebtLongtermAndShorttermCombinedAmount", "SecuredLongTermDebt",
                    "DebtInstrumentCarryingAmount"], "instant", "USD"),
    # --- widened 2026-08-29 for the analyst (Piotroski / Beneish / Altman / reverse DCF)
    "gross_profit": (["GrossProfit"], "duration", "USD"),
    "cost_of_revenue": (["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold",
                         "CostOfServices", "DirectOperatingCosts",
                         "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
                        "duration", "USD"),
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
    "receivables": (["AccountsReceivableNetCurrent", "AccountsReceivableNet",
                     "ReceivablesNetCurrent"], "instant", "USD"),
    "inventory": (["InventoryNet",
                   "InventoryNetOfAllowancesCustomerAdvancesAndProgressBillings"],
                  "instant", "USD"),
    "ppe_net": (["PropertyPlantAndEquipmentNet"], "instant", "USD"),
    "shares": (["CommonStockSharesOutstanding", "CommonStockSharesIssued",
                "WeightedAverageNumberOfDilutedSharesOutstanding",
                "WeightedAverageNumberOfSharesOutstandingBasic"], "instant", "shares"),
    # Same concepts as total_debt, kept as its own field because Piotroski and Beneish
    # both take a long-term-debt SERIES while total_debt is stored as a single latest row.
    "long_term_debt": (["LongTermDebtNoncurrent", "LongTermDebt",
                        "LongTermDebtAndCapitalLeaseObligations",
                        "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                        "DebtLongtermAndShorttermCombinedAmount", "SecuredLongTermDebt",
                        "DebtInstrumentCarryingAmount"], "instant", "USD"),
}

# The same fields under the `ifrs-full` taxonomy, for the 20-F / 40-F filer whose
# companyfacts carry no us-gaap facts at all. Until 2026-09-01 the SEC leg read only
# `facts["us-gaap"]` and accepted only 10-K/10-Q forms, so BHP (CIK 811809), TSM, ASX and
# ABBNY each came back 0/20 with a CIK that then blocked the vendor fallback: four profiles
# BLOCKED on data that data.sec.gov was serving all along. Concept names are the IFRS
# taxonomy's own; units are whatever currency the filer reports in, recorded as
# `statement_currency` rather than assumed USD (method section 6A currency rule).
IFRS_FACT_MAP = {
    "revenue": (["Revenue", "RevenueFromContractsWithCustomers"], "duration"),
    "net_income": (["ProfitLoss", "ProfitLossAttributableToOwnersOfParent"], "duration"),
    "cash": (["CashAndCashEquivalents", "Cash", "CashAndBankBalancesAtCentralBanks"],
             "instant"),
    "total_debt": (["NoncurrentBorrowings", "Borrowings", "LongtermBorrowings"], "instant"),
    "gross_profit": (["GrossProfit"], "duration"),
    "cost_of_revenue": (["CostOfSales"], "duration"),
    "operating_income": (["ProfitLossFromOperatingActivities"], "duration"),
    "operating_cashflow": (["CashFlowsFromUsedInOperatingActivities"], "duration"),
    "capex": (["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
              "duration"),
    "depreciation": (["DepreciationAndAmortisationExpense",
                      "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLoss"
                      "RecognisedInProfitOrLoss"], "duration"),
    "sga": (["SellingGeneralAndAdministrativeExpense", "AdministrativeExpense"], "duration"),
    "total_assets": (["Assets"], "instant"),
    "current_assets": (["CurrentAssets"], "instant"),
    "current_liabilities": (["CurrentLiabilities"], "instant"),
    "total_liabilities": (["Liabilities"], "instant"),
    "retained_earnings": (["RetainedEarnings"], "instant"),
    "equity": (["Equity", "EquityAttributableToOwnersOfParent"], "instant"),
    "receivables": (["TradeAndOtherCurrentReceivables", "CurrentTradeReceivables"], "instant"),
    "inventory": (["Inventories"], "instant"),
    "ppe_net": (["PropertyPlantAndEquipment"], "instant"),
    "shares": (["NumberOfSharesOutstanding", "NumberOfSharesIssued"], "instant"),
    "long_term_debt": (["NoncurrentBorrowings", "Borrowings", "LongtermBorrowings"],
                       "instant"),
}
ANNUAL_FORMS = ("10-K", "20-F", "40-F")
# The interim filter also admits the annual forms: it is what the "latest instant" reads
# of cash and total_debt use, and a 20-F filer files no 10-Q or 10-K at all, so until
# 2026-09-04 every foreign filer (DHT, FRO, SFL, TNK, NAT, TEN, ESLT) read cash and debt
# as absent while its 20-F carried both. 6-K is the foreign interim form.
INTERIM_FORMS = ("10-Q", "10-K", "6-K", "20-F", "40-F")


def _ifrs_currency(ifrs: dict) -> str | None:
    """The one currency the IFRS revenue concept reports in, so every field is read in the
    same unit. A filer with no revenue concept in any currency has no statement currency."""
    for n in IFRS_FACT_MAP["revenue"][0]:
        units = ifrs.get(n, {}).get("units", {})
        for ccy in sorted(units):
            if len(ccy) == 3 and ccy.isalpha() and ccy.isupper():
                return ccy
    return None


# The annual series the quality block consumes. Must stay in step with
# `acis.quality.compute_quality`'s series_fields: a name here that quality does not read
# is dead weight, and a name quality reads that is missing here silently downgrades every
# score that needed it to PENDING_DATA. `tools/check_analyst.py` asserts the two agree.
QUALITY_FIELDS = ("revenue", "net_income", "operating_income", "gross_profit",
                  "cost_of_revenue", "operating_cashflow", "capex", "depreciation",
                  "sga", "total_assets", "current_assets", "current_liabilities",
                  "total_liabilities", "retained_earnings", "equity", "receivables",
                  "inventory", "ppe_net", "shares", "long_term_debt")


def _fundamentals_sec(ticker, cik):
    """Primary leg: SEC companyfacts XBRL. Returns the fundamentals dict, writes nothing.

    Unchanged behaviour — this is the body `do_fundamentals` has always had, lifted into
    its own function so a second leg could exist without touching the first one.
    """
    edgar_wait()
    r = requests.get(SEC_COMPANYFACTS_URL.format(cik=int(cik)), headers=SEC_HEADERS, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"companyfacts HTTP {r.status_code} for {ticker}")
    facts = r.json().get("facts", {})
    gaap = facts.get("us-gaap", {})
    ifrs = facts.get("ifrs-full", {})
    dei = facts.get("dei", {})
    # One taxonomy per filer, chosen by which one carries facts. A US filer's us-gaap wins;
    # a 20-F/40-F filer with only ifrs-full is read from that, in its own currency.
    # The taxonomy whose facts run LATEST wins. A filer that switched from US GAAP to
    # IFRS keeps its old us-gaap facts in companyfacts forever (FRO's stop at 2021-12-31
    # while its ifrs-full facts run to 2025), and "us-gaap if any" read the dead one.
    def _latest_end(m):
        return max((v.get("end") or "" for body in m.values()
                    for units in (body.get("units") or {}).values() for v in units
                    if v.get("form") in ANNUAL_FORMS), default="")
    if gaap and ifrs:
        taxonomy = "ifrs-full" if _latest_end(ifrs) > _latest_end(gaap) else "us-gaap"
    else:
        taxonomy = "us-gaap" if gaap else ("ifrs-full" if ifrs else "us-gaap")
    ifrs_ccy = _ifrs_currency(ifrs) if taxonomy == "ifrs-full" else None
    merged_concepts = {}  # key -> concept names unioned when the fresh one had 1 period

    def pick(key, annual):
        """Latest 8 periods for one field. Instant facts have no duration to test."""
        if taxonomy == "ifrs-full":
            names, shape = IFRS_FACT_MAP[key]
            unit = "shares" if key == "shares" else ifrs_ccy
            src_map = ifrs
        else:
            names, shape, unit = FACT_MAP[key]
            src_map = gaap
        # Every candidate concept is read and the one whose series runs LATEST wins
        # (list order breaks ties). Until 2026-09-04 the first concept with any rows won,
        # so a filer that had moved from NetIncomeLoss to ProfitLoss in 2015 kept a
        # net_income series frozen a decade back while every other field ran to 2025,
        # and the quality block found no common fiscal year: TEEKAY-TANKERS and
        # HUNTINGTON-INGALLS read PENDING_DATA with every raw input present.
        # Among the concepts whose series reaches within ~13 months of the latest one,
        # the LONGEST series wins (list order breaks ties): a concept with one fresh row
        # must not beat one with eight rows ending a quarter earlier, because the quality
        # block needs common fiscal periods, not the single newest date.
        found_series = []
        for n in names:
            src = dei if (n == "EntityCommonStockSharesOutstanding") else src_map
            vals = src.get(n, {}).get("units", {}).get(unit) or [] if unit else []
            keep = {}
            for v in vals:
                form_ok = v.get("form") in ANNUAL_FORMS if annual \
                    else v.get("form") in INTERIM_FORMS
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
                found_series.append((max(keep), sorted(keep.items())[-8:], n))
        if not found_series:
            return []
        latest = max(end for end, _, _ in found_series)
        try:
            cutoff = (datetime.fromisoformat(latest) - timedelta(days=400)).date().isoformat()
        except Exception:  # noqa: BLE001
            cutoff = ""
        fresh = [(end, rows) for end, rows, _ in found_series if end >= cutoff]
        best = max(fresh, key=lambda er: len(er[1]))[1]  # max() keeps the first on ties
        if len(best) < 3 and len(found_series) > 1:
            # A filer that switched concept names leaves the fresh concept with one
            # period and the old one with the history (DKNG's long_term_debt on
            # 2026-09-06: one 2025 row). Union the candidates by period, freshest concept
            # winning each period, so the change legs have something to change from;
            # the merge is declared on the block, never silent.
            merged = {}
            for _, rows, _n in sorted(found_series):  # oldest-ending concept first
                for end, val in rows:
                    merged[end] = val  # a fresher concept overwrites the same period
            merged_concepts.setdefault(key, [_n for _, _, _n in sorted(found_series)])
            return sorted(merged.items())[-8:]
        return best

    def concept_hints(missing_keys):
        """For every field the map could not read: the filer's own concept names that
        look like candidates, with each one's latest end date. Diagnostic only, so a
        session can widen FACT_MAP from evidence instead of guessing concept names
        (2026-09-04: seven shipping filers read NULL on cash and debt and nobody could
        say which tag they used without opening companyfacts by hand)."""
        words = {"cash": ("Cash",), "total_debt": ("Debt", "Borrowing", "Notes", "Loan"),
                 "long_term_debt": ("Debt", "Borrowing", "Notes", "Loan"),
                 "cost_of_revenue": ("Cost", "Voyage", "Vessel", "Expense"),
                 "gross_profit": ("Gross",), "sga": ("Administrative", "Selling"),
                 "capex": ("Payments", "Purchase", "Acquire"),
                 "ppe_net": ("PropertyPlant", "Vessel"), "inventory": ("Inventor",),
                 "shares": ("Shares",), "total_liabilities": ("Liabilit",),
                 "net_income": ("Income", "Profit"), "receivables": ("Receivable",),
                 "retained_earnings": ("Retained",), "equity": ("Equity",),
                 "depreciation": ("Depreciation", "Amortization", "Amortisation"),
                 "operating_income": ("Operating",), "operating_cashflow": ("Operating",),
                 "current_assets": ("Current",), "current_liabilities": ("Current",),
                 "revenue": ("Revenue", "Sales")}
        out = {}
        hint_map = ifrs if taxonomy == "ifrs-full" else gaap
        for key in missing_keys:
            hits = []
            for name, body in hint_map.items():
                if not any(w in name for w in words.get(key, ())):
                    continue
                ends = [v.get("end") for units in (body.get("units") or {}).values()
                        for v in units if v.get("form") in ANNUAL_FORMS]
                if ends:
                    hits.append((max(ends), name))
            # Newest first, then names that START with the keyword, then alphabetical:
            # a cap sorted by name descending hid CashAndCashEquivalents behind
            # RestrictedCash... on the first run.
            keys = words.get(key, ())
            hits.sort(key=lambda en: (en[0], any(en[1].startswith(w) for w in keys), en[1] and -ord(en[1][0])), reverse=True)
            out[key] = [f"{n} ({e})" for e, n in hits[:20]]
        return out

    f = {
        "source": "sec-companyfacts", "cik": cik, "as_of": TODAY,
        "taxonomy": taxonomy,
        # USD for a us-gaap filer by construction of FACT_MAP; the filer's own reporting
        # currency for ifrs-full. do_quality refuses a mixed-currency market cap on this.
        "statement_currency": "USD" if taxonomy == "us-gaap" else ifrs_ccy,
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
    # A field whose series stops more than ~13 months before the filer's latest annual
    # period is a dead concept, not data: HII's cost_of_revenue ran 2014-2017 and TTEK's
    # sga stopped in 2011 while every other field ran to 2025, and the quality block
    # found "0 common fiscal periods" with every input present. Such a field is dropped
    # here, named under coverage.stale_dropped, and sent to concept_hints so the next
    # widening is evidence.
    latest_ends = {k: f[f"{k}_fy"][-1][0] for k in found if f.get(f"{k}_fy")}
    if latest_ends:
        overall_latest = max(latest_ends.values())
        try:
            stale_cutoff = (datetime.fromisoformat(overall_latest)
                            - timedelta(days=400)).date().isoformat()
        except Exception:  # noqa: BLE001
            stale_cutoff = ""
        stale = [k for k, end in latest_ends.items() if end < stale_cutoff]
        for k in stale:
            f.pop(f"{k}_fy", None)
            found.remove(k)
        if stale:
            f["stale_dropped"] = {k: latest_ends[k] for k in stale}
    # Liabilities is the one balance-sheet total many filers never tag (TTEK, J on
    # 2026-09-06); total_liabilities = total_assets - equity is an accounting identity,
    # computed per common period and declared as derived.
    if "total_liabilities" not in found and f.get("total_assets_fy") and f.get("equity_fy"):
        eq = dict(f["equity_fy"])
        derived = [(end, val - eq[end]) for end, val in f["total_assets_fy"]
                   if end in eq and val is not None and eq[end] is not None]
        if len(derived) >= 2:
            f["total_liabilities_fy"] = derived[-8:]
            found.append("total_liabilities")
            f.setdefault("derived", {})["total_liabilities_fy"] = "total_assets_fy - equity_fy"
    if merged_concepts:
        f["merged_concepts"] = merged_concepts
    missing_keys = [k for k in attempted if k not in found]
    if not f.get("cash"):
        missing_keys.append("cash")
    if not f.get("total_debt"):
        missing_keys.append("total_debt")
    if missing_keys:
        f["concept_hints"] = concept_hints(missing_keys)
    f["coverage"] = {"annual_fields_found": len(found), "annual_fields_attempted": len(attempted),
                     "missing": [k for k in attempted if k not in found]}
    if not f["revenue_fy"] and not f["revenue_q"]:
        if not edgar_facts_ok():
            raise RuntimeError("empty companyfacts AND the EDGAR companyfacts probe failed "
                               "— cannot tell an empty filer from a dead data.sec.gov")
        f["verified_zero"] = {"note": "no revenue concepts found",
                              "probe": "edgar-AAPL-companyfacts-ok"}
    return f


# ------------------------------------------- fundamentals: yfinance statements fallback
# The SEC leg only exists for a filer with a CIK, so before 2026-08-30 every non-US listing
# had no fundamentals, no `pcs` and no `quality` block at all: 16 FAILED rows in
# data/requests.json every one reading "no SEC CIK for <T>", and 19 of 28 files in
# data/market/ price-only. Chains here are global by construction, which left most of the
# issuer census unprofileable and undivable. Ron approved this second leg 2026-08-30.
#
# What it is NOT: a filing. Yahoo's statements are a VENDOR AGGREGATE — a third party's
# normalisation of a local-GAAP or IFRS report nobody in this repo has read. Every block it
# writes carries source "yfinance-statements", tag INFERRED and official_source false, and
# method section 6A now says in as many words that such a number satisfies the T2/T3
# profile bar only with a derivation basis and can never be cited as a filing quote.
#
# Each field names candidate (statement, row labels) pairs in preference order. The labels
# are the DataFrame index yf.Ticker(t).income_stmt / .balance_sheet / .cashflow return; they
# were read off a live response (1072.HK, 2026-08-30), not guessed from documentation. The
# emitted keys come from QUALITY_FIELDS itself rather than a parallel list, so a rename
# there fails this map loudly instead of silently degrading every score to PENDING_DATA the
# way the 2026-08-29 long_term_debt drift did.
YF_FACT_MAP = {
    "revenue": [("income", ["Total Revenue", "Operating Revenue"])],
    "net_income": [("income", ["Net Income", "Net Income Common Stockholders",
                               "Net Income Including Noncontrolling Interests",
                               "Net Income Continuous Operations"])],
    "operating_income": [("income", ["Operating Income",
                                     "Total Operating Income As Reported", "EBIT"])],
    "gross_profit": [("income", ["Gross Profit"])],
    "cost_of_revenue": [("income", ["Cost Of Revenue", "Reconciled Cost Of Revenue"])],
    "operating_cashflow": [("cash", ["Operating Cash Flow",
                                     "Cash Flow From Continuing Operating Activities"])],
    # Yahoo signs Capital Expenditure as a cash OUTFLOW (negative); the SEC leg's
    # PaymentsToAcquirePropertyPlantAndEquipment is a positive payment. Normalised to the
    # SEC convention below so one stored field never means two different things.
    "capex": [("cash", ["Capital Expenditure", "Purchase Of PPE"])],
    "depreciation": [("cash", ["Depreciation And Amortization",
                               "Depreciation Amortization Depletion", "Depreciation"]),
                     ("income", ["Reconciled Depreciation",
                                 "Depreciation And Amortization In Income Statement"])],
    "sga": [("income", ["Selling General And Administration",
                        "General And Administrative Expense"])],
    "total_assets": [("balance", ["Total Assets"])],
    "current_assets": [("balance", ["Current Assets", "Total Current Assets"])],
    "current_liabilities": [("balance", ["Current Liabilities", "Total Current Liabilities"])],
    "total_liabilities": [("balance", ["Total Liabilities Net Minority Interest",
                                       "Total Liabilities"])],
    "retained_earnings": [("balance", ["Retained Earnings"])],
    "equity": [("balance", ["Stockholders Equity", "Common Stock Equity",
                            "Total Equity Gross Minority Interest"])],
    "receivables": [("balance", ["Accounts Receivable", "Receivables",
                                 "Gross Accounts Receivable"])],
    "inventory": [("balance", ["Inventory", "Inventories"])],
    "ppe_net": [("balance", ["Net PPE", "Property Plant And Equipment Net"])],
    # A point-in-time share COUNT, matching the SEC leg's instant-shape `shares`. The
    # income-statement average-share rows are the fallback, not the first choice: Piotroski
    # reads this series to detect issuance, and an average smears the very step it looks for.
    "shares": [("balance", ["Ordinary Shares Number", "Share Issued"]),
               ("income", ["Diluted Average Shares", "Basic Average Shares"])],
    "long_term_debt": [("balance", ["Long Term Debt",
                                    "Long Term Debt And Capital Lease Obligation"])],
}

# The two non-QUALITY_FIELDS rows the SEC leg also writes, because do_quality reads them to
# build enterprise value. Same single-latest-row shape it produces.
YF_EXTRA_MAP = {
    "cash": [("balance", ["Cash And Cash Equivalents",
                          "Cash Cash Equivalents And Short Term Investments"])],
    "total_debt": [("balance", ["Total Debt", "Long Term Debt And Capital Lease Obligation",
                                "Long Term Debt"])],
}

YF_CONTROL_TICKER = "MSFT"


def yf_statements_ok():
    """MSFT's income statement carries Total Revenue — proves the Yahoo fundamentals plane
    is alive, so an empty statement set elsewhere can be stamped verified_zero instead of
    lying. Same discipline as edgar_facts_ok(), applied on the plane the number actually
    came from: neither the stooq price probe nor an EDGAR probe certifies anything about a
    vendor statements endpoint."""
    def probe():
        import yfinance as yf
        inc = yf.Ticker(YF_CONTROL_TICKER).income_stmt
        return inc is not None and not inc.empty and "Total Revenue" in list(inc.index)
    return _edgar_probe("yf_statements", probe,
                        f"{YF_CONTROL_TICKER} yfinance income_stmt", plane="yfinance")


def _yf_num(v):
    """float(v) or None. NaN is None, because a NaN written to JSON is not valid JSON and
    a NaN scored as a number is worse."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _yf_rows(frames, sources, transform=None):
    """Latest 8 annual periods for one field as [(period_end, value), ...], oldest first.

    Exactly the row shape the SEC leg's pick() returns, because acis.quality reads both
    legs through one code path and must never need to know which one wrote the file.
    """
    for statement, labels in sources:
        fr = frames.get(statement)
        if fr is None:
            continue
        for label in labels:
            if label not in fr.index:
                continue
            row = fr.loc[label]
            if getattr(row, "ndim", 1) > 1:   # duplicate label -> DataFrame, take the first
                row = row.iloc[0]
            keep = {}
            for col, val in row.items():
                v = _yf_num(val)
                if v is None:
                    continue
                end = col.date() if hasattr(col, "date") else col
                keep[str(end)[:10]] = transform(v) if transform else v
            if keep:
                return sorted(keep.items())[-8:]
    return []


def _fundamentals_yfinance(ticker):
    """Fallback leg for a ticker with no SEC CIK. Returns the fundamentals dict.

    Reuses the plumbing the price leg already uses — a local `import yfinance`, one Ticker
    object per call, and a recorded reason for every source that did not answer — rather
    than adding a second HTTP path of its own. One Ticker object matters: yfinance caches
    per instance, so the three statements and `.info` cost one round trip each, once.
    """
    try:
        import yfinance as yf
    except ImportError as e:
        raise RuntimeError(f"yfinance not installed in this job: {e}")
    try:
        tk = yf.Ticker(ticker)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"yfinance could not resolve {ticker}: "
                           f"{type(e).__name__}: {str(e)[:120]}")

    # A leg that swallows its failure is an unfalsifiable source (see dual_source.py).
    frames, legs = {}, {}
    for name, attr in (("income", "income_stmt"), ("balance", "balance_sheet"),
                       ("cash", "cashflow")):
        try:
            fr = getattr(tk, attr)
            ok = fr is not None and not fr.empty
            frames[name] = fr if ok else None
            legs[name] = {"answered": bool(ok), "reason": None if ok else "empty frame",
                          "line_items": int(fr.shape[0]) if ok else 0,
                          "periods": [str(c)[:10] for c in fr.columns] if ok else []}
        except Exception as e:  # noqa: BLE001
            frames[name] = None
            legs[name] = {"answered": False, "line_items": 0, "periods": [],
                          "reason": f"{type(e).__name__}: {str(e)[:120]}"}
    info = {}
    try:
        info = tk.info or {}
        legs["info"] = {"answered": bool(info), "reason": None if info else "empty info"}
    except Exception as e:  # noqa: BLE001
        legs["info"] = {"answered": False, "reason": f"{type(e).__name__}: {str(e)[:120]}"}

    f = {
        # Honesty markers, load-bearing. method section 1's tag vocabulary and section 6A
        # both bind on these: a vendor aggregate is INFERRED, is not an official source,
        # and can never be cited as a filing quote.
        "source": "yfinance-statements",
        "tag": "INFERRED",
        "official_source": False,
        "vendor": "Yahoo Finance via yfinance",
        "vendor_caveat": ("third-party normalisation of a local-GAAP/IFRS report this repo "
                          "has not read; INFERRED, never citable as a filing quote "
                          "(method section 6A)"),
        "cik": None,
        "as_of": TODAY,
        # Written because they can disagree: 1072.HK reports in CNY and trades in HKD, and
        # multiplying an HKD price by a share count to compare against CNY liabilities is a
        # manufactured number. do_quality reads these two fields and refuses the comparison.
        "statement_currency": info.get("financialCurrency"),
        "price_currency": info.get("currency"),
        "revenue_fy": _yf_rows(frames, YF_FACT_MAP["revenue"]),
        # The SEC leg's revenue_q is 10-Q-derived. Yahoo's interim statements are not
        # reliably quarterly for a foreign filer (a Hong Kong issuer reports half-yearly),
        # so an interim series stored under a name that says "quarterly" would be mislabelled
        # data. Empty with a stated reason beats a wrong label (method section 1).
        "revenue_q": [],
        "interim_note": ("no interim series: vendor interim statements are not reliably "
                         "quarterly for non-US filers, and a half-year row stored as _q "
                         "would be mislabelled"),
        "legs": legs,
    }
    found, attempted = [], []
    for key in QUALITY_FIELDS:
        attempted.append(key)
        rows = _yf_rows(frames, YF_FACT_MAP[key],
                        transform=abs if key == "capex" else None)
        if rows:
            f[f"{key}_fy"] = rows
            found.append(key)
    for key, sources in YF_EXTRA_MAP.items():
        f[key] = _yf_rows(frames, sources)[-1:] or None
    f["coverage"] = {"annual_fields_found": len(found),
                     "annual_fields_attempted": len(attempted),
                     "missing": [k for k in attempted if k not in found]}

    # Vendor scalars, kept OUT of the *_fy series shape on purpose: a single latest count is
    # not a series, and Piotroski reading it as one would compare a period against itself.
    # do_quality falls back to them only when the statement series is absent.
    shares_field = ("impliedSharesOutstanding" if _yf_num(info.get("impliedSharesOutstanding"))
                    else "sharesOutstanding")
    f["shares_latest"] = {"value": _yf_num(info.get(shares_field)), "field": shares_field,
                          "source": "yfinance-info", "tag": "INFERRED", "as_of": TODAY}
    f["market_cap_vendor"] = {"value": _yf_num(info.get("marketCap")),
                              "currency": info.get("currency"),
                              "source": "yfinance-info", "tag": "INFERRED", "as_of": TODAY}

    if not f["revenue_fy"]:
        # Same rule as the SEC leg, certified on the plane the read came from: an empty
        # result is only writable when a control probe proved the endpoint answers at all.
        if not yf_statements_ok():
            raise RuntimeError(
                f"no yfinance statement rows for {ticker} AND the yfinance statements "
                f"control probe failed — cannot tell a name Yahoo does not cover from a "
                f"dead fundamentals endpoint. legs: {legs}")
        f["verified_zero"] = {"note": "no revenue rows in yfinance statements",
                              "probe": f"yfinance-{YF_CONTROL_TICKER}-income-stmt-ok"}
        print(f"  WARN fundamentals {ticker}: yfinance returned no revenue rows "
              f"(control probe OK, so this is coverage, not an outage)")
    print(f"  fundamentals {ticker}: yfinance-statements, {len(found)}/{len(attempted)} "
          f"annual fields, currency {f['statement_currency']}")
    return f


def _store_fundamentals(ticker, f, cik):
    """One writer for both legs, so a block can never land in a shape that depends on which
    source produced it."""
    path = DATA / "market" / f"{safe_name(ticker)}.json"
    m = jload(path, {"ticker": ticker, "price_status": "NO_DATA", "prints": [], "series": None, "pcs": None})
    m["fundamentals"] = f
    m["fetched_at"] = NOW.isoformat()
    m.setdefault("tier", tier_for(ticker, cik))
    jdump(path, m)
    return [f"data/market/{safe_name(ticker)}.json"]


def do_fundamentals(ticker):
    """SEC companyfacts when the ticker resolves to a CIK, the vendor statements fallback
    when it does not, or when the SEC leg answered with nothing at all.

    An SEC filer never silently reads a vendor aggregate INSTEAD of its own filings: the
    vendor leg runs for a CIK only after companyfacts was fetched, returned zero annual
    fields under both taxonomies, and the same-run EDGAR probe proved the endpoint alive.
    The block then records `sec_attempted` so a reader can see the filing surface was
    tried and found empty, and keeps the CIK so identity and tier survive."""
    cik = cik_for(ticker)
    if not cik:
        return _store_fundamentals(ticker, _fundamentals_yfinance(ticker), cik)
    f = _fundamentals_sec(ticker, cik)
    if f["coverage"]["annual_fields_found"] == 0 and not f.get("revenue_q"):
        print(f"  fundamentals {ticker}: companyfacts has 0/{f['coverage']['annual_fields_attempted']} "
              f"annual fields under {f.get('taxonomy')} (probe OK) — falling through to "
              f"yfinance-statements, INFERRED")
        attempted = {"cik": cik, "taxonomy": f.get("taxonomy"),
                     "annual_fields_found": 0, "probe": (f.get("verified_zero") or {}).get("probe"),
                     "as_of": TODAY}
        f = _fundamentals_yfinance(ticker)
        f["cik"] = cik
        f["sec_attempted"] = attempted
    return _store_fundamentals(ticker, f, cik)


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
    shares_as_of = fund.get("shares_fy", [[None]])[-1][0] if fund.get("shares_fy") else None
    if shares is None and isinstance(fund.get("shares_latest"), dict):
        # Vendor leg only: the statement series is absent, so the latest vendor count is the
        # honest remaining input. Named as vendor in shares_used rather than blended in.
        shares = fund["shares_latest"].get("value")
        shares_as_of = fund["shares_latest"].get("as_of")
    price = None
    if m.get("series") and m["series"].get("rows"):
        price = m["series"]["rows"][-1][1]
    elif m.get("prints"):
        price = m["prints"][0].get("close")
    market_cap = shares * price if (shares and price) else None
    if market_cap is None and isinstance(fund.get("market_cap_vendor"), dict):
        market_cap = fund["market_cap_vendor"].get("value")
    # A statement currency that differs from the trading currency makes market cap and
    # enterprise value cross-currency arithmetic: 1072.HK reports in CNY and trades in HKD,
    # so shares x price is HKD while total_liabilities is CNY. Altman Z and the reverse DCF
    # would both be confidently wrong. No FX series is on disk, so the answer is NULL with a
    # stated basis, never a converted guess (method section 1). The SEC leg writes neither
    # field, so this can only ever fire on the vendor leg.
    currency_note = None
    stmt_ccy, px_ccy = fund.get("statement_currency"), fund.get("price_currency")
    if stmt_ccy and px_ccy and stmt_ccy != px_ccy:
        currency_note = (f"statements reported in {stmt_ccy}, listing trades in {px_ccy}; "
                         f"no FX source on disk, so market cap and enterprise value are "
                         f"NULL rather than mixed-currency arithmetic")
        market_cap = None
        print(f"  quality {ticker}: {currency_note}")
    debt, cash = last_val(fund.get("total_debt")), last_val(fund.get("cash"))
    ev = None
    if market_cap is not None and debt is not None and cash is not None:
        ev = market_cap + debt - cash

    q = compute_quality(fund, market_cap=market_cap, enterprise_value=ev, as_of=TODAY)
    q["currency_note"] = currency_note
    q["official_source"] = fund.get("official_source", True)
    q["source_tag"] = fund.get("tag", "VERIFIED")
    q["price_used"] = {"value": price, "source": (m.get("series") or {}).get("source"),
                       "as_of": (m.get("series") or {}).get("as_of")}
    q["shares_used"] = {"value": shares, "as_of": shares_as_of,
                        "source": "fundamentals.shares_fy" if fund.get("shares_fy")
                        else ("fundamentals.shares_latest (vendor, INFERRED)"
                              if shares is not None else None)}
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
# How many Form 4 filings one run will parse. This was a bare `40` written inline in
# three places, which made a truncated window read like a complete one: VRT listed 450
# filings, 40 were examined, and the output said `row_count: 40` with nothing anywhere
# saying the other 410 were never opened. The cap stays (parsing 450 filings is minutes
# of EDGAR-throttled work per ticker) but it is now named, recorded in health, and
# printed as a WARN when it actually truncates.
INSIDER_FILINGS_CAP = 40

# Form 4 transaction codes that are open-market trades by the insider's own choice.
# P = open-market purchase, S = open-market sale. Everything else (A awards, F tax
# withholding, M option exercise, G gifts) is compensation plumbing, not a signal about
# what the insider thinks the stock is worth. VRT's 40 rows are overwhelmingly fractional
# dividend-equivalent accruals at price 0, which a page reporting "40 insider
# transactions" presents as if they were trades.
MARKET_TRADE_CODES = {"P", "S"}


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

    if len(filings) > INSIDER_FILINGS_CAP:
        diag["filings_truncated"] = True
        print(f"  WARN insider {ticker}: {len(filings)} Form 4 filings listed, parsing the "
              f"most recent {INSIDER_FILINGS_CAP} — the window is truncated, not exhaustive")
    rows = []
    for filing in filings[:INSIDER_FILINGS_CAP]:
        fdate = str(getattr(filing, "filing_date", "") or "")
        if fdate and fdate < cutoff:
            break
        try:
            ob = filing.obj()
        except Exception as e:  # noqa: BLE001
            diag["parse_errors"].append(f"{fdate}: {str(e)[:120]}")
            print(f"  Form 4 parse failed ({fdate}): {e}")
            continue
        # Extraction bound against the INSTALLED edgartools source, read offline
        # (edgar.ownership.forms.Form4), not guessed. get_transaction_activities() is the
        # canonical method: it returns List[TransactionActivity] and already unifies
        # market trades (P/S), non-market events (awards A, tax F, option exercise M, gifts,
        # conversions) and derivatives into one normalized list. The earlier per-accessor
        # binding failed because common_stock_purchases returns a pandas DataFrame that is
        # empty for the very common all-awards filing, and the fallback then handed back
        # TransactionActivity dataclasses that dict() cannot iterate.
        try:
            activities = ob.get_transaction_activities()
        except Exception as e:  # noqa: BLE001
            diag["parse_errors"].append(f"{fdate} get_transaction_activities: {str(e)[:120]}")
            continue
        if not activities:
            # A Form 4 with zero activities is legitimate (e.g. a pure holdings amendment).
            # It is not an extraction failure; it just adds no rows. Recorded, not raised.
            diag["empty_activity_filings"] = diag.get("empty_activity_filings", 0) + 1
            continue
        insider = str(getattr(ob, "insider_name", None) or "")[:120]
        for t in activities:
            d = dataclasses.asdict(t)
            rows.append({
                "filing_date": fdate,
                "insider": insider,
                "code": t.code,
                "code_description": t.code_description,
                "transaction_type": t.transaction_type,
                "security_type": t.security_type,
                "security_title": d.get("security_title"),
                "shares": t.shares_numeric,
                "price_per_share": t.price_numeric,
                "value": t.value_numeric,
                "is_derivative": t.is_derivative,
            })
        diag["bound_via"] = "get_transaction_activities"
    # verified_zero needs a control on the SAME plane. stooq-AAPL proves prices work and
    # says nothing about whether EDGAR ownership data is reachable, so it cannot certify
    # an empty Form 4 result. Probe EDGAR with a filer that always has Form 4s.
    if not rows and diag["filings_listed"]:
        raise RuntimeError(
            f"{ticker}: {diag['filings_listed']} Form 4 filings listed and "
            f"{min(len(filings), INSIDER_FILINGS_CAP)} examined, but 0 transaction rows "
            f"extracted. That is an "
            f"EXTRACTION failure, not a verified zero — a company with filings has trades. "
            f"diagnostics: {diag}")
    # A genuine zero (a filer that truly reported nothing in the window) is only reachable
    # here when filings_listed is 0 — the guard above already raised on filings-but-no-rows.
    # It is still certified on the EDGAR plane, never the price plane.
    edgar_control = None
    if not rows:
        edgar_control = edgar_forms_ok()
        if not edgar_control:
            raise RuntimeError(
                f"zero Form 4 rows for {ticker} AND the EDGAR Form 4 probe failed — "
                f"cannot tell an empty result from a dead path. diagnostics: {diag}")
    # row_count counts every Form 4 line. market_rows counts the subset that is an
    # actual open-market trade — see MARKET_TRADE_CODES. Both are written because
    # dropping the compensation rows would be deleting data, and reporting only the
    # total would let award accruals masquerade as insider conviction.
    market_rows = [r for r in rows
                   if r.get("code") in MARKET_TRADE_CODES and (r.get("price_per_share") or 0) > 0]
    out = {"ticker": ticker, "cik": cik, "fetched_at": NOW.isoformat(),
           "window": [cutoff, TODAY], "row_count": len(rows), "rows": rows[:200],
           "market_row_count": len(market_rows),
           "diagnostics": diag,
           "health": {"filings_examined": min(len(filings), INSIDER_FILINGS_CAP),
                      "filings_cap": INSIDER_FILINGS_CAP,
                      "filings_listed": diag["filings_listed"],
                      "window_truncated": bool(diag.get("filings_truncated")),
                      "market_rows": len(market_rows),
                      "non_market_rows": len(rows) - len(market_rows),
                      **({"verified_zero": True, "probe": "edgar-AAPL-form4-ok"}
                         if (not rows and edgar_control) else {})}}
    path = DATA / "market" / f"{safe_name(ticker)}.json"
    m = jload(path, {"ticker": ticker, "price_status": "NO_DATA", "prints": [],
                     "series": None, "fundamentals": None, "pcs": None})
    m["insider"] = out
    m["fetched_at"] = NOW.isoformat()
    m.setdefault("tier", tier_for(ticker, cik))
    jdump(path, m)
    print(f"  insider {ticker}: {len(rows)} transaction row(s) "
          f"({len(market_rows)} open-market) via {diag.get('bound_via')}"
          + (f", {diag['empty_activity_filings']} empty-activity filing(s)"
             if diag.get("empty_activity_filings") else ""))
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
        cik_i = int(ciks[0]) if ciks else None
        # A hit with no link is a claim nobody can check. The FTS response carries no
        # primary-document filename, so the honest link is the filing's index page,
        # which EDGAR builds from cik + un-dashed accession. Written for every hit so a
        # screen row or chain edge sourced from FTS can be traced back to the filing.
        url = (f"https://www.sec.gov/Archives/edgar/data/{cik_i}/"
               f"{adsh.replace('-', '')}/" if (cik_i and adsh) else None)
        hits.append({
            "entity_name": names[0].split("  (")[0].strip() if names else None,
            "cik": cik_i,
            "ticker": tick,
            "form": roots[0] if roots else src.get("file_type"),
            "file_type": src.get("file_type"),
            "filing_date": src.get("file_date"),
            "accession": adsh,
            "url": url,
        })
    total = js.get("hits", {}).get("total", {})
    n = total.get("value", len(hits)) if isinstance(total, dict) else len(hits)
    if not hits and not edgar_fts_ok():
        raise RuntimeError("zero FTS hits AND the EDGAR FTS probe failed — cannot tell an "
                           "empty query from a dead efts.sec.gov")
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:60]
    out = {"query": query, "forms": params["forms"], "window": [start, TODAY],
           "fetched_at": NOW.isoformat(), "hit_total": n, "hits": hits[:100],
           "health": {"status_code": r.status_code, "hit_count": len(hits),
                      **({"verified_zero": True, "probe": "edgar-fts-revenue-ok"} if not hits else {})}}
    jdump(DATA / "edgar" / "fts" / f"{slug}.json", out)
    return [f"data/edgar/fts/{slug}.json"]


# ---------------------------------------------------------------- EDGAR doc
def _strip_html(html_text):
    """Filing HTML to plain text, with entities DECODED rather than blanked.

    This text is the evidence base: `tools/check_screen.py` verifies every earnings quote
    against it verbatim, and method section 1 says a quote that cannot be verified is not
    evidence. The old line replaced `&#?[a-zA-Z0-9]+;` with a SPACE, so every HTML entity
    became a hole: "Powell&#8217;s Chairman" was stored as "Powell s Chairman" and
    "T&amp;D backlog" as "T D backlog". Two consequences, both bad. A quote copied from
    the real SEC page carries a real apostrophe and would NOT match the mangled text on
    disk, so the verifier would reject TRUE quotes — the failure direction that gets a
    checker switched off. And a quote that did match printed on the stock page with the
    mangling intact, which is a misquotation of a filing.

    Entities are decoded after tags are removed, so a decoded "<" can never be read as
    markup. html.unescape is stdlib.
    """
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _web_canonical(url):
    """Same rule as tools/evidence_store.canonical_url. Duplicated on purpose: this job runs
    in Actions without tools/ on its path; tools/tests/test_evidence_store.py asserts the
    two agree on every URL shape in the corpus."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if parts.port and not ((scheme == "https" and parts.port == 443) or
                           (scheme == "http" and parts.port == 80)):
        host = f"{host}:{parts.port}"
    path = re.sub(r"/+$", "", parts.path) or "/"
    tracking = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith(tracking)]
    query.sort()
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def _is_sec_host(url) -> bool:
    """Any sec.gov host, www.sec.gov/Archives included. SEC's access policy asks every
    automated client for the declared EDGAR agent string and rate limit; the browser-like
    agent from a datacenter address answers 403 (found 2026-09-06: two AEVA exhibit pages
    cited on ai-infrastructure were stored as 403, which failed validate for every session
    until they could be re-fetched)."""
    m = re.match(r"https?://([^/]+)", url or "") if isinstance(url, str) else None
    host = (m.group(1) if m else "").lower()
    return host == "sec.gov" or host.endswith(".sec.gov")


def _web_headers(url) -> dict:
    accept = "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5"
    if _is_sec_host(url):
        return {**SEC_HEADERS, "Accept": accept}
    return {"User-Agent": WEB_USER_AGENT, "Accept": accept}


def do_web_doc(url):
    """Fetch one web page into data/web/<sha16>.json as plain text, the web analogue of
    do_edgar_doc. A non-200 answer is STORED, not raised: a 403 or 404 is a fact about the
    source that the citing stage must see (the citation moves), and a stored refusal is
    what lets tools/check_impact.py fail a claim whose source cannot be read. Only a
    transport failure (no response at all) raises, so the row retries."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise RuntimeError(f"web_doc needs an http(s) url, got {url!r}")
    canon = _web_canonical(url)
    doc_id = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]
    if _is_sec_host(url):
        edgar_wait()
    r = requests.get(url, headers=_web_headers(url), timeout=45, allow_redirects=True)
    ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    text = ""
    if r.status_code == 200:
        if ctype in ("text/html", "application/xhtml+xml", ""):
            text = _strip_html(r.text)
        elif ctype.startswith("text/") or ctype in ("application/json", "application/xml"):
            text = re.sub(r"\s+", " ", r.text).strip()
        # A PDF or other binary is stored EMPTY on purpose: its text cannot be verified
        # with the same rule, and an empty text is the honest record of that.
    text = text[:MAX_WEB_CHARS]
    doc = {
        "id": doc_id,
        "url": url,
        "canonical_url": canon,
        "final_url": r.url,
        "fetched_at": NOW.isoformat(),
        "http_status": r.status_code,
        "content_type": ctype,
        "bytes": len(r.content or b""),
        "chars": len(text),
        "truncated": len(text) >= MAX_WEB_CHARS,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text": text,
    }
    path = DATA / "web" / f"{doc_id}.json"
    jdump(path, doc)
    print(f"  web_doc {doc_id}: http {r.status_code}, {ctype or 'no content-type'}, "
          f"{len(text)} chars <- {canon}")
    return [f"data/web/{doc_id}.json"]


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
            names = [it["name"] for it in idx.get("directory", {}).get("item", [])
                     if str(it.get("name", "")).endswith((".htm", ".html"))]
            if not names:  # index.json is flaky (sometimes truncated) — parse the dir listing
                edgar_wait()
                listing = requests.get(base + "/", headers=SEC_HEADERS, timeout=60).text
                names = sorted(set(re.findall(r'href="[^"]*/([^"/]+\.htm[l]?)"', listing)))
            # Prefer names that look like an EX-99 exhibit, but do NOT stop there: GE
            # Vernova and Constellation both file the earnings release under a name with
            # no "ex99" in it (q2-2026-earnings-release.htm and similar), so the old
            # pattern-only search found nothing, silently kept the 8-K COVER PAGE, and
            # left those two filers permanently unquotable while looking fetched. Any
            # document in the filing is a candidate; the longest one wins, and the
            # comparison below only replaces the cover if it is genuinely longer.
            likely = [n for n in names if re.search(r"ex.{0,7}?99", n, re.I)]
            candidates = likely + [n for n in names if n not in likely]
            best = ""
            for name in candidates[:8]:
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
    """Keep every live dive's price series fresh on the cron.

    Skips underscore-prefixed files, which are AGENT STORES and not dives:
    `data/stocks/_dive-log.json` is Stocky's record and carries no `ticker`. Without the
    skip this raised KeyError and killed the whole cron run AFTER the feed batch had
    already fetched 385 items, so the feeds were discarded and the intake corpus sat
    frozen from 2026-08-29 to 2026-09-01 while every push-triggered run passed, because
    this function only runs on the cron. Every other reader in the repo already skips
    them (`app/build.py:read_json_dir`, `validate.py`'s plans loop,
    `impact_calibrate.appraisals`); this one did not.

    A dive missing its `ticker` is now skipped and named rather than fatal: one malformed
    file must never cost a whole scheduled run.
    """
    tickers = set()
    for sf in sorted((DATA / "stocks").glob("*.json")):
        if sf.name.startswith("_"):
            continue
        st = jload(sf, {})
        if st.get("status") == "ARCHIVED" or st.get("fixture"):
            continue
        t = st.get("ticker")
        if not t:
            print(f"  cron refresh: {sf.name} has no ticker, skipped")
            continue
        tickers.add(t)
    tickers.add("SPY")  # shadow benchmark stays fresh
    for t in sorted(tickers):
        try:
            do_prices(t)
            counts["refreshed"] += 1
        except Exception as e:
            counts["errors"] += 1
            print(f"  cron refresh failed for {t}: {e}")


# ---------------------------------------------------------------- main
MAX_FETCH_ATTEMPTS = 3


def request_due(req, is_cron):
    """PENDING always; FAILED again on cron until MAX_FETCH_ATTEMPTS is spent.

    A FAILED row used to be terminal: the loop only ever picked up PENDING, and the
    30-day prune only removes FULFILLED, so a request that lost a race with a flaky
    SEC endpoint stayed dead forever and looked exactly like one that was permanently
    impossible. Retries are cron-only on purpose — a push-triggered bridge round-trip
    is a session waiting on ONE batch, and it should not spend that window re-running
    yesterday's failures.
    """
    st = req.get("status")
    if st == "PENDING":
        return True
    if st == "FAILED" and is_cron:
        return int(req.get("attempts") or 1) < MAX_FETCH_ATTEMPTS
    return False


def main():
    is_cron = "--cron" in sys.argv or os.environ.get("GITHUB_EVENT_NAME") == "schedule"
    req_path = DATA / "requests.json"
    reqs = jload(req_path, {"version": 1, "requests": []})
    counts = {"processed": 0, "fulfilled": 0, "failed": 0, "refreshed": 0, "errors": 0,
              "retried": 0}
    trips = []

    # Explicit CIK assertions first, from every row in the file, so an override on
    # any row for a ticker governs every kind fetched for it this run (quality reads
    # what fundamentals wrote; prices and pcs stamp the same identity).
    for req in reqs.get("requests", []):
        if req.get("ticker") and req.get("cik"):
            register_cik_override(req["ticker"], req["cik"])

    for req in reqs.get("requests", []):
        if not request_due(req, is_cron):
            continue
        if req.get("status") == "FAILED":
            counts["retried"] += 1
            print(f"RETRY {req['id']} (attempt {int(req.get('attempts') or 1) + 1}"
                  f"/{MAX_FETCH_ATTEMPTS})")
        req["attempts"] = int(req.get("attempts") or 0) + 1
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
            elif k == "web_doc":
                wrote = do_web_doc(req["url"])
            else:
                raise RuntimeError(f"unknown kind {k}")
            req["status"] = "FULFILLED"
            req["wrote"] = wrote
            req["fulfilled_at"] = NOW.isoformat()
            req.pop("terminal", None)
            counts["fulfilled"] += 1
            print(f"FULFILLED {req['id']} ({k} {req.get('ticker') or req.get('query') or req.get('url')})")
        except Exception as e:
            req["status"] = "FAILED"
            req["note"] = str(e)[:300]
            req["last_attempt_at"] = NOW.isoformat()
            counts["failed"] += 1
            if req["attempts"] >= MAX_FETCH_ATTEMPTS:
                # Out of retries. Said on the row rather than inferred from a count,
                # so a session reading requests.json can tell "still coming" from
                # "this will never arrive" without knowing the retry policy.
                req["terminal"] = True
                print(f"FAILED {req['id']} (terminal after {req['attempts']} attempts): {e}")
            else:
                print(f"FAILED {req['id']} (attempt {req['attempts']}"
                      f"/{MAX_FETCH_ATTEMPTS}, will retry on next cron): {e}")

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
