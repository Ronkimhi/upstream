#!/usr/bin/env python3
"""The cron-only scheduled price refresh, broadened 2026-09-13.

`refresh_dive_tickers` (the function this file used to test) covered only Stocky's ~14
live dive tickers plus SPY, which left most of the 460-file data/market/ corpus to go
stale indefinitely: the scheduled run never looked at it. It is now `refresh_market_tickers`
and covers every ticker with a data/market/<T>.json file, oldest series first, bounded to
fit inside the 30-minute job timeout (a hard job-level timeout kills the runner outright --
no amount of guarding inside this file can catch that, so the bound has to be real).

What each test guards:
  - _market_ticker_staleness reads every market file's own staleness, oldest first, and
    a file with no `ticker` at all (a shape this repo has actually shipped, e.g. an
    agent-store JSON dropped in the wrong place) is skipped rather than fatal.
  - select_price_refresh_tickers (the pure "selection" BUILD item 2 asks to be unit
    tested) orders oldest-first, de-dupes, and slices to fit an estimated budget, naming
    the remainder rather than silently dropping it.
  - refresh_market_tickers always attempts SPY first regardless of its own staleness (the
    shadow benchmark must stay current every run), keeps going past one ticker's failure,
    and actually stops early on the real wall clock when the budget runs out -- the
    static slice is a plan, the live clock is the guarantee.

Run: python3 -m unittest discover -s tools/tests -q
"""
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def load_fetch():
    spec = importlib.util.spec_from_file_location(
        "fetch_cron_mod", ROOT / "tools" / "fetch" / "fetch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("requests", type(sys)("requests"))
    spec.loader.exec_module(mod)
    return mod


class TestMarketTickerStaleness(unittest.TestCase):
    def setUp(self):
        self.mod = load_fetch()
        self.td = tempfile.TemporaryDirectory()
        self.data = Path(self.td.name) / "data"
        (self.data / "market").mkdir(parents=True)
        self.mod.DATA = self.data

    def tearDown(self):
        self.td.cleanup()

    def write(self, name, obj):
        (self.data / "market" / name).write_text(json.dumps(obj))

    def test_staleness_key_prefers_series_as_of(self):
        self.write("AAA.json", {"ticker": "AAA", "fetched_at": "2026-09-10T00:00:00+00:00",
                                "series": {"as_of": "2026-09-01"}})
        keyed = {t: k for k, t in self.mod._market_ticker_staleness()}
        self.assertEqual(keyed["AAA"], "2026-09-01")

    def test_falls_back_to_fetched_at_then_empty(self):
        self.write("BBB.json", {"ticker": "BBB", "fetched_at": "2026-09-05T00:00:00+00:00"})
        self.write("CCC.json", {"ticker": "CCC"})
        keyed = {t: k for k, t in self.mod._market_ticker_staleness()}
        self.assertEqual(keyed["BBB"], "2026-09-05T00:00:00+00:00")
        self.assertEqual(keyed["CCC"], "")

    def test_a_file_with_no_ticker_is_skipped_not_fatal(self):
        """A shape this repo has actually shipped: an agent store or malformed file
        sitting where a market file is expected. One bad file must never break the read."""
        self.write("GOOD.json", {"ticker": "GOOD", "fetched_at": "2026-09-01T00:00:00+00:00"})
        self.write("_weird-store.json", {"not_a_ticker_field": True})
        tickers = {t for _k, t in self.mod._market_ticker_staleness()}
        self.assertEqual(tickers, {"GOOD"})


class TestSelectPriceRefreshTickers(unittest.TestCase):
    """The pure selection BUILD item 2 calls out for a unit test: no disk, no network,
    no clock."""

    def setUp(self):
        self.mod = load_fetch()

    def test_oldest_first(self):
        cands = [("2026-06-01", "FRESH"), ("2020-01-01", "STALE"), ("2023-01-01", "MID")]
        selected, remainder = self.mod.select_price_refresh_tickers(
            cands, budget_seconds=9000, per_ticker_seconds=1)
        self.assertEqual(selected, ["STALE", "MID", "FRESH"])
        self.assertEqual(remainder, 0)

    def test_empty_staleness_key_sorts_first_without_inventing_a_date(self):
        cands = [("2020-01-01", "HAS_DATE"), ("", "NEVER_FETCHED")]
        selected, _ = self.mod.select_price_refresh_tickers(
            cands, budget_seconds=9000, per_ticker_seconds=1)
        self.assertEqual(selected[0], "NEVER_FETCHED")

    def test_duplicate_tickers_are_deduped_keeping_the_oldest_occurrence(self):
        cands = [("2020-01-01", "DUP"), ("2026-01-01", "DUP"), ("2021-01-01", "OTHER")]
        selected, _ = self.mod.select_price_refresh_tickers(
            cands, budget_seconds=9000, per_ticker_seconds=1)
        self.assertEqual(selected.count("DUP"), 1)
        self.assertEqual(selected, ["DUP", "OTHER"])

    def test_slice_fits_the_budget_and_names_the_remainder(self):
        cands = [(f"2020-01-{i:02d}", f"T{i}") for i in range(1, 11)]  # 10 candidates
        selected, remainder = self.mod.select_price_refresh_tickers(
            cands, budget_seconds=45, per_ticker_seconds=9)  # room for exactly 5
        self.assertEqual(len(selected), 5)
        self.assertEqual(remainder, 5)
        self.assertEqual(selected, [f"T{i}" for i in range(1, 6)], "must stay oldest-first")

    def test_nothing_is_dropped_silently_when_it_all_fits(self):
        cands = [("2020-01-01", "A"), ("2021-01-01", "B")]
        selected, remainder = self.mod.select_price_refresh_tickers(
            cands, budget_seconds=900, per_ticker_seconds=9)
        self.assertEqual(set(selected), {"A", "B"})
        self.assertEqual(remainder, 0)

    def test_the_module_defaults_are_internally_consistent(self):
        """The worst-case-per-ticker constant must stay bigger than the typical one --
        it exists specifically to be the more conservative of the two."""
        self.assertGreater(self.mod.PRICE_REFRESH_WORST_CASE_SECONDS_PER_TICKER,
                           self.mod.PRICE_REFRESH_TYPICAL_SECONDS_PER_TICKER)
        self.assertLess(self.mod.PRICE_REFRESH_BUDGET_SECONDS, 1800,
                        "must leave room under the 30-minute job ceiling for everything else")


class TestRefreshMarketTickers(unittest.TestCase):
    def setUp(self):
        self.mod = load_fetch()
        self.td = tempfile.TemporaryDirectory()
        self.data = Path(self.td.name) / "data"
        (self.data / "market").mkdir(parents=True)
        self.mod.DATA = self.data
        self.priced = []
        self.mod.do_prices = lambda t: self.priced.append(t)

    def tearDown(self):
        self.td.cleanup()

    def write(self, name, obj):
        (self.data / "market" / name).write_text(json.dumps(obj))

    def run_it(self):
        counts = {"refreshed": 0, "errors": 0}
        self.mod.refresh_market_tickers(counts)
        return counts

    def test_spy_is_always_attempted_first(self):
        """Preserved from the pre-2026-09-13 behaviour: the shadow sweep needs a current
        benchmark every run, not merely when SPY happens to be the stalest file."""
        self.write("VRT.json", {"ticker": "VRT", "fetched_at": "2020-01-01T00:00:00+00:00"})
        self.run_it()
        self.assertEqual(self.priced[0], "SPY")
        self.assertIn("VRT", self.priced)

    def test_oldest_market_files_go_first_not_only_dive_tickers(self):
        """The core of BUILD item 2: every data/market/ file is a candidate, not only a
        dive ticker, and the stalest goes first."""
        self.write("NEW.json", {"ticker": "NEW", "fetched_at": "2026-09-12T00:00:00+00:00"})
        self.write("OLD.json", {"ticker": "OLD", "fetched_at": "2020-01-01T00:00:00+00:00"})
        self.run_it()
        after_spy = [t for t in self.priced if t != "SPY"]
        self.assertEqual(after_spy.index("OLD") < after_spy.index("NEW"), True)

    def test_a_ticker_failure_does_not_stop_the_run(self):
        def flaky(t):
            if t == "BOOM":
                raise RuntimeError("simulated network failure")
            self.priced.append(t)
        self.mod.do_prices = flaky
        self.write("BOOM.json", {"ticker": "BOOM", "fetched_at": "2020-01-01T00:00:00+00:00"})
        self.write("FINE.json", {"ticker": "FINE", "fetched_at": "2020-01-02T00:00:00+00:00"})
        counts = self.run_it()
        self.assertIn("FINE", self.priced)
        self.assertEqual(counts["errors"], 1)

    def test_the_remainder_is_recorded_on_counts(self):
        for i in range(3):
            self.write(f"T{i}.json", {"ticker": f"T{i}",
                                      "fetched_at": f"2020-01-0{i+1}T00:00:00+00:00"})
        self.mod.PRICE_REFRESH_BUDGET_SECONDS = 9  # room for exactly 1 at 9s/ticker
        counts = {"refreshed": 0, "errors": 0}
        self.mod.refresh_market_tickers(counts)
        self.assertGreater(counts["refresh_remainder"], 0)

    def test_the_real_clock_stops_the_loop_even_if_the_static_estimate_was_optimistic(self):
        """The static slice is a plan; the live wall-clock check is the actual guarantee.
        A fake clock simulates each ticker call taking far longer than the typical
        estimate assumed, and the loop must still stop before blowing the budget."""
        clock = {"t": 0.0}
        fake_time = types.SimpleNamespace(monotonic=lambda: clock["t"],
                                          sleep=lambda s: None, time=lambda: clock["t"])
        self.mod.time = fake_time

        def slow_price(t):
            clock["t"] += 400  # one "call" burns nearly half the whole budget
            self.priced.append(t)
        self.mod.do_prices = slow_price
        for i in range(5):
            self.write(f"T{i}.json", {"ticker": f"T{i}",
                                      "fetched_at": f"2020-01-0{i+1}T00:00:00+00:00"})
        counts = {"refreshed": 0, "errors": 0}
        self.mod.refresh_market_tickers(counts)
        # budget is 900s; SPY + a couple of 400s calls must exhaust it well before all
        # 6 candidates (SPY + T0..T4) are attempted.
        self.assertLess(len(self.priced), 6)
        self.assertGreater(counts["refresh_remainder"], 0)


if __name__ == "__main__":
    unittest.main()
