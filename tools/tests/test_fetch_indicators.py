#!/usr/bin/env python3
"""The indicator label key, which killed six scheduled fetches.

`eval_indicators` read `ind["indicator"]` and nothing else. `check_scenarios.py` accepts
`signal` or `name` and `validate.py` requires no label key at all, so Ember has been
writing `signal` for months: 141 of the 173 armed indicators on disk carry it. The read
therefore raised KeyError at the END of the run, after every price, filing and page had
already been fetched, and the crash took the commit step with it. Runs 90 and 91
(2026-09-08 and 2026-09-09, scheduled, on main) fetched correctly and committed nothing.

That is the same shape as the failure `CLAUDE.md` records for 2026-09-02 to 2026-09-04
("Fetched data always lands"), which was closed by making the UI rebuild best-effort. It
reopened one step earlier, inside fetch.py itself, where continue-on-error cannot reach.

Two failures of one rule is Adam's promotion bar, so it is a test rather than a memory.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _load_fetch():
    spec = importlib.util.spec_from_file_location(
        "fetch_under_test", ROOT / "tools" / "fetch" / "fetch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fetch_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _chain(indicator_field, ticker="ZZZZ"):
    return {
        "id": "test-chain", "signal_id": "SIG-00000000-00", "title": "t",
        "clock": "SLOW", "status": "MAPPED", "links": [], "map_limitation": "x",
        "scenarios": [{
            "id": "S1", "title": "s", "narrative": "n", "probability_pct": 100,
            "links_moved": [], "invalidation_signs": ["x"], "status": "OPEN",
            "leading_indicators": [{
                **indicator_field,
                "armed": True,
                "check": {"type": "price", "ticker": ticker, "op": ">=", "level": 1.0},
            }],
        }],
    }


class TestIndicatorLabelKey(unittest.TestCase):
    """Every label spelling the gate accepts must survive eval_indicators."""

    def _run(self, chain):
        fetch = _load_fetch()
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "chains").mkdir(parents=True)
            (data / "chains" / "test-chain.json").write_text(json.dumps(chain))
            (data / "indicators.json").write_text(json.dumps({"trips": []}))
            fetch.DATA = data
            trips = []
            fetch.eval_indicators(trips)          # must not raise
            return trips, json.loads((data / "indicators.json").read_text())

    def test_signal_key_does_not_crash(self):
        """The spelling 141 of 173 armed indicators actually use."""
        self._run(_chain({"signal": "DLR prints above its 52 week high"}))

    def test_indicator_key_still_works(self):
        """The other 32 keep working: this is a widening, not a rename."""
        self._run(_chain({"indicator": "CAT reclaims 900"}))

    def test_name_key_does_not_crash(self):
        """check_scenarios accepts `name` too, so the fetcher must not be stricter."""
        self._run(_chain({"name": "ETR prints above its 52 week high"}))

    def test_no_label_is_skipped_loudly_not_fatal(self):
        """A malformed indicator costs one printed line, never the whole batch."""
        trips, inds = self._run(_chain({}))
        self.assertEqual(trips, [])
        self.assertEqual(inds["trips"], [])

    def test_corpus_on_disk_evaluates(self):
        """The real chains, which is the case that actually broke.

        Reads the live corpus rather than a fixture: a fixture that passes while
        `data/chains/` crashes is exactly the gate Rule 21 exists to catch.
        """
        fetch = _load_fetch()
        fetch.DATA = ROOT / "data"
        armed = 0
        for cf in sorted((ROOT / "data" / "chains").glob("*.json")):
            if cf.name.startswith("_"):
                continue
            chain = json.loads(cf.read_text())
            for sc in chain.get("scenarios") or []:
                for ind in sc.get("leading_indicators") or []:
                    if isinstance(ind, dict) and ind.get("check") and ind.get("armed"):
                        armed += 1
        self.assertGreater(armed, 0, "no armed indicator on disk: this test proved nothing")
        fetch.eval_indicators([])                 # must not raise
        print(f"  eval_indicators: {armed} armed indicator(s) on disk, none fatal")


if __name__ == "__main__":
    unittest.main()
