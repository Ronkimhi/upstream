"""Refusal tests for method section 7's upward check and the re-anchored would-buy zone.

These exist because `tools/check_machine.py` counts gates that have never been watched
refusing anything, and because the rules under test were added (2026-09-08) to fix the
opposite problem: every mechanism in check_analyst.py capped a verdict DOWNWARD and none
had ever questioned a WATCH, so 14 dives closed 1 INVESTABLE / 13 WATCH / 0 TOO_LATE.
A rule that only ever loosens is worth less than no rule, so each test below asserts the
refusal AND its matching pass.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from check_analyst import upward_check_failures, would_buy_failures  # noqa: E402


def watch(**over):
    """A WATCH that has cleared every downward cap: grade B, independence answered."""
    d = {
        "verdict": "WATCH",
        "earnings_quality": {"grade": "B", "basis": "cash conversion 94%"},
        "independence_test": {"largest_disagreement": "The street models flat volume.",
                              "why_the_gap_exists": "Orphaned coverage after the spin.",
                              "falsification": "Q3 backlog print on 2026-11-04."},
        "zone_contains_spot": True,
        "would_buy_zone": {"low": 90.0, "high": 110.0, "basis": "bear case clears here",
                           "as_of": "2026-09-08"},
        "price_ref": {"value": 100.0},
    }
    d.update(over)
    return d


class TestUpwardCheck(unittest.TestCase):
    def test_refuses_a_cleared_watch_with_no_watch_basis(self):
        """The whole point: a WATCH that cleared every cap must say what still binds."""
        out = upward_check_failures(watch())
        self.assertEqual(len(out), 1, out)
        self.assertIn("watch_basis", out[0])

    def test_passes_when_the_cap_is_named(self):
        self.assertEqual(
            upward_check_failures(watch(watch_basis="Position size: 60% of revenue is one "
                                                    "customer and the contract renews in 2027.")),
            [])

    def test_silent_when_a_downward_cap_is_genuinely_binding(self):
        """Grade C already caps at WATCH, so the upward check must not second-guess it."""
        self.assertEqual(
            upward_check_failures(watch(earnings_quality={"grade": "C", "basis": "DSO drift"})),
            [])

    def test_silent_when_the_independence_test_is_unanswered(self):
        d = watch()
        d["independence_test"]["falsification"] = "  "
        self.assertEqual(upward_check_failures(d), [])

    def test_silent_when_price_is_outside_the_zone(self):
        """A WATCH whose price is above its own bear-case floor is an ordinary WATCH."""
        self.assertEqual(upward_check_failures(watch(zone_contains_spot=False)), [])

    def test_silent_on_investable_and_too_late(self):
        for verdict in ("INVESTABLE", "TOO_LATE"):
            self.assertEqual(upward_check_failures(watch(verdict=verdict)), [], verdict)


class TestZoneContainsSpot(unittest.TestCase):
    def test_a_zone_containing_spot_is_allowed_but_must_be_declared(self):
        """Before 2026-09-08 this was refused outright, which is what made every WATCH
        permanent: the zone could not contain today's price on the day it was written."""
        d = watch()
        del d["zone_contains_spot"]
        out = would_buy_failures(d)
        self.assertTrue(any("zone_contains_spot" in f for f in out), out)

    def test_declared_correctly_passes(self):
        self.assertEqual(would_buy_failures(watch()), [])

    def test_refuses_a_lie_about_containment(self):
        out = would_buy_failures(watch(zone_contains_spot=False))
        self.assertTrue(any("IS inside" in f for f in out), out)

    def test_refuses_a_lie_the_other_way(self):
        d = watch(price_ref={"value": 200.0}, zone_contains_spot=True)
        out = would_buy_failures(d)
        self.assertTrue(any("is not inside" in f for f in out), out)

    def test_zone_below_spot_still_valid(self):
        """The old shape stays legal: a bear-case floor below today's price is normal."""
        d = watch(price_ref={"value": 200.0}, zone_contains_spot=False)
        self.assertEqual(would_buy_failures(d), [])


if __name__ == "__main__":
    unittest.main()
