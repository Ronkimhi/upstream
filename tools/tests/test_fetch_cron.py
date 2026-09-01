#!/usr/bin/env python3
"""The cron-only branch of the fetch plane, which no test covered.

Every push-triggered run skips `refresh_dive_tickers`; only the schedule fires it. So a
crash in there is invisible to CI, invisible to every manual dispatch, and shows up once a
day as a failed workflow whose feed batch had already succeeded. That is exactly what
happened: `data/stocks/_dive-log.json` is Stocky's agent store and carries no `ticker`, the
function did `st["ticker"]` unguarded, and the KeyError killed the run AFTER 385 feed items
had been fetched. The intake corpus sat frozen from 2026-08-29 to 2026-09-01 and two radar
runs swept a corpus that could not change.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def load_fetch():
    spec = importlib.util.spec_from_file_location(
        "fetch_mod", ROOT / "tools" / "fetch" / "fetch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("requests", type(sys)("requests"))
    spec.loader.exec_module(mod)
    return mod


class TestRefreshDiveTickers(unittest.TestCase):
    def setUp(self):
        self.mod = load_fetch()
        self.td = tempfile.TemporaryDirectory()
        self.data = Path(self.td.name) / "data"
        (self.data / "stocks").mkdir(parents=True)
        self.mod.DATA = self.data
        self.priced = []
        self.mod.do_prices = lambda t: self.priced.append(t)

    def tearDown(self):
        self.td.cleanup()

    def write(self, name, obj):
        (self.data / "stocks" / name).write_text(json.dumps(obj))

    def run_it(self):
        counts = {"refreshed": 0, "errors": 0}
        self.mod.refresh_dive_tickers(counts)
        return counts

    def test_an_agent_store_does_not_kill_the_cron(self):
        """The exact 2026-09-01 failure: _dive-log.json has no ticker."""
        self.write("VRT__ai-infrastructure.json", {"ticker": "VRT", "status": "FINAL"})
        self.write("_dive-log.json", {"as_of": "2026-09-01", "dives": []})
        counts = self.run_it()
        self.assertIn("VRT", self.priced)
        self.assertNotIn(None, self.priced)
        self.assertEqual(0, counts["errors"])

    def test_a_dive_with_no_ticker_is_skipped_not_fatal(self):
        """One malformed file must never cost a whole scheduled run."""
        self.write("GOOD__chain.json", {"ticker": "AAA", "status": "FINAL"})
        self.write("BROKEN__chain.json", {"status": "FINAL"})
        self.run_it()
        self.assertIn("AAA", self.priced)

    def test_archived_and_fixture_dives_are_still_excluded(self):
        self.write("OLD__chain.json", {"ticker": "OLD", "status": "ARCHIVED"})
        self.write("FIX__chain.json", {"ticker": "FIX", "fixture": True})
        self.write("LIVE__chain.json", {"ticker": "LIVE", "status": "FINAL"})
        self.run_it()
        self.assertIn("LIVE", self.priced)
        self.assertNotIn("OLD", self.priced)
        self.assertNotIn("FIX", self.priced)

    def test_the_shadow_benchmark_is_always_refreshed(self):
        self.write("_dive-log.json", {"as_of": "2026-09-01"})
        self.run_it()
        self.assertIn("SPY", self.priced)


if __name__ == "__main__":
    unittest.main()
