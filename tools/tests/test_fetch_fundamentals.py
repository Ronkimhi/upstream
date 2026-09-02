#!/usr/bin/env python3
"""Unit tests for the two-leg fundamentals fetch (SEC companyfacts + yfinance fallback).

Added 2026-08-30 with the fallback leg itself. Before it, `do_fundamentals` raised
"no SEC CIK for <T>" for every non-US listing, so 16 requests were FAILED and 19 of 28
files in data/market/ were price-only — most of a globally-constructed issuer census with
no fundamentals, no pcs and no quality block.

What each test guards:
  - the leg is chosen by the CIK and nothing else, in BOTH directions. An SEC filer must
    never silently read a vendor aggregate instead of its own filings, and a foreign
    listing must never fall back into a SEC path that cannot serve it.
  - the emitted field names are asserted against `fetch.QUALITY_FIELDS` ITSELF, not
    against a copied list. On 2026-08-29 `fetch.QUALITY_FIELDS` and `acis.quality`'s
    `series_fields` drifted by one name and every Piotroski and Beneish score silently
    reported PENDING_DATA with all inputs apparently present (CLAUDE.md postlude 1c). A
    future rename of any field must break this test rather than degrade the scores.
  - the three honesty markers survive. A vendor aggregate is not a filing; source,
    tag INFERRED and official_source false are what keep it out of a filing citation.
  - the verified_zero rule holds on the vendor plane too: an empty result is only
    writable when a same-run control probe proved the endpoint answers at all.

No network. `yfinance` is injected into sys.modules as a stub and `fetch.requests` is
rebound, so nothing here can reach SEC or Yahoo even by accident.

The statement frames are hand-built stubs rather than pandas DataFrames because CI
installs only `requests` for the test step (.github/workflows/ci.yml). They implement
exactly the surface `_yf_rows` uses, and that surface was read off a live yfinance
response (1072.HK, 2026-08-30) rather than from documentation.

Run: python3 -m unittest discover -s tools/tests -q
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch = _load("fetch_fund_mod", ROOT / "tools" / "fetch" / "fetch.py")


# --------------------------------------------------------------- pandas-free stub frames
class _Row:
    """One statement line item across periods. Mirrors a pandas Series row."""

    ndim = 1

    def __init__(self, pairs):
        self._pairs = list(pairs)

    def items(self):
        return iter(self._pairs)


class _Frame:
    """The slice of the pandas DataFrame surface `_yf_rows` actually touches:
    `.empty`, `.index`, `.columns`, `.shape`, and `.loc[label]`."""

    class _Loc:
        def __init__(self, frame):
            self._f = frame

        def __getitem__(self, label):
            return _Row(zip(self._f.columns, self._f._data[label]))

    def __init__(self, data, columns):
        self._data = dict(data)
        self.columns = list(columns)
        self.loc = _Frame._Loc(self)

    @property
    def index(self):
        return list(self._data)

    @property
    def empty(self):
        return not self._data

    @property
    def shape(self):
        return (len(self._data), len(self.columns))


PERIODS = ["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]

# One label per field, taken from the FIRST candidate in each YF_FACT_MAP entry, so a full
# statement set covers all 20 QUALITY_FIELDS. Values are arbitrary but distinct.
FULL_INCOME = {
    "Total Revenue": [100.0, 110.0, 120.0, 130.0],
    "Net Income": [10.0, 11.0, 12.0, 13.0],
    "Operating Income": [20.0, 21.0, 22.0, 23.0],
    "Gross Profit": [30.0, 31.0, 32.0, 33.0],
    "Cost Of Revenue": [70.0, 79.0, 88.0, 97.0],
    "Selling General And Administration": [5.0, 5.1, 5.2, 5.3],
}
FULL_BALANCE = {
    "Total Assets": [1000.0, 1100.0, 1200.0, 1300.0],
    "Current Assets": [400.0, 410.0, 420.0, 430.0],
    "Current Liabilities": [300.0, 310.0, 320.0, 330.0],
    "Total Liabilities Net Minority Interest": [600.0, 610.0, 620.0, 630.0],
    "Retained Earnings": [200.0, 210.0, 220.0, 230.0],
    "Stockholders Equity": [400.0, 490.0, 580.0, 670.0],
    "Accounts Receivable": [50.0, 51.0, 52.0, 53.0],
    "Inventory": [60.0, 61.0, 62.0, 63.0],
    "Net PPE": [150.0, 151.0, 152.0, 153.0],
    "Ordinary Shares Number": [1000.0, 1000.0, 1010.0, 1010.0],
    "Long Term Debt": [90.0, 91.0, 92.0, 93.0],
    "Cash And Cash Equivalents": [70.0, 71.0, 72.0, 73.0],
    "Total Debt": [120.0, 121.0, 122.0, 123.0],
}
FULL_CASH = {
    "Operating Cash Flow": [40.0, 41.0, 42.0, 43.0],
    # Yahoo signs capex as a cash OUTFLOW. The stored field must come back positive,
    # matching the SEC leg's PaymentsToAcquirePropertyPlantAndEquipment.
    "Capital Expenditure": [-15.0, -16.0, -17.0, -18.0],
    "Depreciation And Amortization": [8.0, 8.1, 8.2, 8.3],
}
FULL_INFO = {"financialCurrency": "EUR", "currency": "EUR",
             "impliedSharesOutstanding": 1010.0, "marketCap": 5050.0}


class _StubTicker:
    def __init__(self, income=None, balance=None, cash=None, info=None):
        self.income_stmt = _Frame(income if income is not None else FULL_INCOME, PERIODS)
        self.balance_sheet = _Frame(balance if balance is not None else FULL_BALANCE, PERIODS)
        self.cashflow = _Frame(cash if cash is not None else FULL_CASH, PERIODS)
        self.info = dict(FULL_INFO if info is None else info)


def _install_yf(ticker_obj, calls=None):
    """Put a stub `yfinance` in sys.modules. Returns the module so a test can assert on it."""
    mod = types.ModuleType("yfinance")

    def Ticker(symbol):  # noqa: N802 - mirrors the real yfinance name
        if calls is not None:
            calls.append(symbol)
        return ticker_obj
    mod.Ticker = Ticker
    sys.modules["yfinance"] = mod
    return mod


class _ExplodingRequests:
    """Any HTTP call at all is a test failure: these tests must never reach a network."""

    def get(self, *a, **k):
        raise AssertionError(f"network call attempted: {a[:1]}")


SEC_FACTS = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
    {"form": "10-K", "start": "2024-01-01", "end": "2024-12-31", "val": 999.0},
    {"form": "10-K", "start": "2025-01-01", "end": "2025-12-31", "val": 1111.0},
]}}}, "dei": {}}}


IFRS_FACTS = {"facts": {"us-gaap": {}, "ifrs-full": {
    "Revenue": {"units": {"AUD": [
        {"form": "20-F", "start": "2023-07-01", "end": "2024-06-30", "val": 51000.0},
        {"form": "20-F", "start": "2024-07-01", "end": "2025-06-30", "val": 55000.0},
    ]}},
    "Assets": {"units": {"AUD": [
        {"form": "20-F", "end": "2024-06-30", "val": 95000.0},
        {"form": "20-F", "end": "2025-06-30", "val": 100000.0},
    ]}},
}, "dei": {}}}


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _SecRequests:
    def __init__(self, payload=None):
        self.urls = []
        self._payload = SEC_FACTS if payload is None else payload

    def get(self, url, **k):
        self.urls.append(url)
        return _FakeResponse(self._payload)


class FundamentalsLegTestCase(unittest.TestCase):
    """Shared plumbing: a temp DATA root and restoration of everything monkeypatched."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._data, self._cik, self._req = fetch.DATA, fetch.cik_for, fetch.requests
        self._yf = sys.modules.get("yfinance")
        fetch.DATA = Path(self._tmp.name)
        fetch.requests = _ExplodingRequests()
        fetch._edgar_probes.clear()
        # The fetcher narrates loudly by design. Captured rather than silenced, so a test
        # can assert on the narration and a failing run can still print it.
        self.out = io.StringIO()
        self._stdout = contextlib.redirect_stdout(self.out)
        self._stdout.__enter__()

    def tearDown(self):
        self._stdout.__exit__(None, None, None)
        fetch.DATA, fetch.cik_for, fetch.requests = self._data, self._cik, self._req
        if self._yf is None:
            sys.modules.pop("yfinance", None)
        else:
            sys.modules["yfinance"] = self._yf
        fetch._edgar_probes.clear()
        self._tmp.cleanup()

    def written(self, ticker):
        path = fetch.DATA / "market" / f"{fetch.safe_name(ticker)}.json"
        return json.loads(path.read_text())["fundamentals"]


