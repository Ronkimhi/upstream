#!/usr/bin/env python3
"""NON_US_SUFFIX_TELLS widened with .NS .KS .TW .OL (BUILD item 4, 2026-09-13).

score_axis_a() forces COVERAGE-THIN on any ticker whose suffix is in this tuple, because
PCS's retail-attention axis is a US-locals scope cut, not a data-quality judgement -- see
crowdedness.py's own comment above the tuple. A suffix missing from this list is silently
scoreable as DARK/EMERGING/CROWDED and, unlike a genuinely thin US name, would be
machine-admissible on a scope method explicitly excludes: NSE (India), KRX (South Korea),
TWSE (Taiwan) and Oslo Børs (Norway) listings were exactly that gap before this fix.

Pure, offline: score_axis_a takes plain dicts, no network and no repo state.

Run: python3 -m unittest discover -s tools/tests -q
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from acis.crowdedness import NON_US_SUFFIX_TELLS, score_axis_a  # noqa: E402

# Fields chosen to score DARK (100/100, all four non-null) when the ticker carries no
# non-US suffix -- so a test failure here means the suffix, not the field shape, is what
# is (or is not) forcing COVERAGE-THIN.
DARK_FIELDS = {
    "trends": {"multiple": 1.0, "no_history": False},
    "wsb": {"rank": None, "mentions_30d": 0},
    "stocktwits": {"velocity": 0.5, "watchers": 100},
    "media": {"article_count_90d": 0},
}

NEW_SUFFIXES = (".NS", ".KS", ".TW", ".OL")
PRE_EXISTING_SUFFIXES = (".PA", ".SW", ".ST", ".SS", ".SZ", ".L", ".DE", ".MI",
                         ".AS", ".BR", ".LS", ".T", ".HK", ".TO", ".AX")


class TestSuffixListMembership(unittest.TestCase):
    def test_the_four_new_suffixes_are_present(self):
        for suffix in NEW_SUFFIXES:
            self.assertIn(suffix, NON_US_SUFFIX_TELLS)

    def test_the_pre_existing_fourteen_are_untouched(self):
        for suffix in PRE_EXISTING_SUFFIXES:
            self.assertIn(suffix, NON_US_SUFFIX_TELLS)

    def test_no_accidental_duplicates(self):
        self.assertEqual(len(NON_US_SUFFIX_TELLS), len(set(NON_US_SUFFIX_TELLS)))


class TestNewSuffixesForceCoverageThin(unittest.TestCase):
    def test_control_ticker_with_no_suffix_scores_dark(self):
        """Proves the field shape earns DARK on its own, so the suffix tests below are
        actually testing the suffix, not a fixture that could never score DARK anyway."""
        out = score_axis_a(DARK_FIELDS, ticker="RELIANCE")
        self.assertEqual(out["state"], "DARK")
        self.assertTrue(out["machine_admissible"])

    def test_ns_india_is_never_machine_admissible(self):
        out = score_axis_a(DARK_FIELDS, ticker="RELIANCE.NS")
        self.assertEqual(out["state"], "COVERAGE-THIN")
        self.assertFalse(out["machine_admissible"])

    def test_ks_south_korea_is_never_machine_admissible(self):
        out = score_axis_a(DARK_FIELDS, ticker="005930.KS")
        self.assertEqual(out["state"], "COVERAGE-THIN")
        self.assertFalse(out["machine_admissible"])

    def test_tw_taiwan_is_never_machine_admissible(self):
        out = score_axis_a(DARK_FIELDS, ticker="2330.TW")
        self.assertEqual(out["state"], "COVERAGE-THIN")
        self.assertFalse(out["machine_admissible"])

    def test_ol_norway_is_never_machine_admissible(self):
        out = score_axis_a(DARK_FIELDS, ticker="EQNR.OL")
        self.assertEqual(out["state"], "COVERAGE-THIN")
        self.assertFalse(out["machine_admissible"])

    def test_the_check_is_case_and_position_correct_not_a_loose_substring(self):
        """A ticker that merely CONTAINS one of these letters must not be caught -- the
        rule is a trailing suffix, not a substring anywhere in the ticker."""
        out = score_axis_a(DARK_FIELDS, ticker="NSTAR")  # no ".NS" suffix, just starts with NS
        self.assertEqual(out["state"], "DARK")
        self.assertTrue(out["machine_admissible"])


if __name__ == "__main__":
    unittest.main()
