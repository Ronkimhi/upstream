#!/usr/bin/env python3
"""Adversarial tests for Sieve's campaign-era screen identity boundary."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_map  # noqa: E402


def evidence():
    return [{
        "claim": "Issuer is a qualified supplier",
        "tag": "VERIFIED",
        "source_name": "Issuer annual report",
        "source_date": "2026-08-30",
        "url": "https://example.com/annual-report",
    }]


class CampaignScreenTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for folder in ("companies", "mappings", "screens", "chains"):
            (self.root / "data" / folder).mkdir(parents=True, exist_ok=True)
        self.mapping = {
            "id": "MAP-theme-a",
            "chain_id": "theme-a",
            "target_issuers_per_link": 10,
            "status": "COMPLETE",
            "issuers": [{"issuer_id": "ISS-A", "name": "Issuer A"}],
            "listings": [{
                "listing_id": "XNYS:AAA",
                "issuer_id": "ISS-A",
                "ticker": "AAA",
                "exchange": "XNYS",
            }],
            "placements": [{
                "chain_id": "theme-a",
                "link_id": "L1",
                "issuer_id": "ISS-A",
                "role": "Qualified supplier",
                "status": "ACTIVE",
                "evidence": evidence(),
            }],
            "link_coverage": [],
        }
        self.mapping["audit"] = {
            "status": "PASS",
            "mapping_fingerprint": check_map.mapping_fingerprint(self.mapping),
        }
        self.profile = {
            "issuer_id": "ISS-A",
            "status": "COMPLETE",
            "opportunity_tier": "O2",
            "data_tier": "T1",
        }
        self.row = {
            "issuer_id": "ISS-A",
            "listing_id": "XNYS:AAA",
            "ticker": "AAA",
            "market_ticker": "AAA",
            "chain_id": "theme-a",
            "link_id": "L1",
            "mapping_ref": "data/mappings/theme-a.json",
            "profile_ref": "data/companies/ISS-A.json",
            "data_tier": "T1",
        }
        self.screen = {
            "id": "theme-a",
            "chain_id": "theme-a",
            "identity_schema": "campaign-v1",
            "scenario_id": None,
            "as_of": "2026-08-01",
            "buckets": {"pure_play": [self.row]},
        }
        self.write()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self):
        (self.root / "data" / "mappings" / "theme-a.json").write_text(
            json.dumps(self.mapping))
        (self.root / "data" / "companies" / "ISS-A.json").write_text(
            json.dumps(self.profile))
        (self.root / "data" / "screens" / "theme-a.json").write_text(
            json.dumps(self.screen))

    def gate(self):
        return subprocess.run(
            [sys.executable, str(ROOT / "tools" / "check_screen.py"),
             "--root", str(self.root), "--date", "2026-08-31"],
            cwd=ROOT, capture_output=True, text=True, check=False)

    def assert_fails(self, expected):
        result = self.gate()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(expected, result.stdout)

    def test_exact_valid_o2_handoff_passes(self):
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unmapped_ticker_is_rejected(self):
        self.row["listing_id"] = "XNYS:ZZZ"
        self.row["market_ticker"] = "ZZZ"
        self.write()
        self.assert_fails("does not resolve to issuer_id")

    def test_wrong_link_is_rejected(self):
        self.row["link_id"] = "L2"
        self.write()
        self.assert_fails("no current qualified mapping placement")

    def test_o3_or_blocked_profile_is_rejected(self):
        self.profile.update({"status": "BLOCKED", "opportunity_tier": "O3"})
        self.write()
        self.assert_fails("requires a COMPLETE O1 or O2 profile")

    def test_o3_complete_profile_is_still_rejected(self):
        """O3 is refused on the tier alone, not only because DRAFT/BLOCKED came with it."""
        self.profile.update({"status": "COMPLETE", "opportunity_tier": "O3"})
        self.write()
        self.assert_fails("requires a COMPLETE O1 or O2 profile")

    def test_draft_o2_profile_is_rejected(self):
        self.profile.update({"status": "DRAFT", "opportunity_tier": "O2"})
        self.write()
        self.assert_fails("requires a COMPLETE O1 or O2 profile")

    def test_selection_promoting_a_row_to_o1_does_not_break_the_screen(self):
        """The funnel deadlock, as a test.

        `run selection` promotes a screened COMPLETE O2 profile to O1, and this gate re-reads
        every screen on disk on every invocation. While it demanded O2 exactly, the first
        selection permanently failed every strict screen behind it, so screening and selecting
        could never both be true. O1 is a strictly later state of the same profile.
        """
        self.profile["opportunity_tier"] = "O1"
        self.write()
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_stale_or_failed_mapping_audit_is_rejected(self):
        """Without placement scope declared on the row, the census audit still binds."""
        with self.subTest("failed"):
            self.mapping["audit"]["status"] = "FAIL"
            self.write()
            self.assert_fails("lacks a current PASS audit")
        with self.subTest("stale"):
            self.mapping["audit"]["status"] = "PASS"
            self.mapping["placements"][0]["role"] = "Amended role"
            self.write()
            self.assert_fails("lacks a current PASS audit")

    def test_placement_scope_admits_a_verified_placement_on_an_active_mapping(self):
        """Ron, 2026-09-01: a screen row consumes ONE placement, so an ACTIVE mapping with
        no census audit admits a row that says so and rests on all-VERIFIED evidence."""
        self.mapping["status"] = "ACTIVE"
        self.mapping.pop("audit")
        self.row["audit_scope"] = "PLACEMENT"
        self.write()
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 row(s) admitted on placement scope", result.stdout)

    def test_placement_scope_refuses_inferred_evidence(self):
        self.mapping["status"] = "ACTIVE"
        self.mapping.pop("audit")
        self.mapping["placements"][0]["evidence"][0]["tag"] = "INFERRED"
        self.row["audit_scope"] = "PLACEMENT"
        self.write()
        self.assert_fails("requires every evidence item on the placement to be VERIFIED")

    def test_placement_scope_still_needs_a_qualified_placement(self):
        self.mapping["status"] = "ACTIVE"
        self.mapping.pop("audit")
        self.mapping["placements"][0]["status"] = "PENDING"
        self.row["audit_scope"] = "PLACEMENT"
        self.write()
        self.assert_fails("placement.status must be exactly ACTIVE")

    def test_duplicate_issuer_needs_secondary_link_basis(self):
        self.screen["buckets"]["second_order"] = [copy.deepcopy(self.row)]
        self.write()
        self.assert_fails("duplicate issuer row needs a non-empty secondary_link_basis")

    def test_unmarked_new_screen_over_mapping_is_rejected(self):
        self.screen.pop("identity_schema")
        self.screen["as_of"] = "2026-08-31"
        self.write()
        self.assert_fails("lacks identity_schema 'campaign-v1'")

    def test_rejected_placement_is_not_currently_qualified(self):
        self.mapping["placements"][0]["status"] = "REJECTED"
        self.mapping["audit"]["mapping_fingerprint"] = check_map.mapping_fingerprint(
            self.mapping)
        self.write()
        self.assert_fails("placement.status must be exactly ACTIVE")

    def test_pending_missing_or_unknown_placement_is_rejected(self):
        for status in ("PENDING", None, "UNKNOWN", "INACTIVE"):
            with self.subTest(status=status):
                self.mapping["placements"][0]["status"] = status
                self.mapping["audit"]["mapping_fingerprint"] = check_map.mapping_fingerprint(
                    self.mapping)
                self.write()
                self.assert_fails("placement.status must be exactly ACTIVE")

    def test_profile_data_tier_must_match_screen_row(self):
        self.profile["data_tier"] = "T3"
        self.write()
        self.assert_fails("does not match profile data_tier")

    def test_legacy_screen_warns_but_remains_readable(self):
        self.screen["buckets"]["pure_play"] = [{"ticker": "LEGACY"}]
        self.screen.pop("identity_schema")
        (self.root / "data" / "screens" / "theme-a.json").unlink()
        (self.root / "data" / "screens" / "ai-infrastructure.json").write_text(
            json.dumps(self.screen))
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pre-campaign legacy screen", result.stdout)


if __name__ == "__main__":
    unittest.main()