class TestLegSelection(FundamentalsLegTestCase):
    """The CIK is the only thing that chooses a leg. Both directions are load-bearing:
    a foreign listing must not hit a SEC path that cannot serve it, and an SEC filer must
    not silently read a vendor aggregate instead of its own filings."""

    def test_fallback_fires_only_when_there_is_no_cik(self):
        calls = []
        _install_yf(_StubTicker(), calls)
        fetch.cik_for = lambda t: None
        # fetch.requests explodes on any call, so reaching SEC here fails the test.
        fetch.do_fundamentals("1072.HK")
        f = self.written("1072.HK")
        self.assertEqual(f["source"], "yfinance-statements")
        self.assertEqual(calls, ["1072.HK"], "the vendor leg must ask for the exact ticker")

    def test_sec_path_is_untouched_when_the_sec_leg_finds_fields(self):
        """A filer whose companyfacts carry facts never reads a vendor aggregate."""
        sec = _SecRequests()
        fetch.requests = sec
        fetch.cik_for = lambda t: 320193

        def _boom(symbol):
            raise AssertionError("the vendor leg ran for a ticker whose SEC leg had facts")
        yf = types.ModuleType("yfinance")
        yf.Ticker = _boom
        sys.modules["yfinance"] = yf

        fetch.do_fundamentals("AAPL")
        f = self.written("AAPL")
        self.assertEqual(f["source"], "sec-companyfacts")
        self.assertEqual(f["taxonomy"], "us-gaap")
        self.assertEqual(f["statement_currency"], "USD")
        self.assertEqual(f["cik"], 320193)
        self.assertEqual(list(f["revenue_fy"][-1]), ["2025-12-31", 1111.0])
        self.assertTrue(any("companyfacts" in u for u in sec.urls), sec.urls)

    def test_ifrs_20f_filer_is_read_from_ifrs_full_in_its_own_currency(self):
        """BHP's shape: a CIK, zero us-gaap facts, everything under ifrs-full in AUD."""
        sec = _SecRequests(IFRS_FACTS)
        fetch.requests = sec
        fetch.cik_for = lambda t: 811809

        def _boom(symbol):
            raise AssertionError("the vendor leg ran for an IFRS filer whose SEC leg had facts")
        yf = types.ModuleType("yfinance")
        yf.Ticker = _boom
        sys.modules["yfinance"] = yf

        fetch.do_fundamentals("BHP")
        f = self.written("BHP")
        self.assertEqual(f["source"], "sec-companyfacts")
        self.assertEqual(f["taxonomy"], "ifrs-full")
        self.assertEqual(f["statement_currency"], "AUD")
        self.assertEqual(list(f["revenue_fy"][-1]), ["2025-06-30", 55000.0])
        self.assertEqual(list(f["total_assets_fy"][-1]), ["2025-06-30", 100000.0])
        self.assertEqual(f["coverage"]["annual_fields_found"], 2)

    def test_empty_companyfacts_with_a_cik_falls_through_to_the_vendor_leg(self):
        """The 2026-09-01 defect: a CIK whose companyfacts are empty under both
        taxonomies was a dead end. Now the vendor leg runs, INFERRED, with the SEC attempt
        recorded and the CIK kept so tier and identity survive."""
        sec = _SecRequests({"facts": {"us-gaap": {}, "dei": {}}})
        fetch.requests = sec
        fetch.cik_for = lambda t: 811809
        fetch._edgar_probes["facts"] = True
        fetch._edgar_probes["yf_statements"] = True
        calls = []
        _install_yf(_StubTicker(), calls)

        fetch.do_fundamentals("BHP")
        f = self.written("BHP")
        self.assertEqual(f["source"], "yfinance-statements")
        self.assertEqual(f["tag"], "INFERRED")
        self.assertIs(f["official_source"], False)
        self.assertEqual(f["cik"], 811809)
        self.assertEqual(f["sec_attempted"]["annual_fields_found"], 0)
        self.assertEqual(f["sec_attempted"]["cik"], 811809)
        self.assertEqual(calls, ["BHP"])
        self.assertIn("falling through to yfinance-statements", self.out.getvalue())

    def test_tier_is_t3_for_a_vendor_leg_name(self):
        """tier_for(ticker, None) is T3 — data availability, not opportunity tier."""
        _install_yf(_StubTicker())
        fetch.cik_for = lambda t: None
        fetch.do_fundamentals("ANDR.VI")
        m = json.loads((fetch.DATA / "market" / "ANDR-VI.json").read_text())
        self.assertEqual(m["tier"], "T3")


