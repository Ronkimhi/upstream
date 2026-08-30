#!/usr/bin/env python3
"""Adversarial tests for Stocky's campaign-era O1 admission boundary."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_analyst  # noqa: E402
import check_map  # noqa: E402
import check_profile  # noqa: E402


def source():
    return {
        "claim": "Issuer performs the mapped role",
        "tag": "VERIFIED",
        "source_name": "Issuer annual report",
        "source_date": "2026-08-30",
        "url": "https://example.com/annual-report",
    }


def listing_evidence(name, exchange, ticker):
    return {
        "legal_issuer": name,
        "exchange": exchange,
        "ticker": ticker,
        "source_excerpt": (
            f"{name} is listed on {exchange} under the symbol {ticker}."
        ),
        "source_name": "Official Exchange issuer directory",
        "source_date": "2026-08-30",
        "url": f"https://exchange.example/issuer/{exchange}/{ticker}",
        "tag": "VERIFIED",
        "source_type": "OFFICIAL_EXCHANGE",
    }


class StockyAdmissionTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for folder in ("companies", "mappings", "screens", "stocks"):
            (self.root / "data" / folder).mkdir(parents=True, exist_ok=True)
        self.stock_path = self.root / "data" / "stocks" / "AAA__theme-a.json"
        self.mapping = {
            "id": "MAP-theme-a",
            "chain_id": "theme-a",
            "target_issuers_per_link": 10,
            "status": "COMPLETE",
            "issuers": [
                {"issuer_id": "ISS-A", "name": "Issuer A"},
                {"issuer_id": "ISS-B", "name": "Issuer B"},
            ],
            "listings": [
                {
                    "listing_id": "XNYS:AAA",
                    "issuer_id": "ISS-A",
                    "ticker": "AAA",
                    "exchange": "XNYS",
                    "identity_evidence": [
                        listing_evidence("Issuer A", "XNYS", "AAA")
                    ],
                },
                {
                    "listing_id": "XTKS:AAA",
                    "issuer_id": "ISS-A",
                    "ticker": "AAA",
                    "exchange": "XTKS",
                    "identity_evidence": [
                        listing_evidence("Issuer A", "XTKS", "AAA")
                    ],
                },
                {
                    "listing_id": "XNAS:AAA",
                    "issuer_id": "ISS-B",
                    "ticker": "AAA",
                    "exchange": "XNAS",
                    "identity_evidence": [
                        listing_evidence("Issuer B", "XNAS", "AAA")
                    ],
                },
            ],
            "placements": [
                {
                    "chain_id": "theme-a",
                    "link_id": "L1",
                    "issuer_id": "ISS-A",
                    "role": "Qualified supplier",
                    "status": "ACTIVE",
                    "evidence": [source()],
                },
                {
                    "chain_id": "theme-a",
                    "link_id": "L2",
                    "issuer_id": "ISS-A",
                    "role": "Secondary role",
                    "status": "ACTIVE",
                    "evidence": [source()],
                },
                {
                    "chain_id": "theme-a",
                    "link_id": "L1",
                    "issuer_id": "ISS-B",
                    "role": "Different issuer with the same ticker text",
                    "status": "ACTIVE",
                    "evidence": [source()],
                },
            ],
            "link_coverage": [
                {
                    "link_id": "L1",
                    "status": "TARGET_MET",
                    "distinct_issuer_count": 2,
                },
                {
                    "link_id": "L2",
                    "status": "OPEN",
                    "distinct_issuer_count": 1,
                },
            ],
            "changelog": [{
                "ts": "2026-08-30T01:00:00Z",
                "by": "atlas-cartographer",
                "kind": "BUILD",
                "change": "Built the material issuer mapping and exact link census.",
                "prior": None,
            }],
        }
        self.attach_audit(self.mapping)
        self.screen = {
            "id": "theme-a",
            "chain_id": "theme-a",
            "buckets": {
                "pure_play": [
                    {
                        "ticker": "AAA",
                        "issuer_id": "ISS-A",
                        "listing_id": "XTKS:AAA",
                        "link_id": "L1",
                    },
                    {
                        "ticker": "AAA",
                        "issuer_id": "ISS-B",
                        "listing_id": "XNAS:AAA",
                        "link_id": "L1",
                    },
                ]
            },
        }
        def metric(value):
            return {
                "value": value,
                "tag": "VERIFIED",
                "source_name": "Issuer annual report",
                "source_date": "2026-08-30",
                "url": "https://example.com/annual-report",
            }

        self.profile = {
            "issuer_id": "ISS-A",
            "issuer_name": "Issuer A",
            "as_of": "2026-08-30",
            "status": "COMPLETE",
            "data_tier": "T3",
            "opportunity_tier": "O1",
            "listing_refs": ["XNYS:AAA", "XTKS:AAA"],
            "placements": [
                {"chain_id": "theme-a", "link_id": "L1"},
                {"chain_id": "theme-a", "link_id": "L2"},
            ],
            "business_summary": "Specialist supplier",
            "exposure_summary": "Direct disclosed exposure",
            "metrics": {
                "revenue": {"latest_fy": metric(125.0)},
                "growth": {"revenue_cagr_3y": metric(0.2)},
                "margins": {"operating_margin": metric(0.15)},
                "cash_conversion": {"fcf_margin": metric(0.12)},
                "leverage": {"net_debt_to_ebitda": metric(1.1)},
                "quality": {
                    "piotroski": metric(7),
                    "beneish_state": metric(-2.4),
                },
                "valuation": {
                    "market_cap": metric(1_000.0),
                    "price_to_earnings": metric(20.0),
                },
                "reverse_dcf": {
                    "implied_fcf_cagr": metric(0.18),
                    "horizon_spread": metric(0.05),
                },
            },
            "crowdedness_caveats": ["Coverage differs by listing"],
            "catalysts": ["Capacity commissioning"],
            "risks": ["Customer concentration"],
            "data_gaps": ["No quarterly segment margin"],
            "disposition": {"state": "ADVANCE", "basis": "Qualified for Stocky"},
            "selection_basis": {
                dimension: {"basis": f"{dimension} reviewed"}
                for dimension in check_profile.SELECTION_DIMENSIONS
            } | {
                "screen_handoff": {
                    "screen_ref": "theme-a",
                    "chain_id": "theme-a",
                    "link_id": "L1",
                    "listing_id": "XTKS:AAA",
                }
            },
            "confidence_audit": {"verified": 11},
            "changelog": [],
        }
        self.stock = {
            "ticker": "AAA",
            "issuer_id": "ISS-A",
            "listing_id": "XTKS:AAA",
            "chain_id": "theme-a",
            "link_id": "L1",
            "screen_ref": "theme-a",
            "status": "DRAFT",
            "created_at": "2026-08-30",
            "updated_at": "2026-08-30",
            "as_of": "2026-08-30",
        }
        self._write_upstream()

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def attach_audit(mapping, *, audited_at="2026-08-30T02:00:00Z", status="PASS"):
        checks = []
        target_seen = {}
        placed_issuers = {
            placement["issuer_id"]
            for placement in mapping.get("placements") or []
        }
        for listing in mapping.get("listings") or []:
            if listing.get("issuer_id") not in placed_issuers:
                continue
            evidence = listing["identity_evidence"][0]
            checks.append({
                "chain_id": mapping["chain_id"],
                "listing_id": listing["listing_id"],
                "issuer_id": listing["issuer_id"],
                "identity_ok": True,
                "role_ok": True,
                "source_date_ok": True,
                "source_url": evidence["url"],
                "source_date": evidence["source_date"],
                "source_type": evidence["source_type"],
                "source_excerpt": evidence["source_excerpt"],
                "record_digest": check_map.listing_identity_digest(listing),
            })
        for placement in mapping.get("placements") or []:
            link_id = placement["link_id"]
            coverage = next(
                row for row in mapping.get("link_coverage") or []
                if row["link_id"] == link_id
            )
            if coverage.get("status") == "TARGET_MET":
                seen = target_seen.setdefault(link_id, 0)
                if seen >= 2:
                    continue
                target_seen[link_id] = seen + 1
            evidence = placement["evidence"][0]
            checks.append({
                "chain_id": placement["chain_id"],
                "link_id": placement["link_id"],
                "issuer_id": placement["issuer_id"],
                "identity_ok": True,
                "role_ok": True,
                "source_date_ok": True,
                "source_url": evidence["url"],
                "source_date": evidence["source_date"],
                "source_excerpt": evidence["claim"],
                "record_digest": check_map.placement_claim_digest(
                    placement, evidence
                ),
            })
        target_links = {
            row["link_id"]
            for row in mapping.get("link_coverage") or []
            if row.get("status") == "TARGET_MET"
        }
        exhausted_links = {
            row["link_id"]
            for row in mapping.get("link_coverage") or []
            if row.get("status") == "EXHAUSTED"
        }
        listing_checks = sum(1 for row in checks if row.get("listing_id"))
        placement_checks = sum(1 for row in checks if row.get("issuer_id") and not row.get("listing_id"))
        mapping["audit"] = {
            "audited_at": audited_at,
            "reviewed_by": check_map.AUDIT_REVIEWER,
            "agent_id": check_map.AUDIT_REVIEWER,
            "transcript_ref": "audit-transcript-20260830-stocky",
            "review_mode": "FRESH_CONTEXT",
            "independence_limitation": (
                "Repository declarations cannot prove fresh-context independence."
            ),
            "status": status,
            "mapping_fingerprint": check_map.mapping_fingerprint(mapping),
            "links_examined": len(mapping.get("link_coverage") or []),
            "placements_examined": placement_checks,
            "listings_examined": listing_checks,
            "searches_examined": 0,
            "target_met_links_examined": len(target_links),
            "exhausted_links_examined": len(exhausted_links),
            "sampled_checks": checks,
            "identity_conflicts": [],
            "role_conflicts": [],
            "source_date_conflicts": [],
            "amendments_required": (
                ["Resolve issuer identity before re-audit"] if status == "FAIL" else []
            ),
            "surviving_limitation": (
                "Private supplier relationships may remain undisclosed."
            ),
        }

    def _write_upstream(self):
        (self.root / "data" / "mappings" / "theme-a.json").write_text(
            json.dumps(self.mapping))
        (self.root / "data" / "screens" / "theme-a.json").write_text(
            json.dumps(self.screen))
        (self.root / "data" / "companies" / "ISS-A.json").write_text(
            json.dumps(self.profile))

    def findings(self, stock=None):
        return check_analyst.stock_admission_failures(
            self.root, stock or self.stock)

    def run_gate(self, stock, date="2026-08-31", path=None):
        path = path or self.stock_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stock) if isinstance(stock, dict) else stock)
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "check_analyst.py"),
                "--root",
                str(self.root),
                "--date",
                date,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )


class TestExactO1Admission(StockyAdmissionTree):
    def test_exact_o1_dual_listing_shared_ticker_passes(self):
        self.assertEqual(self.findings(), [])

    def test_wrong_listing_is_not_rescued_by_shared_ticker(self):
        stock = copy.deepcopy(self.stock)
        stock["listing_id"] = "XNAS:AAA"
        findings = self.findings(stock)
        self.assertTrue(any("does not exactly match O1 screen handoff" in f
                            for f in findings), findings)
        self.assertTrue(any("belongs to 'ISS-B'" in f for f in findings), findings)

    def test_wrong_dive_link_is_rejected(self):
        stock = copy.deepcopy(self.stock)
        stock["link_id"] = "L2"
        findings = self.findings(stock)
        self.assertTrue(any("dive link_id" in f for f in findings), findings)
        self.assertTrue(any("no exact row" in f for f in findings), findings)

    def test_wrong_screen_ref_is_rejected(self):
        stock = copy.deepcopy(self.stock)
        stock["screen_ref"] = "theme-a__S2"
        findings = self.findings(stock)
        self.assertTrue(any("dive screen_ref" in f for f in findings), findings)
        self.assertTrue(any("does not resolve in data/screens" in f
                            for f in findings), findings)

    def test_wrong_screen_link_is_rejected(self):
        self.screen["buckets"]["pure_play"][0]["link_id"] = "L2"
        self._write_upstream()
        findings = self.findings()
        self.assertTrue(any("no exact issuer_id/listing_id/link_id row" in f
                            for f in findings), findings)
        self.assertTrue(any("no exact row" in f for f in findings), findings)

    def test_screen_row_without_normalized_identity_is_rejected(self):
        row = self.screen["buckets"]["pure_play"][0]
        row.pop("issuer_id")
        row.pop("listing_id")
        self._write_upstream()
        findings = self.findings()
        self.assertTrue(any("no exact row" in f for f in findings), findings)

    def test_unqualified_mapping_placement_is_rejected(self):
        self.mapping["placements"][0]["evidence"] = []
        self._write_upstream()
        findings = self.findings()
        self.assertTrue(any("placement has no evidence" in f for f in findings), findings)

    def test_profile_without_structured_o1_selection_basis_is_rejected(self):
        self.profile["selection_basis"] = {
            "screen_handoff": self.profile["selection_basis"]["screen_handoff"]
        }
        self._write_upstream()
        findings = self.findings()
        self.assertTrue(any("selection_basis.direct_exposure" in f
                            for f in findings), findings)

    def test_o2_o3_blocked_and_draft_profiles_are_rejected(self):
        cases = (
            ("COMPLETE", "O2"),
            ("DRAFT", "O3"),
            ("BLOCKED", "O3"),
        )
        for status, tier in cases:
            with self.subTest(status=status, tier=tier):
                self.profile["status"] = status
                self.profile["opportunity_tier"] = tier
                self._write_upstream()
                findings = self.findings()
                self.assertTrue(
                    any("status must be COMPLETE" in f or
                        "requires opportunity_tier O1" in f
                        for f in findings),
                    findings,
                )


class TestMappingAuditAdmission(StockyAdmissionTree):
    def test_valid_complete_pass_audit_admits_exact_o1_handoff(self):
        self.assertEqual(self.findings(), [])

    def test_active_fail_audit_probe_rejects_draft_and_final(self):
        """Cass REV-20260830-01 reopen probe: honest ACTIVE + current FAIL audit."""
        mapping = copy.deepcopy(self.mapping)
        mapping["status"] = "ACTIVE"
        mapping["audit"]["status"] = "FAIL"
        mapping["audit"]["audited_at"] = "2026-08-30T03:00:00Z"
        mapping["audit"]["identity_conflicts"] = ["Issuer identity is ambiguous"]
        mapping["audit"]["amendments_required"] = [
            "Resolve the issuer identity before re-audit"
        ]
        mapping["audit"]["mapping_fingerprint"] = check_map.mapping_fingerprint(mapping)
        self.mapping = mapping
        self._write_upstream()
        findings = self.findings()
        self.assertTrue(
            any("Stocky admission requires mapping status COMPLETE" in f for f in findings),
            findings,
        )
        for status in ("DRAFT", "FINAL"):
            with self.subTest(status=status):
                stock = copy.deepcopy(self.stock)
                stock["status"] = status
                result = self.run_gate(stock)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(
                    "Stocky admission requires mapping status COMPLETE",
                    result.stdout,
                )

    def test_draft_mapping_rejected(self):
        self.mapping["status"] = "DRAFT"
        self._write_upstream()
        findings = self.findings()
        self.assertTrue(
            any("Stocky admission requires mapping status COMPLETE" in f for f in findings),
            findings,
        )

    def test_active_mapping_without_complete_rejected(self):
        self.mapping["status"] = "ACTIVE"
        self.attach_audit(self.mapping, status="FAIL")
        self._write_upstream()
        findings = self.findings()
        self.assertTrue(
            any("Stocky admission requires mapping status COMPLETE" in f for f in findings),
            findings,
        )

    def test_complete_missing_audit_rejected(self):
        self.mapping.pop("audit")
        self._write_upstream()
        findings = self.findings()
        self.assertTrue(
            any("needs a current PASS audit" in f for f in findings),
            findings,
        )

    def test_complete_fail_audit_rejected(self):
        self.mapping["audit"]["status"] = "FAIL"
        self.mapping["audit"]["identity_conflicts"] = ["Role evidence is ambiguous"]
        self.mapping["audit"]["amendments_required"] = ["Clarify the mapped role"]
        self._write_upstream()
        findings = self.findings()
        self.assertTrue(
            any("requires audit.status PASS" in f for f in findings),
            findings,
        )

    def test_stale_fingerprint_rejected(self):
        self.mapping["placements"][0]["role"] = "Material role changed after audit"
        self._write_upstream()
        findings = self.findings()
        self.assertTrue(
            any("fingerprint does not match current material content" in f
                for f in findings),
            findings,
        )

    def test_stale_audit_timestamp_rejected(self):
        self.mapping["changelog"].append({
            "ts": "2026-08-30T04:00:00Z",
            "by": "atlas-cartographer",
            "kind": "AMEND",
            "change": "Material mapping edit after the audit.",
            "prior": "Prior census.",
        })
        self._write_upstream()
        findings = self.findings()
        self.assertTrue(
            any("audit.audited_at is earlier than material mapping changelog" in f
                for f in findings),
            findings,
        )

    def test_audit_conflicts_rejected_on_pass(self):
        self.mapping["audit"]["identity_conflicts"] = [
            "Issuer legal name does not match the listing"
        ]
        self._write_upstream()
        findings = self.findings()
        self.assertTrue(
            any("PASS audit requires empty conflict" in f for f in findings),
            findings,
        )


class TestAdmissionMigrationBoundary(StockyAdmissionTree):
    @staticmethod
    def committed_vrt_content():
        return (ROOT / "data" / "stocks" / "VRT__ai-infrastructure.json").read_text()

    def test_forged_old_dates_on_new_file_do_not_bypass_campaign_admission(self):
        stock = {
            "ticker": "AAA",
            "chain_id": "theme-a",
            "link_id": "L1",
            "status": "FINAL",
            "created_at": "2026-08-29",
            "updated_at": "2026-08-29",
            "as_of": "2026-08-29",
        }
        result = self.run_gate(stock, date="2026-08-31")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("campaign-era dive requires non-empty issuer_id", result.stdout)

    def test_exact_committed_vrt_path_and_content_warns_but_remains_readable(self):
        result = self.run_gate(
            self.committed_vrt_content(),
            date="2026-08-30",
            path=self.root / "data" / "stocks" / "VRT__ai-infrastructure.json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("exact pre-2026-08-30 committed legacy baseline match", result.stdout)
        self.assertIn("not grandfathered silently", result.stdout)

    def test_copied_or_renamed_vrt_does_not_inherit_legacy_exemption(self):
        result = self.run_gate(
            self.committed_vrt_content(),
            path=self.root / "data" / "stocks" / "VRT__copied.json",
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("campaign-era dive requires non-empty issuer_id", result.stdout)
        self.assertNotIn("committed legacy baseline match", result.stdout)

    def test_changed_ticker_or_chain_on_vrt_path_does_not_inherit_exemption(self):
        for field, value in (("ticker", "AAA"), ("chain_id", "other-theme")):
            with self.subTest(field=field):
                stock = json.loads(self.committed_vrt_content())
                stock[field] = value
                result = self.run_gate(
                    stock,
                    path=self.root / "data" / "stocks" / "VRT__ai-infrastructure.json",
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("campaign-era dive requires non-empty issuer_id", result.stdout)
                self.assertNotIn("committed legacy baseline match", result.stdout)

    def test_campaign_era_draft_and_final_fail_even_when_not_touched_today(self):
        for status in ("DRAFT", "FINAL"):
            with self.subTest(status=status):
                unattributed = {
                    "ticker": "AAA",
                    "chain_id": "theme-a",
                    "link_id": "L1",
                    "status": status,
                    "created_at": "2026-08-30",
                    "updated_at": "2026-08-30",
                    "as_of": "2026-08-30",
                }
                result = self.run_gate(unattributed, date="2026-08-31")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("campaign-era dive requires non-empty issuer_id", result.stdout)
                self.assertIn("campaign-era dive requires non-empty listing_id", result.stdout)
                self.assertIn("campaign-era dive requires non-empty screen_ref", result.stdout)


if __name__ == "__main__":
    unittest.main()
