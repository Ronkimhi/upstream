#!/usr/bin/env python3
"""Remaining performance obligations, a new fundamentals field (BUILD item 5, 2026-09-13).

The transaction price allocated to performance obligations not yet satisfied as of the
period end (ASC 606 / IFRS 15 contracted-but-unrecognised revenue). Written as ONE
point-in-time fact {value, unit, period_end, concept, taxonomy}, never a series: unlike
every *_fy field, this is never unioned across concept renames and never filled between
periods the filer itself did not tag (method's "never interpolated" rule).

us-gaap concept confirmed 2026-09-13 by WebSearch (task instruction: never from memory):
RevenueRemainingPerformanceObligation. ifrs-full: no confirmed core taxonomy element after
eight separate searches (see fetch.py's own comment beside RPO_CONCEPTS) -- left empty
rather than guessed, so an ifrs-full filer's field is always None, not a fabricated tag.

What each test guards:
  - the field is written from a companyfacts fixture, exactly the shape (value, unit,
    period_end, concept, taxonomy) the task asks for.
  - interim forms count (RPO is disclosed every 10-Q, not only annually) and the LATEST
    period end wins; a non-qualifying form (an 8-K) is never a candidate.
  - never interpolated: with two disjoint periods on file, the single latest one is
    returned verbatim -- nothing is synthesized for the gap between them.
  - a filer that never tagged the concept gets None, not an absent key and not a guess.
  - ifrs-full always reads None, because RPO_CONCEPTS["ifrs-full"] is deliberately empty.

No network: `fetch.requests` is a stub answering from an in-memory companyfacts payload.

Run: python3 -m unittest discover -s tools/tests -q
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch = _load("fetch_rpo_mod", ROOT / "tools" / "fetch" / "fetch.py")


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _SecRequests:
    def __init__(self, payload):
        self.urls = []
        self._payload = payload

    def get(self, url, **k):
        self.urls.append(url)
        return _FakeResponse(self._payload)


REVENUE_ONLY = {"Revenues": {"units": {"USD": [
    {"form": "10-K", "start": "2024-01-01", "end": "2024-12-31", "val": 999.0},
    {"form": "10-K", "start": "2025-01-01", "end": "2025-12-31", "val": 1111.0},
]}}}


def _facts(us_gaap_extra=None, ifrs_full=None):
    gaap = dict(REVENUE_ONLY)
    if us_gaap_extra:
        gaap.update(us_gaap_extra)
    return {"facts": {"us-gaap": gaap, "ifrs-full": ifrs_full or {}, "dei": {}}}


class RpoTestCase(unittest.TestCase):
    def setUp(self):
        self._data, self._cik, self._req = fetch.DATA, fetch.cik_for, fetch.requests
        self._td = tempfile.TemporaryDirectory()
        fetch.DATA = Path(self._td.name) / "data"
        fetch._edgar_probes.clear()

    def tearDown(self):
        fetch.DATA, fetch.cik_for, fetch.requests = self._data, self._cik, self._req
        fetch._edgar_probes.clear()
        self._td.cleanup()

    def written(self, ticker):
        path = fetch.DATA / "market" / f"{fetch.safe_name(ticker)}.json"
        return json.loads(path.read_text())["fundamentals"]


class TestPureExtraction(unittest.TestCase):
    """_remaining_performance_obligation directly: no disk, no network."""

    def test_the_latest_interim_period_wins(self):
        gaap = {"RevenueRemainingPerformanceObligation": {"units": {"USD": [
            {"form": "10-Q", "end": "2025-06-30", "val": 4200.0},
            {"form": "10-K", "end": "2025-12-31", "val": 5300.0},
        ]}}}
        out = fetch._remaining_performance_obligation("us-gaap", gaap, {})
        self.assertEqual(out, {"value": 5300.0, "unit": "USD", "period_end": "2025-12-31",
                               "concept": "RevenueRemainingPerformanceObligation",
                               "taxonomy": "us-gaap"})

    def test_a_non_interim_form_is_not_a_candidate(self):
        """An 8-K figure (not a periodic report) must never win even if it is dated
        later than every real candidate."""
        gaap = {"RevenueRemainingPerformanceObligation": {"units": {"USD": [
            {"form": "10-K", "end": "2025-12-31", "val": 5300.0},
            {"form": "8-K", "end": "2026-01-10", "val": 9999.0},
        ]}}}
        out = fetch._remaining_performance_obligation("us-gaap", gaap, {})
        self.assertEqual(out["value"], 5300.0)
        self.assertEqual(out["period_end"], "2025-12-31")

    def test_never_interpolated_disjoint_periods_return_only_the_latest_verbatim(self):
        """Two periods with a large gap between them: nothing is synthesized to fill it,
        and the single value returned is exactly the filer's own latest disclosure."""
        gaap = {"RevenueRemainingPerformanceObligation": {"units": {"USD": [
            {"form": "10-K", "end": "2021-12-31", "val": 100.0},
            {"form": "10-K", "end": "2025-12-31", "val": 7000.0},
        ]}}}
        out = fetch._remaining_performance_obligation("us-gaap", gaap, {})
        self.assertEqual(out["value"], 7000.0)
        self.assertEqual(out["period_end"], "2025-12-31")
        self.assertIsInstance(out["value"], float)  # a single fact, never a list/series

    def test_absent_concept_is_none(self):
        self.assertIsNone(fetch._remaining_performance_obligation("us-gaap", {}, {}))

    def test_ifrs_full_is_always_none(self):
        """RPO_CONCEPTS['ifrs-full'] is deliberately empty (see fetch.py's own comment):
        no confirmed core taxonomy element after eight WebSearch queries. A filer with a
        same-named-looking ifrs concept must still read None, never a guessed match."""
        ifrs = {"RevenueRemainingPerformanceObligation": {"units": {"EUR": [
            {"form": "20-F", "end": "2025-12-31", "val": 42.0}]}}}
        self.assertIsNone(fetch._remaining_performance_obligation("ifrs-full", {}, ifrs))
        self.assertEqual(fetch.RPO_CONCEPTS["ifrs-full"], [])