class TestFieldNames(FundamentalsLegTestCase):
    """The 2026-08-29 schema-drift incident, as a test. Assertions are against
    `fetch.QUALITY_FIELDS` itself so a rename anywhere breaks here loudly."""

    def test_map_covers_exactly_quality_fields(self):
        self.assertEqual(set(fetch.YF_FACT_MAP), set(fetch.QUALITY_FIELDS),
                         "YF_FACT_MAP and QUALITY_FIELDS have drifted apart")

    def test_emitted_keys_are_exactly_the_quality_field_series(self):
        _install_yf(_StubTicker())
        fetch.cik_for = lambda t: None
        fetch.do_fundamentals("ANDR.VI")
        f = self.written("ANDR.VI")
        emitted = {k for k in f if k.endswith("_fy")}
        self.assertEqual(emitted, {f"{k}_fy" for k in fetch.QUALITY_FIELDS})
        for key in fetch.QUALITY_FIELDS:
            rows = f[f"{key}_fy"]
            self.assertEqual(len(rows), len(PERIODS), key)
            for period, value in rows:
                self.assertRegex(period, r"^\d{4}-\d{2}-\d{2}$", key)
                self.assertIsInstance(value, float, key)
            self.assertEqual([p for p, _ in rows], sorted(p for p, _ in rows),
                             f"{key}: rows must be oldest-first like the SEC leg's")

    def test_capex_is_normalised_to_the_sec_sign_convention(self):
        """Yahoo signs capex negative (outflow); the SEC concept is a positive payment.
        One stored field may not mean two different things depending on the leg."""
        _install_yf(_StubTicker())
        fetch.cik_for = lambda t: None
        fetch.do_fundamentals("ANDR.VI")
        self.assertEqual([v for _, v in self.written("ANDR.VI")["capex_fy"]],
                         [15.0, 16.0, 17.0, 18.0])

    def test_ev_inputs_match_the_sec_leg_shape(self):
        """do_quality reads fundamentals['cash'] and ['total_debt'] as single latest rows."""
        _install_yf(_StubTicker())
        fetch.cik_for = lambda t: None
        fetch.do_fundamentals("ANDR.VI")
        f = self.written("ANDR.VI")
        self.assertEqual(f["cash"], [["2025-12-31", 73.0]])
        self.assertEqual(f["total_debt"], [["2025-12-31", 123.0]])


