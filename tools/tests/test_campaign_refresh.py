#!/usr/bin/env python3
"""A dive refresh may land on its own on a COMPLETE theme (Ron, 2026-11-19).

`refresh data/stocks/<T>__<chain>.json` sets a FINAL dive back to DRAFT until a fresh-context
red team re-signs it. On a COMPLETE theme that made the theme's evidence-backed stage DIVED,
and the campaign gate then refused both states: leaving COMPLETE was "ahead of evidence",
lowering it was "stage regressed". Hit five times on 2026-11-19 (STE, WST, HALO, HUBB, NVT,
AON), each landed only by fusing the refresh with its red team in one commit.

These tests run against a real git repository, because the rule is relative to git HEAD and
a mocked HEAD would not prove the far end.
"""
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import campaign_calibrate  # noqa: E402
import check_campaign  # noqa: E402
import test_campaign  # noqa: E402

CAMPAIGN = "CAMP-20261119-01.json"
DIVES = {"a": ("ISS-A", "XNYS:AAA"), "b": ("ISS-B", "XNAS:AAA")}


class RefreshTree(test_campaign.CampaignTree):
    def setUp(self):
        super().setUp()
        base = test_campaign.TestExactFinalAttribution._two_issuer_theme(self)
        for suffix, (issuer_id, listing_id) in DIVES.items():
            self.write_dive(suffix, {
                "ticker": "AAA",
                "issuer_id": issuer_id,
                "listing_id": listing_id,
                "chain_id": "theme-a",
                "link_id": "L1",
                "status": "FINAL",
                "changelog": [{"ts": "2026-09-06T18:00:00Z", "by": "stocky",
                               "kind": "BUILD", "change": "first dive"}],
            })
        self.manifest_path = self.root / "data" / "campaigns" / CAMPAIGN
        campaign = copy.deepcopy(base)
        campaign["status"] = "SELECTED"
        campaign["themes"][0]["stage"] = "COMPLETE"
        campaign["completion"] = check_campaign.compute_campaign_completion(
            self.root, campaign)
        self.assertEqual(campaign["completion"]["per_theme"][0]["stage_computed"],
                         "COMPLETE")
        self.write_manifest(campaign)
        self.git("init", "-q")
        self.commit("baseline: theme COMPLETE, both O1 dives FINAL")

    # -- helpers ---------------------------------------------------------------------
    def git(self, *args):
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=test",
             "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false", *args],
            check=True, capture_output=True, text=True)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", message)

    def dive_path(self, suffix):
        return self.root / "data" / "stocks" / f"AAA-{suffix}__theme-a.json"

    def write_dive(self, suffix, dive):
        self.dive_path(suffix).write_text(json.dumps(dive))

    def read_dive(self, suffix):
        return json.loads(self.dive_path(suffix).read_text())

    def manifest(self):
        return json.loads(self.manifest_path.read_text())

    def write_manifest(self, campaign):
        self.manifest_path.write_text(json.dumps(campaign))

    def refresh(self, suffix="a", *, kind="AMEND", status="DRAFT"):
        dive = self.read_dive(suffix)
        dive["status"] = status
        if kind is not None:
            dive["changelog"].append({"ts": "2026-11-19T23:30:00Z", "by": "stocky",
                                      "kind": kind, "change": "refresh on Ron's command"})
        self.write_dive(suffix, dive)

    def hand_lowered(self, stage="DIVED"):
        """The manifest with its stage typed lower and its counts honestly recomputed."""
        campaign = self.manifest()
        campaign["themes"][0]["stage"] = stage
        campaign["completion"] = check_campaign.compute_campaign_completion(
            self.root, campaign)
        return campaign

    def regressions(self, campaign):
        return [finding for finding in check_campaign.preservation_failures(
            self.root, self.manifest_path, campaign) if "regressed" in finding]