class TestFundamentalsFixtureIntegration(RpoTestCase):
    """The task's own ask: 'Unit test with a companyfacts fixture.'"""

    def test_written_from_a_companyfacts_fixture(self):
        fetch.requests = _SecRequests(_facts(us_gaap_extra={
            "RevenueRemainingPerformanceObligation": {"units": {"USD": [
                {"form": "10-Q", "end": "2025-06-30", "val": 4200.0},
                {"form": "10-K", "end": "2025-12-31", "val": 5300.0},
            ]}}}))
        fetch.cik_for = lambda t: 320193
        fetch.do_fundamentals("AAPL")
        f = self.written("AAPL")
        self.assertEqual(f["remaining_performance_obligations"],
                         {"value": 5300.0, "unit": "USD", "period_end": "2025-12-31",
                          "concept": "RevenueRemainingPerformanceObligation",
                          "taxonomy": "us-gaap"})

    def test_key_is_present_but_none_when_the_filer_never_tagged_it(self):
        """Matches the cash/total_debt convention (present, None) rather than the *_fy
        series convention (key absent) -- RPO is a single-latest-value field like those
        two, not a scored series."""
        fetch.requests = _SecRequests(_facts())
        fetch.cik_for = lambda t: 320193
        fetch.do_fundamentals("AAPL")
        f = self.written("AAPL")
        self.assertIn("remaining_performance_obligations", f)
        self.assertIsNone(f["remaining_performance_obligations"])

    def test_ifrs_filer_reads_none_not_a_fabricated_concept(self):
        ifrs_facts = {"facts": {"us-gaap": {}, "ifrs-full": {
            "Revenue": {"units": {"AUD": [
                {"form": "20-F", "start": "2024-07-01", "end": "2025-06-30", "val": 55000.0}]}},
        }, "dei": {}}}
        fetch.requests = _SecRequests(ifrs_facts)
        fetch.cik_for = lambda t: 811809
        fetch.do_fundamentals("BHP")
        f = self.written("BHP")
        self.assertEqual(f["taxonomy"], "ifrs-full")
        self.assertIsNone(f["remaining_performance_obligations"])


if __name__ == "__main__":
    unittest.main()