class TestHonestyMarkers(FundamentalsLegTestCase):
    """A vendor aggregate is not a filing (method section 6A). These three fields are what
    keep a Yahoo number out of a filing citation, so they are asserted literally."""

    def test_the_three_markers(self):
        _install_yf(_StubTicker())
        fetch.cik_for = lambda t: None
        fetch.do_fundamentals("1072.HK")
        f = self.written("1072.HK")
        self.assertEqual(f["source"], "yfinance-statements")
        self.assertEqual(f["tag"], "INFERRED")
        self.assertIs(f["official_source"], False)
        self.assertIsNone(f["cik"])

    def test_coverage_accounting_matches_the_sec_leg(self):
        income = dict(FULL_INCOME)
        income.pop("Gross Profit")
        _install_yf(_StubTicker(income=income))
        fetch.cik_for = lambda t: None
        fetch.do_fundamentals("1072.HK")
        cov = self.written("1072.HK")["coverage"]
        self.assertEqual(cov["annual_fields_attempted"], len(fetch.QUALITY_FIELDS))
        self.assertEqual(cov["annual_fields_found"], len(fetch.QUALITY_FIELDS) - 1)
        self.assertEqual(cov["missing"], ["gross_profit"])
        self.assertNotIn("gross_profit_fy", self.written("1072.HK"),
                         "a field with no rows must be ABSENT, never an empty list scored "
                         "as zero (method section 1)")

    def test_currencies_are_recorded_when_they_differ(self):
        _install_yf(_StubTicker(info={**FULL_INFO, "financialCurrency": "CNY",
                                      "currency": "HKD"}))
        fetch.cik_for = lambda t: None
        fetch.do_fundamentals("1072.HK")
        f = self.written("1072.HK")
        self.assertEqual((f["statement_currency"], f["price_currency"]), ("CNY", "HKD"))

    def test_interim_series_is_empty_with_a_stated_reason(self):
        """Vendor interim statements are not reliably quarterly for a foreign filer, so
        the SEC leg's `revenue_q` name would be a mislabel rather than data."""
        _install_yf(_StubTicker())
        fetch.cik_for = lambda t: None
        fetch.do_fundamentals("ANDR.VI")
        f = self.written("ANDR.VI")
        self.assertEqual(f["revenue_q"], [])
        self.assertTrue(f["interim_note"])


