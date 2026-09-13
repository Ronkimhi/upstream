"""The front door's two build projections (Ron, 2026-09-13).

`shadow_summary` splits the shadow book's hit rate per origin, because the page used to pool
one number across every origin and the day a second origin landed it would have mixed
Stocky's TOO_LATE number with Ember's OVER_CROWDED number. `build_top` is the Top 3 block;
its ranking lives in tools/opportunities.py and is tested there, so here the test is only
that the build carries the block with its denominators and no run date.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))

import build  # noqa: E402


def row(rid, origin, ticker, expression=None, link="l1"):
    r = {"id": rid, "ticker": ticker, "origin": origin, "verdict_date": "2026-08-30",
         "spot": {"value": 10.0}, "review_at": "2026-11-28"}
    if origin == "HEAT_OVER_CROWDED":
        r.update({"chain_id": "alpha", "link_id": link, "expression": expression or "ISSUER"})
    return r


class TestShadowSummary(unittest.TestCase):
    def test_hit_rates_are_per_origin_never_pooled(self):
        book = {"rows": [row("a", "DIVE_TOO_LATE", "VRT"), row("b", "HEAT_OVER_CROWDED", "FRO"),
                         row("c", "HEAT_OVER_CROWDED", "DHT"), row("d", "HEAT_OVER_CROWDED", "XFUT", "INSTRUMENT")]}
        results = {"a": {"call": "WRONG", "delta_pct": 12.0}, "b": {"call": "RIGHT", "delta_pct": -8.0},
                   "c": {"call": "RIGHT", "delta_pct": -4.0}, "d": {"call": "WRONG", "delta_pct": 300.0}}
        summary = build.shadow_summary(book, results)
        self.assertEqual(0, summary["by_origin"]["DIVE_TOO_LATE"]["hit_rate"])
        self.assertEqual(67, summary["by_origin"]["HEAT_OVER_CROWDED"]["hit_rate"])
        self.assertEqual(3, summary["by_origin"]["HEAT_OVER_CROWDED"]["graded"])
        link = summary["by_link"][0]
        self.assertEqual(-6.0, link["median_delta_pct"])
        self.assertEqual({"ticker": "XFUT", "delta_pct": 300.0, "call": "WRONG"}, link["instrument"])
        self.assertEqual(4, summary["rows_total"])

    def test_an_ungraded_row_is_awaiting_not_right(self):
        summary = build.shadow_summary({"rows": [row("a", "DIVE_TOO_LATE", "VRT")]}, {})
        self.assertIsNone(summary["by_origin"]["DIVE_TOO_LATE"]["hit_rate"])
        self.assertEqual(0, summary["by_origin"]["DIVE_TOO_LATE"]["graded"])

    def test_an_empty_book_is_an_empty_summary(self):
        summary = build.shadow_summary({"rows": []}, {})
        self.assertEqual({}, summary["by_origin"])
        self.assertEqual([], summary["by_link"])


class TestBuildTop(unittest.TestCase):
    def test_the_block_carries_its_denominators_and_no_run_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "chains").mkdir(parents=True)
            top = build.build_top(root / "data", chains=[], stocks=[])
        for key in ("top", "ranked_total", "links_total", "unrankable_total", "instruments_total",
                    "instruments_unrated_total", "unrankable_by_reason", "generated_by"):
            self.assertIn(key, top)
        self.assertEqual([], top["top"])
        self.assertNotIn("as_of", top)
        self.assertNotIn("generated_at", top)


if __name__ == "__main__":
    unittest.main()
