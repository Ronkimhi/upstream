#!/usr/bin/env python3
"""The would-buy zone on a WATCH (Ron, 2026-09-03), watched refusing.

A WATCH must say at what price this file's own thesis would have been INVESTABLE, or name
the cap that makes price irrelevant. These tests are the evidence `tools/check_machine.py`
asks for: a gate that has actually refused something."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_analyst  # noqa: E402


def watch(**over):
    d = {
        "verdict": "WATCH",
        "price_ref": {"value": 100.0, "source": "series", "as_of": "2026-09-03"},
        "watch_triggers": [{"metric": "m", "level": "1", "direction": "below"}],
        "changelog": [{"ts": "2026-09-03T10:00:00Z", "by": "stocky", "change": "x"}],
    }
    d.update(over)
    return d


class WouldBuyGate(unittest.TestCase):
    def test_refuses_watch_with_no_zone_and_no_basis(self):
        findings = check_analyst.would_buy_failures(watch())
        self.assertEqual(len(findings), 1)
        self.assertIn("without would_buy_zone", findings[0])

    def test_refuses_null_zone_with_empty_basis(self):
        findings = check_analyst.would_buy_failures(watch(would_buy_zone=None))
        self.assertTrue(any("would_buy_basis is empty" in f for f in findings))

    def test_refuses_null_zone_whose_basis_names_no_cap(self):
        findings = check_analyst.would_buy_failures(
            watch(would_buy_zone=None, would_buy_basis="hard to say"))
        self.assertTrue(any("names none of the caps" in f for f in findings))

    def test_accepts_null_zone_naming_the_cap(self):
        findings = check_analyst.would_buy_failures(
            watch(would_buy_zone=None,
                  would_buy_basis="grade C caps the verdict at WATCH at any price"))
        self.assertEqual(findings, [])

    def test_zone_containing_spot_must_declare_it(self):
        """Re-anchored 2026-09-08. This test used to assert that a zone whose high sat at
        or above spot was REFUSED. Under base-case anchoring that rule made a WATCH
        permanent by construction: the zone could not contain today's price on the day it
        was written, and all nine drawn zones landed 10.4%-163.6% below spot with none ever
        reached. Method section 7 now anchors the zone to the bear case, where a zone
        containing spot is information (the bear case clears here), so the gate asks the
        dive to DECLARE it rather than refusing it."""
        findings = check_analyst.would_buy_failures(
            watch(would_buy_zone={"low": 90.0, "high": 100.0, "basis": "b"}))
        self.assertTrue(any("does not declare zone_contains_spot" in f for f in findings),
                        findings)

    def test_zone_containing_spot_is_accepted_once_declared(self):
        findings = check_analyst.would_buy_failures(
            watch(would_buy_zone={"low": 90.0, "high": 110.0, "basis": "bear case clears",
                                  "as_of": "2026-09-08"},
                  zone_contains_spot=True), last_close=100.0)
        self.assertEqual(findings, [])

    def test_refuses_a_false_containment_claim(self):
        findings = check_analyst.would_buy_failures(
            watch(would_buy_zone={"low": 70.0, "high": 85.0, "basis": "b"},
                  zone_contains_spot=True), last_close=100.0)
        self.assertTrue(any("is not inside" in f for f in findings), findings)

    def test_refuses_zone_with_empty_basis_or_inverted_bounds(self):
        f1 = check_analyst.would_buy_failures(
            watch(would_buy_zone={"low": 80.0, "high": 90.0, "basis": ""}))
        f2 = check_analyst.would_buy_failures(
            watch(would_buy_zone={"low": 90.0, "high": 80.0, "basis": "b"}))
        self.assertTrue(any("basis is empty" in f for f in f1))
        self.assertTrue(any("is not below high" in f for f in f2))

    def test_refuses_zone_outside_sanity_band(self):
        findings = check_analyst.would_buy_failures(
            watch(would_buy_zone={"low": 10.0, "high": 20.0, "basis": "b"}), last_close=100.0)
        self.assertTrue(any("outside 0.3x-2x" in f for f in findings))

    def test_accepts_a_valid_zone(self):
        """A bear-case floor below today's price stays the ordinary shape; it now declares
        zone_contains_spot: False rather than leaving containment implied."""
        findings = check_analyst.would_buy_failures(
            watch(would_buy_zone={"low": 70.0, "high": 85.0, "basis": "revenue row closes",
                                  "as_of": "2026-09-03"},
                  zone_contains_spot=False), last_close=100.0)
        self.assertEqual(findings, [])

    def test_non_watch_is_out_of_scope(self):
        self.assertEqual(check_analyst.would_buy_failures(watch(verdict="INVESTABLE")), [])

    def test_pre_gate_dives_are_dated_by_their_last_changelog_entry(self):
        old = watch(changelog=[{"ts": "2026-09-02T23:00:00Z"}])
        new = watch(changelog=[{"ts": "2026-09-02T23:00:00Z"}, {"ts": "2026-09-03T01:00:00Z"}])
        self.assertLess(check_analyst.latest_changelog_date(old), check_analyst.WOULD_BUY_GATE)
        self.assertGreaterEqual(check_analyst.latest_changelog_date(new),
                                check_analyst.WOULD_BUY_GATE)

    def test_reached_only_counts_closes_after_the_zone_was_drawn(self):
        zone = {"low": 70.0, "high": 85.0, "basis": "b", "as_of": "2026-09-03"}
        before = [["2026-09-01", 80.0]]
        after = [["2026-09-01", 95.0], ["2026-09-10", 84.0]]
        self.assertFalse(check_analyst.would_buy_reached(zone, before))
        self.assertTrue(check_analyst.would_buy_reached(zone, after))


if __name__ == "__main__":
    unittest.main()