class TestVerifiedZero(FundamentalsLegTestCase):
    """The verified_zero rule, on the plane the read came from. An empty result may only be
    written when a same-run control probe proved the endpoint answers at all."""

    def test_empty_result_fails_when_the_control_probe_fails(self):
        empty = _StubTicker(income={}, balance={}, cash={}, info={})
        yf = _install_yf(empty)

        def Ticker(symbol):  # noqa: N802
            if symbol == fetch.YF_CONTROL_TICKER:
                raise RuntimeError("Yahoo fundamentals endpoint down")
            return empty
        yf.Ticker = Ticker
        fetch.cik_for = lambda t: None
        with self.assertRaises(RuntimeError) as ctx:
            fetch.do_fundamentals("0390.HK")
        self.assertIn("control probe failed", str(ctx.exception))

    def test_empty_result_is_stamped_verified_zero_when_the_probe_passes(self):
        empty = _StubTicker(income={}, balance={}, cash={}, info={})
        control = _StubTicker()
        yf = _install_yf(empty)
        yf.Ticker = lambda s: control if s == fetch.YF_CONTROL_TICKER else empty
        fetch.cik_for = lambda t: None
        fetch.do_fundamentals("0390.HK")
        f = self.written("0390.HK")
        self.assertTrue(f["verified_zero"]["probe"].startswith("yfinance-"))
        self.assertEqual(f["coverage"]["annual_fields_found"], 0)
        self.assertIn("control probe OK, so this is coverage, not an outage", self.out.getvalue())

    def test_a_source_that_did_not_answer_records_why(self):
        """A leg that swallows its failure is an unfalsifiable source (dual_source.py)."""
        empty = _StubTicker(income={}, balance={}, cash={}, info={})
        control = _StubTicker()
        yf = _install_yf(empty)
        yf.Ticker = lambda s: control if s == fetch.YF_CONTROL_TICKER else empty
        fetch.cik_for = lambda t: None
        fetch.do_fundamentals("0390.HK")
        legs = self.written("0390.HK")["legs"]
        for name in ("income", "balance", "cash"):
            self.assertFalse(legs[name]["answered"], name)
            self.assertTrue(legs[name]["reason"], f"{name} leg gave no reason")


