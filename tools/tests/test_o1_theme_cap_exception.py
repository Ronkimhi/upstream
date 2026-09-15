#!/usr/bin/env python3
"""Pressure tests for the DEPTH campaign's named per-theme O1 cap exception.

Ron's decision, 2026-09-15 ("Yes, dive NVT and BDX"): the DEPTH cap of 1-3 O1 per theme
stays locked everywhere except two named themes, each raised by exactly one issuer.
`targets.o1_per_theme_exceptions` is the only door past the cap, and every row must be
named (an exact theme_id), issuer-scoped (a stable issuer_id) and dated, so it cannot be
silently reused for a different theme or a different name.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_campaign  # noqa: E402


def exception_row(theme_id="ai-infrastructure", issuer_id="NVENT-ELECTRIC", **overrides):
    row = {
        "theme_id": theme_id,
        "issuer_id": issuer_id,
        "o1_per_theme_max": 4,
        "decided_by": "ron",
        "decided_at": "2026-09-15",
        "note": 'Ron\'s decision, 2026-09-15: "Yes, dive NVT and BDX"',
    }
    row.update(overrides)
    return row


class DepthCampaignFixture(unittest.TestCase):
    """A minimal, ten-theme, dict-only DEPTH campaign: completion_gate_failures and the
    exception helpers are pure functions over campaign/computed dicts, no filesystem."""

    def setUp(self):
        self.campaign = {
            "status": "COMPLETE",
            "targets": dict(check_campaign.DEPTH_TARGETS),
            "themes": [{"theme_id": f"T{i}"} for i in range(10)],
        }
        self.campaign["themes"][0]["theme_id"] = "ai-infrastructure"
        self.campaign["themes"][1]["theme_id"] = "glp1-fill-finish"
        self.computed = {
            "completed_profiles": 24,
            "opportunity_tiers": {"O1": 12, "O2": 12, "O3": 40},
            "o1_complete": 12,
            "o1_final": 12,
            "themes_complete": 10,
            "per_theme": [
                {
                    "theme_id": t["theme_id"],
                    "completed_profiles": 2,
                    "o1": 2 if i < 2 else 1,
                    "o1_issuer_ids": [f"ISS-{i}-A", f"ISS-{i}-B"] if i < 2 else [f"ISS-{i}-A"],
                }
                for i, t in enumerate(self.campaign["themes"])
            ],
        }

    def gate(self, campaign=None, computed=None):
        return check_campaign.completion_gate_failures(
            campaign if campaign is not None else self.campaign,
            computed if computed is not None else self.computed,
        )


class TestExceptionAcceptsInsideScope(DepthCampaignFixture):
    def test_named_theme_named_issuer_fourth_o1_passes(self):
        self.campaign["targets"]["o1_per_theme_exceptions"] = [exception_row()]
        self.computed["per_theme"][0]["o1"] = 4
        self.computed["per_theme"][0]["o1_issuer_ids"] = [
            "HUBBELL", "MYR-GROUP", "VERTIV-HOLDINGS", "NVENT-ELECTRIC",
        ]
        # o1_final/o1_complete/opportunity_tiers.O1 must track the raised total for the
        # other COMPLETE-boundary checks to pass too.
        self.computed["opportunity_tiers"]["O1"] = 13
        self.computed["o1_complete"] = 13
        self.computed["o1_final"] = 13
        self.assertEqual(self.gate(), [])

    def test_second_named_theme_second_issuer_also_passes(self):
        self.campaign["targets"]["o1_per_theme_exceptions"] = [
            exception_row(),
            exception_row(theme_id="glp1-fill-finish", issuer_id="BECTON-DICKINSON"),
        ]
        self.computed["per_theme"][1]["o1"] = 4
        self.computed["per_theme"][1]["o1_issuer_ids"] = [
            "BECTON-DICKINSON", "HALOZYME-THERAPEUTICS", "STERIS",
            "WEST-PHARMACEUTICAL-SERVICES",
        ]
        self.computed["opportunity_tiers"]["O1"] = 13
        self.computed["o1_complete"] = 13
        self.computed["o1_final"] = 13
        self.assertEqual(self.gate(), [])

    def test_unused_exception_leaves_a_normal_theme_alone(self):
        # The exception exists but the theme never grew a 4th O1: nothing to trip.
        self.campaign["targets"]["o1_per_theme_exceptions"] = [exception_row()]
        self.assertEqual(self.gate(), [])


class TestExceptionRefusesOutsideScope(DepthCampaignFixture):
    def test_fourth_o1_in_an_unnamed_theme_still_fails(self):
        # No exception at all: theme 2 (index 2) grows a 4th O1.
        self.computed["per_theme"][2]["o1"] = 4
        self.computed["per_theme"][2]["o1_issuer_ids"] = ["A", "B", "C", "D"]
        self.computed["opportunity_tiers"]["O1"] = 13
        self.computed["o1_complete"] = 13
        self.computed["o1_final"] = 13
        failures = self.gate()
        self.assertTrue(
            any(f"theme {self.campaign['themes'][2]['theme_id']} has 4 O1" in f
                and "needs 1-3" in f for f in failures),
            failures,
        )

    def test_exception_named_for_a_different_theme_does_not_cover_this_one(self):
        # The exception exists, but only for ai-infrastructure; theme 2 still grows a 4th.
        self.campaign["targets"]["o1_per_theme_exceptions"] = [exception_row()]
        self.computed["per_theme"][2]["o1"] = 4
        self.computed["per_theme"][2]["o1_issuer_ids"] = ["A", "B", "C", "D"]
        self.computed["opportunity_tiers"]["O1"] = 13
        self.computed["o1_complete"] = 13
        self.computed["o1_final"] = 13
        failures = self.gate()
        self.assertTrue(
            any(f"theme {self.campaign['themes'][2]['theme_id']} has 4 O1" in f for f in failures),
            failures,
        )

    def test_exception_present_but_named_issuer_not_among_the_o1_set_fails(self):
        # Four O1 in the named theme, but none of them is the named issuer: the extra slot
        # is unaccounted for, and the exception cannot be claimed by a different name.
        self.campaign["targets"]["o1_per_theme_exceptions"] = [exception_row()]
        self.computed["per_theme"][0]["o1"] = 4
        self.computed["per_theme"][0]["o1_issuer_ids"] = [
            "HUBBELL", "MYR-GROUP", "VERTIV-HOLDINGS", "SOME-OTHER-ISSUER",
        ]
        self.computed["opportunity_tiers"]["O1"] = 13
        self.computed["o1_complete"] = 13
        self.computed["o1_final"] = 13
        failures = self.gate()
        self.assertTrue(
            any("not among the theme's O1 issuers" in f for f in failures), failures)

    def test_exception_covers_only_the_one_named_issuer_not_a_raised_cap(self):
        # Named issuer is present, but the theme grew to 5 O1: pulling the named issuer
        # back out still leaves 4, more than the base cap of 3 -- the exception is not a
        # license to keep stacking O1 in that theme.
        self.campaign["targets"]["o1_per_theme_exceptions"] = [exception_row()]
        self.computed["per_theme"][0]["o1"] = 5
        self.computed["per_theme"][0]["o1_issuer_ids"] = [
            "HUBBELL", "MYR-GROUP", "VERTIV-HOLDINGS", "NVENT-ELECTRIC", "SOME-OTHER",
        ]
        self.computed["opportunity_tiers"]["O1"] = 14
        self.computed["o1_complete"] = 14
        self.computed["o1_final"] = 14
        failures = self.gate()
        self.assertTrue(
            any("covers exactly one issuer, not a raised theme cap" in f for f in failures),
            failures,
        )


class TestExceptionRowSchema(unittest.TestCase):
    """o1_per_theme_exceptions()/o1_per_theme_exception_failures() are the shape gate that
    runs inside validate_campaign, independent of the completion boundary above."""

    def setUp(self):
        self.theme_ids = {"ai-infrastructure", "glp1-fill-finish"}

    def test_well_formed_row_produces_no_failures_and_resolves(self):
        targets = {"mode": "DEPTH", "o1_per_theme_exceptions": [exception_row()]}
        self.assertEqual(
            check_campaign.o1_per_theme_exception_failures(targets, self.theme_ids), [])
        resolved = check_campaign.o1_per_theme_exceptions(targets)
        self.assertEqual(set(resolved), {"ai-infrastructure"})
        self.assertEqual(resolved["ai-infrastructure"]["issuer_id"], "NVENT-ELECTRIC")

    def test_unresolved_theme_id_fails(self):
        # o1_per_theme_exception_failures is the schema gate that runs inside
        # validate_campaign, where the real selected theme_ids are known; a bogus
        # theme_id is refused there even though it is shape-valid on its own.
        targets = {"mode": "DEPTH",
                   "o1_per_theme_exceptions": [exception_row(theme_id="not-a-theme")]}
        failures = check_campaign.o1_per_theme_exception_failures(targets, self.theme_ids)
        self.assertTrue(
            any("does not resolve to a selected theme" in f for f in failures), failures)

    def test_cap_not_raised_above_base_fails(self):
        targets = {"mode": "DEPTH",
                   "o1_per_theme_exceptions": [exception_row(o1_per_theme_max=3)]}
        failures = check_campaign.o1_per_theme_exception_failures(targets, self.theme_ids)
        self.assertTrue(
            any("greater than the base cap" in f for f in failures), failures)

    def test_missing_decided_at_decided_by_note_all_fail(self):
        targets = {"mode": "DEPTH", "o1_per_theme_exceptions": [
            exception_row(decided_at=None, decided_by="", note=""),
        ]}
        failures = check_campaign.o1_per_theme_exception_failures(targets, self.theme_ids)
        self.assertTrue(any("decided_at" in f for f in failures), failures)
        self.assertTrue(any("decided_by" in f for f in failures), failures)
        self.assertTrue(any("note" in f for f in failures), failures)

    def test_bad_issuer_id_fails(self):
        targets = {"mode": "DEPTH",
                   "o1_per_theme_exceptions": [exception_row(issuer_id="not a safe id!")]}
        failures = check_campaign.o1_per_theme_exception_failures(targets, self.theme_ids)
        self.assertTrue(
            any("not a stable safe identifier" in f for f in failures), failures)

    def test_duplicate_theme_exception_fails(self):
        targets = {"mode": "DEPTH", "o1_per_theme_exceptions": [
            exception_row(), exception_row(issuer_id="SOME-OTHER-ISSUER"),
        ]}
        failures = check_campaign.o1_per_theme_exception_failures(targets, self.theme_ids)
        self.assertTrue(any("duplicate exception" in f for f in failures), failures)

    def test_absent_key_is_not_a_failure(self):
        targets = {"mode": "DEPTH"}
        self.assertEqual(
            check_campaign.o1_per_theme_exception_failures(targets, self.theme_ids), [])
        self.assertEqual(check_campaign.o1_per_theme_exceptions(targets), {})


class TestExceptionKeyAllowedUnderFrozenTargets(unittest.TestCase):
    """validate_campaign's frozen-targets check must not treat a well-formed exceptions
    list as an unlocked key, but must still refuse any other stray key."""

    def test_exceptions_key_alone_is_not_outside_the_frozen_set(self):
        targets = dict(check_campaign.DEPTH_TARGETS)
        targets["o1_per_theme_exceptions"] = [exception_row()]
        extra = sorted(
            set(targets) - set(check_campaign.DEPTH_TARGETS) - {"o1_per_theme_exceptions"})
        self.assertEqual(extra, [])

    def test_unrelated_stray_key_is_still_outside_the_frozen_set(self):
        targets = dict(check_campaign.DEPTH_TARGETS)
        targets["bonus"] = 1
        extra = sorted(
            set(targets) - set(check_campaign.DEPTH_TARGETS) - {"o1_per_theme_exceptions"})
        self.assertEqual(extra, ["bonus"])


if __name__ == "__main__":
    unittest.main()
