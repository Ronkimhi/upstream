#!/usr/bin/env python3
"""Pressure tests for the broker earnings-quality NULL exception.

Ron's decision, 2026-09-15 ("Yes, for brokers only"): quality.piotroski and
quality.beneish_state may carry state NOT_APPLICABLE (value null) on an O1 profile ONLY
for the four named insurance-brokerage issuers, ONLY when the market file's matching
quality score is PENDING_DATA on nothing but cost_of_revenue_fy and/or sga_fy, and ONLY
with a basis naming both the missing input and the industry reason. Any other issuer, any
other missing input, or any other NULL field still blocks O1.
"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_analyst  # noqa: E402
import check_profile  # noqa: E402

BROKER_ISSUER = "AON"
NON_BROKER_ISSUER = "ISS-A"


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


def broker_na(field, missing_terms="cost of revenue"):
    return {
        "value": None,
        "tag": "NULL",
        "state": "NOT_APPLICABLE",
        "basis": (
            f"{field.replace('_', ' ')} cannot be computed: AON is an insurance broker "
            f"and carries no cost-of-goods line, so {missing_terms} is permanently absent "
            "from official filings (market file quality is PENDING_DATA solely on that "
            "input)"
        ),
    }


def substantive_metrics(issuer_id=NON_BROKER_ISSUER):
    return {
        "revenue": {"latest_fy": inferred(2025, "Official fiscal-year revenue table")},
        "growth": {
            "revenue_cagr_3y": inferred(
                0.12, "Derived from three official annual revenue totals"),
        },
        "margins": {
            "operating_margin": inferred(0.18, "Operating profit divided by official revenue"),
        },
        "cash_conversion": {
            "fcf_margin": inferred(0.11, "Official cash flow less capex divided by revenue"),
        },
        "leverage": {
            "net_debt_to_ebitda": inferred(1.4, "Official net debt divided by reported EBITDA"),
        },
        "quality": {
            "piotroski": inferred(7, "Nine Piotroski criteria derived from official statements"),
            "beneish_state": inferred(-2.1, "Beneish score derived from official statements"),
        },
        "valuation": {
            "market_cap": inferred(1_250_000_000, "Official shares outstanding times quoted price"),
            "pe_ratio": inferred(14.2, "Market capitalization divided by official net income"),
        },
        "reverse_dcf": {
            "implied_fcf_cagr": inferred(0.09, "Reverse DCF using the disclosed assumption set"),
            "horizon_spread": inferred(0.03, "Difference between the five- and ten-year solves"),
        },
    }


class ProfileTree(unittest.TestCase):
    """Same fixture shape as test_profile_substance.py, plus a market file so the broker
    exception's on-disk cross-check has something real to read."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for folder in ("companies", "mappings", "screens", "market"):
            (self.root / "data" / folder).mkdir(parents=True, exist_ok=True)
        # A subclass (BrokerProfileTree) sets these before calling super().setUp(); only
        # fall back to the plain non-broker identity when nothing has claimed them yet.
        self.issuer_id = getattr(self, "issuer_id", NON_BROKER_ISSUER)
        self.ticker = getattr(self, "ticker", "AAA")
        self._write_tree()

    def _write_tree(self):
        mapping = {
            "chain_id": "theme-a",
            "issuers": [{"issuer_id": self.issuer_id, "name": "Issuer A"}],
            "listings": [{
                "listing_id": "XNYS:AAA",
                "issuer_id": self.issuer_id,
                "ticker": self.ticker,
                "exchange": "XNYS",
            }],
            "placements": [{
                "chain_id": "theme-a",
                "link_id": "L1",
                "issuer_id": self.issuer_id,
            }],
        }
        (self.root / "data" / "mappings" / "theme-a.json").write_text(json.dumps(mapping))
        screen = {
            "id": "theme-a",
            "chain_id": "theme-a",
            "buckets": {"pure_play": [{
                "ticker": self.ticker,
                "issuer_id": self.issuer_id,
                "listing_id": "XNYS:AAA",
                "link_id": "L1",
            }]},
        }
        (self.root / "data" / "screens" / "theme-a.json").write_text(json.dumps(screen))
        self.path = self.root / "data" / "companies" / f"{self.issuer_id}.json"

    def write_market_quality(self, *, piotroski=None, beneish=None):
        data = {"quality": {}}
        if piotroski is not None:
            data["quality"]["piotroski"] = piotroski
        if beneish is not None:
            data["quality"]["beneish"] = beneish
        (self.root / "data" / "market" / f"{self.ticker}.json").write_text(json.dumps(data))

    def tearDown(self):
        self.tmp.cleanup()

    def profile(self, metrics=None, *, opportunity_tier="O1"):
        return {
            "issuer_id": self.issuer_id,
            "issuer_name": "Issuer A",
            "as_of": "2026-09-15",
            "status": "COMPLETE",
            "data_tier": "T3",
            "opportunity_tier": opportunity_tier,
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

    def o1_profile(self, metrics=None):
        profile = self.profile(metrics, opportunity_tier="O1")
        profile["selection_basis"] = self.selection_basis()
        return profile

    def validate(self, profile):
        return check_profile.validate_profile(self.root, self.path, profile)


class BrokerProfileTree(ProfileTree):
    """Same tree, but the mapped issuer is AON (in BROKER_NO_COGS_ISSUERS)."""

    def setUp(self):
        self.issuer_id = BROKER_ISSUER
        self.ticker = "AON"
        super().setUp()
        # Both scores PENDING_DATA solely on the broker's structurally-missing inputs.
        self.write_market_quality(
            piotroski={"score": None, "state": "PENDING_DATA",
                       "missing": ["cost_of_revenue_fy"],
                       "inputs_found": 8, "inputs_needed": 9},
            beneish={"score": None, "state": "PENDING_DATA",
                     "missing": ["cost_of_revenue_fy", "sga_fy"],
                     "inputs_found": 10, "inputs_needed": 12},
        )

    def broker_metrics(self, **overrides):
        metrics = substantive_metrics(self.issuer_id)
        metrics["quality"] = {
            "piotroski": broker_na("piotroski", "cost_of_revenue_fy"),
            "beneish_state": broker_na("beneish_state", "sga_fy"),
        }
        metrics["quality"].update(overrides)
        return metrics


class TestBrokerExceptionAccepts(BrokerProfileTree):
    def test_broker_o1_with_not_applicable_quality_passes(self):
        profile = self.o1_profile(self.broker_metrics())
        self.assertEqual(self.validate(profile), [])

    def test_missing_only_sga_still_passes(self):
        self.write_market_quality(
            piotroski={"score": None, "state": "PENDING_DATA",
                       "missing": ["cost_of_revenue_fy"],
                       "inputs_found": 8, "inputs_needed": 9},
            beneish={"score": None, "state": "PENDING_DATA",
                     "missing": ["sga_fy"],
                     "inputs_found": 11, "inputs_needed": 12},
        )
        profile = self.o1_profile(self.broker_metrics())
        self.assertEqual(self.validate(profile), [])


class TestBrokerExceptionRefuses(BrokerProfileTree):
    def test_not_applicable_on_a_non_broker_still_blocks_o1(self):
        # Same NOT_APPLICABLE state, same market PENDING_DATA shape, but the issuer is not
        # in BROKER_NO_COGS_ISSUERS: the exception must not fire.
        tree = ProfileTree()
        tree.setUp()
        self.addCleanup(tree.tearDown)
        tree.write_market_quality(
            piotroski={"score": None, "state": "PENDING_DATA",
                       "missing": ["cost_of_revenue_fy"],
                       "inputs_found": 8, "inputs_needed": 9},
            beneish={"score": None, "state": "PENDING_DATA",
                     "missing": ["cost_of_revenue_fy", "sga_fy"],
                     "inputs_found": 10, "inputs_needed": 12},
        )
        metrics = substantive_metrics(tree.issuer_id)
        metrics["quality"] = {
            "piotroski": broker_na("piotroski"),
            "beneish_state": broker_na("beneish_state"),
        }
        profile = tree.o1_profile(metrics)
        failures = tree.validate(profile)
        self.assertTrue(
            any("O1 cannot retain a NULL critical canonical field" in f for f in failures),
            failures,
        )

    def test_not_applicable_when_another_input_is_also_missing_still_blocks_o1(self):
        # The market file's PENDING_DATA reason names a field outside the closed
        # {cost_of_revenue_fy, sga_fy} set: the gap is not structural, just not yet
        # researched, so the exception does not cover it.
        self.write_market_quality(
            piotroski={"score": None, "state": "PENDING_DATA",
                       "missing": ["cost_of_revenue_fy", "shares_fy"],
                       "inputs_found": 7, "inputs_needed": 9},
            beneish={"score": None, "state": "PENDING_DATA",
                     "missing": ["cost_of_revenue_fy", "sga_fy"],
                     "inputs_found": 10, "inputs_needed": 12},
        )
        profile = self.o1_profile(self.broker_metrics())
        failures = self.validate(profile)
        self.assertTrue(
            any("metrics.quality.piotroski" in f
                and "O1 cannot retain a NULL critical canonical field" in f
                for f in failures),
            failures,
        )

    def test_broker_with_a_different_null_field_still_blocks_o1(self):
        # AON, correctly NOT_APPLICABLE on quality -- but revenue.latest_fy is a plain
        # NULL. The broker exception is scoped to quality.piotroski/beneish_state only.
        metrics = self.broker_metrics()
        metrics["revenue"]["latest_fy"] = missing("latest_fy", "Latest fiscal year")
        profile = self.o1_profile(metrics)
        failures = self.validate(profile)
        self.assertTrue(
            any("metrics.revenue.latest_fy" in f
                and "O1 cannot retain a NULL critical canonical field" in f
                for f in failures),
            failures,
        )
        # And quality itself still cleared, so this is the only NULL-field complaint.
        self.assertFalse(
            any("metrics.quality" in f and "NULL critical" in f for f in failures), failures)

    def test_missing_broker_basis_wording_still_blocks_o1(self):
        # State NOT_APPLICABLE, market file genuinely PENDING_DATA on the right inputs --
        # but the basis never says "broker" or names the missing input in readable prose.
        metrics = self.broker_metrics()
        metrics["quality"]["piotroski"] = {
            "value": None, "tag": "NULL", "state": "NOT_APPLICABLE",
            "basis": "Piotroski score is not available for this issuer",
        }
        profile = self.o1_profile(metrics)
        failures = self.validate(profile)
        self.assertTrue(
            any("metrics.quality.piotroski" in f
                and "O1 cannot retain a NULL critical canonical field" in f
                for f in failures),
            failures,
        )

    def test_market_file_not_pending_still_blocks_o1(self):
        # The market plane actually solved the score: NOT_APPLICABLE would be a lie.
        self.write_market_quality(
            piotroski={"score": 6, "state": "MIDDLING", "inputs_found": 9, "inputs_needed": 9},
            beneish={"score": None, "state": "PENDING_DATA",
                     "missing": ["cost_of_revenue_fy", "sga_fy"],
                     "inputs_found": 10, "inputs_needed": 12},
        )
        profile = self.o1_profile(self.broker_metrics())
        failures = self.validate(profile)
        self.assertTrue(
            any("metrics.quality.piotroski" in f
                and "O1 cannot retain a NULL critical canonical field" in f
                for f in failures),
            failures,
        )

    def test_generic_null_state_without_not_applicable_still_blocks_o1(self):
        # Plain NULL/PENDING_DATA state (no NOT_APPLICABLE) never qualifies, broker or not.
        metrics = self.broker_metrics()
        metrics["quality"]["piotroski"] = missing("piotroski")
        profile = self.o1_profile(metrics)
        failures = self.validate(profile)
        self.assertTrue(
            any("metrics.quality.piotroski" in f
                and "O1 cannot retain a NULL critical canonical field" in f
                for f in failures),
            failures,
        )


class TestBrokerBasisHelper(unittest.TestCase):
    def test_broker_quality_basis_ok_requires_broker_and_missing_input(self):
        good = ("Piotroski cannot be computed: AON is an insurance broker with no "
                "cost of revenue line, so cost_of_revenue_fy is permanently absent")
        self.assertTrue(check_profile.broker_quality_basis_ok(good))
        self.assertFalse(check_profile.broker_quality_basis_ok(
            "Piotroski cannot be computed: data is not yet available"))
        self.assertFalse(check_profile.broker_quality_basis_ok(
            "AON is an insurance broker but we have not looked into this yet"))
        self.assertFalse(check_profile.broker_quality_basis_ok(""))
        self.assertFalse(check_profile.broker_quality_basis_ok(None))


class TestEarningsQualityVetoBrokerBasis(unittest.TestCase):
    """check_analyst's veto: a null grade on one of the four broker issuers must surface
    the same broker + missing-input wording, or the gate refuses it -- a null grade is
    never a silent pass on quality."""

    def dive(self, basis):
        return {
            "issuer_id": BROKER_ISSUER,
            "verdict": "WATCH",
            "earnings_quality": {"grade": None, "basis": basis},
        }

    def test_broker_null_grade_with_broker_basis_is_accepted(self):
        d = self.dive(
            "Piotroski and Beneish could not be computed: AON is an insurance broker "
            "with no cost of revenue line, cost_of_revenue_fy is permanently absent"
        )
        self.assertEqual(check_analyst.broker_null_grade_basis_failures(d), [])

    def test_broker_null_grade_with_generic_basis_is_refused(self):
        d = self.dive("Quality scores were not available at dive time")
        failures = check_analyst.broker_null_grade_basis_failures(d)
        self.assertTrue(
            any("must say the Piotroski/Beneish scores could not be computed" in f
                for f in failures),
            failures,
        )

    def test_non_broker_null_grade_is_unaffected_by_the_broker_rule(self):
        d = self.dive("Quality scores were not available at dive time")
        d["issuer_id"] = NON_BROKER_ISSUER
        self.assertNotIn(d["issuer_id"], check_profile.BROKER_NO_COGS_ISSUERS)
        # A non-broker with no basis wording at all passes THIS rule (the plain "basis
        # missing" and grade-cap rules elsewhere in check_analyst still apply to it).
        self.assertEqual(check_analyst.broker_null_grade_basis_failures(d), [])

    def test_broker_with_a_computed_grade_is_unaffected_by_the_broker_rule(self):
        d = self.dive("some basis")
        d["earnings_quality"]["grade"] = "B"
        self.assertEqual(check_analyst.broker_null_grade_basis_failures(d), [])


if __name__ == "__main__":
    unittest.main()
