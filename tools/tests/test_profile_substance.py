#!/usr/bin/env python3
"""Isolated pressure tests for substantive issuer-profile metrics."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_profile  # noqa: E402


def inferred(value, basis, **extra):
    return {
        "value": value,
        "tag": "INFERRED",
        "official_source": True,
        "basis": basis,
        "source_name": "Issuer annual report",
        "source_date": "2026-08-01",
        "url": "https://example.com/annual-report",
        **extra,
    }


def missing(field, label=None):
    return {
        "value": None,
        "tag": "NULL",
        "basis": f"{label or field.replace('_', ' ')} is not disclosed",
    }


def substantive_metrics():
    return {
        "revenue": {
            "latest_fy": inferred(2025, "Official fiscal-year revenue table"),
        },
        "growth": {
            "revenue_cagr_3y": inferred(
                0.12, "Derived from three official annual revenue totals"),
        },
        "margins": {
            "operating_margin": inferred(
                0.18, "Operating profit divided by official revenue"),
        },
        "cash_conversion": {
            "fcf_margin": inferred(
                0.11, "Official cash flow less capex divided by revenue"),
        },
        "leverage": {
            "net_debt_to_ebitda": inferred(
                1.4, "Official net debt divided by reported EBITDA"),
        },
        "quality": {
            "piotroski": inferred(
                7, "Nine Piotroski criteria derived from official statements"),
            "beneish_state": inferred(
                -2.1, "Beneish score derived from official statements"),
        },
        "valuation": {
            "market_cap": inferred(
                1_250_000_000, "Official shares outstanding times quoted price"),
            "pe_ratio": inferred(
                14.2, "Market capitalization divided by official net income"),
        },
        "reverse_dcf": {
            "implied_fcf_cagr": inferred(
                0.09, "Reverse DCF using the disclosed assumption set"),
            "horizon_spread": inferred(
                0.03, "Difference between the five- and ten-year solves"),
        },
    }


def null_metrics():
    return {
        "revenue": {
            "latest_fy": missing("latest_fy", "Latest fiscal year"),
        },
        "growth": {
            "revenue_cagr_3y": missing("revenue_cagr_3y", "Revenue CAGR 3y"),
        },
        "margins": {
            "operating_margin": missing("operating_margin"),
        },
        "cash_conversion": {
            "operating_cash_flow_to_net_income": missing(
                "operating_cash_flow_to_net_income"),
        },
        "leverage": {
            "net_debt_to_ebitda": missing("net_debt_to_ebitda"),
        },
        "quality": {
            "piotroski": missing("piotroski"),
            "beneish_state": missing("beneish_state"),
        },
        "valuation": {
            "market_cap": missing("market_cap"),
            "fcf_yield": missing("fcf_yield"),
        },
        "reverse_dcf": {
            "implied_fcf_cagr": missing("implied_fcf_cagr"),
            "horizon_spread": missing("horizon_spread"),
        },
    }


class ProfileTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for folder in ("companies", "mappings", "screens"):
            (self.root / "data" / folder).mkdir(parents=True, exist_ok=True)
        self.path = self.root / "data" / "companies" / "ISS-A.json"
        mapping = {
            "chain_id": "theme-a",
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
            }],
        }
        (self.root / "data" / "mappings" / "theme-a.json").write_text(
            json.dumps(mapping))
        screen = {
            "id": "theme-a",
            "chain_id": "theme-a",
            "buckets": {"pure_play": [{
                "ticker": "AAA",
                "issuer_id": "ISS-A",
                "listing_id": "XNYS:AAA",
                "link_id": "L1",
            }]},
        }
        (self.root / "data" / "screens" / "theme-a.json").write_text(
            json.dumps(screen))

    def tearDown(self):
        self.tmp.cleanup()

    def profile(self, metrics=None):
        return {
            "issuer_id": "ISS-A",
            "issuer_name": "Issuer A",
            "as_of": "2026-08-01",
            "status": "COMPLETE",
            "data_tier": "T3",
            "opportunity_tier": "O2",
            "listing_refs": ["XNYS:AAA"],
            "placements": [{"chain_id": "theme-a", "link_id": "L1"}],
            "business_summary": "Specialist supplier",
            "exposure_summary": "Direct disclosed exposure",
            "metrics": copy.deepcopy(metrics or substantive_metrics()),
            "crowdedness_caveats": ["Local listing has thin retail coverage"],
            "catalysts": ["Capacity commissioning"],
            "risks": ["Customer concentration"],
            "data_gaps": ["Quarterly segment detail is unavailable"],
            "disposition": {"state": "RETAIN", "basis": "Profile is complete"},
            "confidence_audit": {"inferred": 10},
            "changelog": [],
        }

    @staticmethod
    def selection_basis():
        basis = {
            dimension: {"basis": f"{dimension} was reviewed"}
            for dimension in check_profile.SELECTION_DIMENSIONS
        }
        basis["screen_handoff"] = {
            "screen_ref": "theme-a",
            "chain_id": "theme-a",
            "link_id": "L1",
            "listing_id": "XNYS:AAA",
        }
        return basis

    def validate(self, profile):
        return check_profile.validate_profile(self.root, self.path, profile)


class TestCanonicalMetricSchema(ProfileTree):
    def test_generic_placeholder_copied_into_every_group_fails(self):
        placeholder = {
            "placeholder": {
                "value": None,
                "tag": "NULL",
                "basis": "not collected",
            },
        }
        profile = self.profile({
            group: copy.deepcopy(placeholder)
            for group in check_profile.METRIC_GROUPS
        })
        failures = self.validate(profile)
        for group in check_profile.METRIC_GROUPS:
            self.assertTrue(
                any(f"metrics.{group}: non-canonical" in item
                    for item in failures),
                f"{group} accepted a generic placeholder: {failures}",
            )

    def test_missing_canonical_keys_fail_closed(self):
        probes = {
            "revenue": ("latest_fy", "latest_fy"),
            "growth": ("revenue_cagr_3y", "revenue_cagr_3y"),
            "margins": ("operating_margin", "operating_margin"),
            "cash_conversion": ("fcf_margin", "operating_cash_flow_to_net_income"),
            "leverage": ("net_debt_to_ebitda", "net_debt_to_ebitda"),
            "quality": ("beneish_state", "piotroski and beneish_state"),
            "valuation": ("pe_ratio", "ratio or yield"),
            "reverse_dcf": ("horizon_spread", "horizon_spread"),
        }
        for group, (field, expected) in probes.items():
            with self.subTest(group=group, field=field):
                profile = self.profile()
                profile["metrics"][group].pop(field)
                failures = self.validate(profile)
                self.assertTrue(
                    any(f"metrics.{group}" in item and expected in item
                        for item in failures),
                    failures,
                )

        profile = self.profile()
        profile["metrics"]["valuation"].pop("market_cap")
        self.assertTrue(
            any("needs canonical key market_cap" in item
                for item in self.validate(profile)))

    def test_field_specific_nulls_pass_o2_but_not_generic_or_o1(self):
        profile = self.profile(null_metrics())
        self.assertEqual(self.validate(profile), [])

        generic = copy.deepcopy(profile)
        generic["metrics"]["growth"]["revenue_cagr_3y"]["basis"] = "not collected"
        failures = self.validate(generic)
        self.assertTrue(
            any("NULL basis must name the missing revenue_cagr_3y field" in item
                for item in failures),
            failures,
        )

        copied = copy.deepcopy(profile)
        copied_basis = (
            "Latest fiscal year, revenue CAGR, operating margin, FCF margin, "
            "net debt EBITDA, Piotroski, Beneish, market cap, FCF yield, "
            "implied FCF CAGR, and horizon spread are unavailable"
        )
        for group in copied["metrics"].values():
            for metric in group.values():
                metric["basis"] = copied_basis
        failures = self.validate(copied)
        self.assertTrue(
            any("NULL basis is reused across canonical fields" in item
                for item in failures),
            failures,
        )

        profile["opportunity_tier"] = "O1"
        profile["selection_basis"] = self.selection_basis()
        failures = self.validate(profile)
        self.assertTrue(
            any("O1 cannot retain a NULL critical canonical field" in item
                for item in failures),
            failures,
        )

    def test_substantive_t3_official_source_profile_passes(self):
        profile = self.profile()
        self.assertEqual(self.validate(profile), [])

        for group in profile["metrics"].values():
            for metric in group.values():
                self.assertEqual(metric["tag"], "INFERRED")
                self.assertIs(metric["official_source"], True)

    def test_t3_official_quality_equivalent_is_explicit_and_bounded(self):
        profile = self.profile()
        profile["metrics"]["quality"] = {
            "official_source_equivalent": inferred(
                0.82,
                "Quality equivalent replaces unavailable Piotroski and Beneish models",
                equivalent_for=["piotroski", "beneish_state"],
            ),
        }
        self.assertEqual(self.validate(profile), [])

        profile["data_tier"] = "T1"
        failures = self.validate(profile)
        self.assertTrue(
            any("official-source equivalent is limited to T2/T3" in item
                for item in failures),
            failures,
        )

    def test_o1_keeps_structured_selection_and_exact_handoff(self):
        profile = self.profile()
        profile["opportunity_tier"] = "O1"
        profile["selection_basis"] = self.selection_basis()
        self.assertEqual(self.validate(profile), [])

        incomplete_basis = copy.deepcopy(profile)
        incomplete_basis["selection_basis"].pop("capture")
        failures = self.validate(incomplete_basis)
        self.assertTrue(
            any("selection_basis.capture must be a non-empty object" in item
                for item in failures),
            failures,
        )

        profile["metrics"]["growth"]["revenue_cagr_3y"]["state"] = "UNKNOWN"
        failures = self.validate(profile)
        self.assertTrue(any("O1 cannot retain unknown state" in item
                            for item in failures), failures)

        profile["metrics"]["growth"]["revenue_cagr_3y"].pop("state")
        profile["selection_basis"]["screen_handoff"]["listing_id"] = "XNYS:OTHER"
        failures = self.validate(profile)
        self.assertTrue(
            any("listing identity does not resolve" in item for item in failures),
            failures,
        )


if __name__ == "__main__":
    unittest.main()