class TestRefreshReopensACompleteTheme(RefreshTree):
    def test_refresh_lands_alone_and_red_team_re_advances(self):
        self.refresh("a")
        amended = campaign_calibrate.calibrate_campaign(self.root, self.manifest())
        self.assertEqual(amended["themes"][0]["stage"], "DIVED")
        self.assertEqual(amended.pop("_stage_advances"), [])
        reopens = amended.pop("_stage_reopens")
        self.assertTrue(any("AAA-a__theme-a.json" in row for row in reopens), reopens)
        # The counts say what is true while the dive is DRAFT.
        self.assertEqual(amended["completion"]["o1_final"], 1)
        self.assertEqual(amended["completion"]["themes_complete"], 0)
        self.assertEqual(amended["completion"]["per_theme"][0]["stage_computed"], "DIVED")
        self.assertEqual(check_campaign.preservation_failures(
            self.root, self.manifest_path, amended), [])

        # The refresh commits on its own.
        self.write_manifest(amended)
        self.commit("[refresh] AAA-a DRAFT, theme DIVED")
        self.assertEqual(check_campaign.preservation_failures(
            self.root, self.manifest_path, self.manifest()), [])

        # The red team re-signs; the next calibration advances the theme back to COMPLETE.
        self.refresh("a", kind="RED_TEAM", status="FINAL")
        signed = campaign_calibrate.calibrate_campaign(self.root, self.manifest())
        self.assertEqual(signed["themes"][0]["stage"], "COMPLETE")
        self.assertEqual(signed.pop("_stage_advances"), ["T1:DIVED->COMPLETE"])
        self.assertEqual(signed.pop("_stage_reopens"), [])
        self.assertEqual(signed["completion"]["o1_final"], 2)
        self.assertEqual(check_campaign.preservation_failures(
            self.root, self.manifest_path, signed), [])

    def test_action_field_amend_counts_as_the_refresh_entry(self):
        dive = self.read_dive("a")
        dive["status"] = "DRAFT"
        dive["changelog"].append({"ts": "2026-11-19T23:30:00Z", "by": "stocky",
                                  "action": "AMEND", "change": "refresh"})
        self.write_dive("a", dive)
        self.assertEqual(self.regressions(self.hand_lowered()), [])

    def test_complete_campaign_drops_to_active_only_with_the_refresh(self):
        campaign = self.manifest()
        campaign["status"] = "COMPLETE"
        self.write_manifest(campaign)
        self.commit("campaign COMPLETE")

        lowered = copy.deepcopy(campaign)
        lowered["status"] = "ACTIVE"
        failures = check_campaign.preservation_failures(self.root, self.manifest_path, lowered)
        self.assertTrue(any("campaign status regressed COMPLETE -> ACTIVE" in f
                            for f in failures), failures)

        self.refresh("a")
        amended = campaign_calibrate.calibrate_campaign(self.root, self.manifest())
        amended.pop("_stage_advances")
        amended.pop("_stage_reopens")
        self.assertEqual(amended["status"], "ACTIVE")
        self.assertEqual(check_campaign.preservation_failures(
            self.root, self.manifest_path, amended), [])


class TestUnexplainedRegressionStillRefused(RefreshTree):
    def assert_refused(self):
        amended = campaign_calibrate.calibrate_campaign(self.root, self.manifest())
        self.assertEqual(amended["themes"][0]["stage"], "COMPLETE",
                         "the calibrator must not lower a stage without a refresh behind it")
        self.assertEqual(amended["_stage_reopens"], [])
        failures = self.regressions(self.hand_lowered())
        self.assertTrue(any("stage regressed COMPLETE -> DIVED" in f for f in failures),
                        failures)

    def test_no_final_to_draft_dive_behind_the_regression(self):
        # A FINAL dive deleted outright: the evidence says DIVED, nothing says refresh.
        self.dive_path("b").unlink()
        self.assertEqual(check_campaign.compute_campaign_completion(
            self.root, self.manifest())["per_theme"][0]["stage_computed"], "DIVED")
        self.assert_refused()

    def test_dive_already_draft_at_head_is_not_a_refresh(self):
        dive = self.read_dive("a")
        dive["status"] = "DRAFT"
        self.write_dive("a", dive)
        self.commit("dive committed DRAFT under a COMPLETE manifest")
        self.refresh("a")
        self.assert_refused()

    def test_final_to_draft_without_a_new_amend_entry(self):
        self.refresh("a", kind=None)
        self.assert_refused()

    def test_final_to_draft_with_a_non_amend_entry(self):
        self.refresh("a", kind="NOTE")
        self.assert_refused()

    def test_hand_lowered_stage_on_an_untouched_theme(self):
        failures = self.regressions(self.hand_lowered())
        self.assertTrue(any("stage regressed COMPLETE -> DIVED" in f for f in failures),
                        failures)

    def test_refresh_does_not_license_a_deeper_regression(self):
        self.refresh("a")
        failures = self.regressions(self.hand_lowered("SCREENED"))
        self.assertTrue(any("stage regressed COMPLETE -> SCREENED" in f for f in failures),
                        failures)


if __name__ == "__main__":
    unittest.main()