class TestQualityCurrencyGuard(FundamentalsLegTestCase):
    """Statements in one currency and a price in another make market cap and enterprise
    value cross-currency arithmetic. 1072.HK reports CNY and trades HKD, so Altman Z and
    the reverse DCF would be confidently wrong. NULL with a basis, never a converted guess.

    `acis.quality` is stubbed because CI installs neither pandas nor financetoolkit for the
    test step; the guard under test lives in fetch.do_quality and runs before that call.
    """

    def setUp(self):
        super().setUp()
        self._quality = sys.modules.get("acis.quality")
        self.seen = {}
        stub = types.ModuleType("acis.quality")

        def compute_quality(fund, market_cap=None, enterprise_value=None, as_of=None):
            self.seen = {"market_cap": market_cap, "enterprise_value": enterprise_value}
            return {"piotroski": {"score": 1}, "beneish": {"score": None},
                    "altman": {"score": None}, "reverse_dcf": {"implied_fcf_cagr": None,
                                                               "state": "PENDING_DATA"}}
        stub.compute_quality = compute_quality
        sys.modules["acis.quality"] = stub

    def tearDown(self):
        if self._quality is None:
            sys.modules.pop("acis.quality", None)
        else:
            sys.modules["acis.quality"] = self._quality
        super().tearDown()

    def _market(self, ticker, fundamentals):
        path = fetch.DATA / "market" / f"{fetch.safe_name(ticker)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "ticker": ticker, "price_status": "SINGLE_SOURCE", "prints": [],
            "series": {"rows": [["2026-08-28", 20.0]], "source": "yfinance",
                       "as_of": "2026-08-28"},
            "fundamentals": fundamentals}))
        return path

    def _fundamentals(self, stmt_ccy, px_ccy):
        return {"source": "yfinance-statements", "tag": "INFERRED", "official_source": False,
                "statement_currency": stmt_ccy, "price_currency": px_ccy,
                "shares_fy": [["2025-12-31", 100.0]],
                "cash": [["2025-12-31", 10.0]], "total_debt": [["2025-12-31", 30.0]]}

    def test_mismatched_currencies_null_the_market_cap(self):
        self._market("1072.HK", self._fundamentals("CNY", "HKD"))
        fetch.do_quality("1072.HK")
        self.assertIsNone(self.seen["market_cap"])
        self.assertIsNone(self.seen["enterprise_value"])
        q = json.loads((fetch.DATA / "market" / "1072-HK.json").read_text())["quality"]
        self.assertIn("CNY", q["currency_note"])
        self.assertIs(q["official_source"], False)
        self.assertEqual(q["source_tag"], "INFERRED")

    def test_matching_currencies_still_compute(self):
        self._market("ANDR.VI", self._fundamentals("EUR", "EUR"))
        fetch.do_quality("ANDR.VI")
        self.assertEqual(self.seen["market_cap"], 2000.0)
        self.assertEqual(self.seen["enterprise_value"], 2020.0)
        q = json.loads((fetch.DATA / "market" / "ANDR-VI.json").read_text())["quality"]
        self.assertIsNone(q["currency_note"])

    def test_sec_leg_is_unaffected_by_the_guard(self):
        """The SEC leg writes neither currency field, so the guard can never fire on it."""
        self._market("AAPL", {"source": "sec-companyfacts", "cik": 320193,
                              "shares_fy": [["2025-12-31", 100.0]],
                              "cash": [["2025-12-31", 10.0]],
                              "total_debt": [["2025-12-31", 30.0]]})
        fetch.do_quality("AAPL")
        self.assertEqual(self.seen["market_cap"], 2000.0)
        q = json.loads((fetch.DATA / "market" / "AAPL.json").read_text())["quality"]
        self.assertIsNone(q["currency_note"])
        self.assertIs(q["official_source"], True)
        self.assertEqual(q["source_tag"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()
