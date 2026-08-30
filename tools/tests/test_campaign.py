#!/usr/bin/env python3
"""Isolated pressure tests for the ten-theme campaign foundation."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_campaign  # noqa: E402
import check_map  # noqa: E402
import check_profile  # noqa: E402
import map_calibrate  # noqa: E402


def source(claim="Issuer performs this role", *, tag="VERIFIED"):
    item = {
        "claim": claim,
        "tag": tag,
        "source_name": "Issuer annual report",
        "source_date": "2026-08-01",
        "url": "https://example.com/annual-report",
    }
    if tag == "INFERRED":
        item.update({"official_source": True, "basis": "derived from segment disclosure"})
    return item


class CampaignTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for folder in ("chains", "mappings", "companies", "signals", "screens", "stocks"):
            (self.root / "data" / folder).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def chain(self, chain_id, links=("L1", "L2")):
        obj = {"id": chain_id, "signal_id": f"SIG-{chain_id}", "links": [
            {"id": link_id} for link_id in links]}
        path = self.root / "data" / "chains" / f"{chain_id}.json"
        path.write_text(json.dumps(obj))
        return obj

    def mapping(self, chain_id="theme-a", links=("L1", "L2")):
        self.chain(chain_id, links)
        placements = [
            {
                "chain_id": chain_id,
                "link_id": link_id,
                "issuer_id": "ISS-A",
                "role": f"Role on {link_id}",
                "evidence": [source()],
            }
            for link_id in links
        ]
        coverage = [
            {
                "link_id": link_id,
                "status": "EXHAUSTED",
                "distinct_issuer_count": 1,
                "exhausted_reason": "Only one public issuer disclosed this role",
                "searches": [{
                    "query": f"{link_id} public supplier",
                    "tag": "VERIFIED",
                    "source_name": "Exchange issuer search",
                    "source_date": "2026-08-01",
                    "url": "https://example.com/search",
                }],
            }
            for link_id in links
        ]
        obj = {
            "id": f"MAP-{chain_id}",
            "chain_id": chain_id,
            "as_of": "2026-08-01",
            "status": "COMPLETE",
            "target_issuers_per_link": 10,
            "issuers": [{"issuer_id": "ISS-A", "name": "Issuer A"}],
            "listings": [{
                "listing_id": "XNYS:AAA",
                "issuer_id": "ISS-A",
                "ticker": "AAA",
                "exchange": "XNYS",
            }],
            "placements": placements,
            "link_coverage": coverage,
            "confidence_audit": {"verified": len(links)},
            "changelog": [],
        }
        path = self.root / "data" / "mappings" / f"{chain_id}.json"
        path.write_text(json.dumps(obj))
        return path, obj

    def profile(self, placements=None):
        placements = placements or [{"chain_id": "theme-a", "link_id": "L1"}]
        null_metric = {"value": None, "tag": "NULL", "basis": "not disclosed"}
        metrics = {group: copy.deepcopy(null_metric)
                   for group in check_profile.METRIC_GROUPS}
        metrics["revenue"] = {
            "value": 125.0,
            "unit": "USDm",
            "tag": "INFERRED",
            "official_source": True,
            "basis": "converted from the issuer's reported local-currency total",
            "source_name": "Issuer annual report",
            "source_date": "2026-08-01",
            "url": "https://example.com/annual-report",
        }
        return {
            "issuer_id": "ISS-A",
            "issuer_name": "Issuer A",
            "as_of": "2026-08-01",
            "status": "COMPLETE",
            "data_tier": "T3",
            "opportunity_tier": "O2",
            "listing_refs": ["XNYS:AAA"],
            "placements": placements,
            "business_summary": "Specialist supplier",
            "exposure_summary": "Direct disclosed exposure",
            "metrics": metrics,
            "crowdedness_caveats": ["Foreign listing coverage is thinner than US coverage"],
            "catalysts": ["Capacity commissioning"],
            "risks": ["Customer concentration"],
            "data_gaps": ["No quarterly segment margin"],
            "disposition": {"state": "RETAIN", "basis": "Profile complete; keep ranked"},
            "confidence_audit": {"verified": 0, "inferred": 1, "null": 7},
            "changelog": [],
        }


class TestManyToManyAndExhausted(CampaignTree):
    def test_one_issuer_can_occupy_two_links_without_padding(self):
        path, mapping = self.mapping()
        self.assertEqual(check_map.validate_mapping(self.root, path, mapping), [])
        keys = {(p["chain_id"], p["link_id"], p["issuer_id"])
                for p in mapping["placements"]}
        self.assertEqual(len(keys), 2)
        self.assertEqual({p["issuer_id"] for p in mapping["placements"]}, {"ISS-A"})

    def test_honest_exhausted_passes_and_unsearched_exhausted_fails(self):
        path, mapping = self.mapping()
        self.assertEqual(check_map.validate_mapping(self.root, path, mapping), [])
        mapping["link_coverage"][0]["searches"] = []
        mapping["link_coverage"][0]["exhausted_reason"] = ""
        failures = check_map.validate_mapping(self.root, path, mapping)
        self.assertTrue(any("EXHAUSTED needs exhausted_reason" in f for f in failures))
        self.assertTrue(any("EXHAUSTED needs the searches" in f for f in failures))

    def test_duplicate_roles_do_not_pad_distinct_issuer_counts(self):
        path, mapping = self.mapping()
        mapping["placements"].append(copy.deepcopy(mapping["placements"][0]))
        mapping["placements"][-1]["role"] = "A second wording for the same role"
        mapping["link_coverage"][0]["distinct_issuer_count"] = 2
        failures = check_map.validate_mapping(self.root, path, mapping)
        self.assertTrue(any("duplicate (chain_id, link_id, issuer_id)" in f
                            for f in failures))
        self.assertTrue(any("computed 1" in f for f in failures),
                        "coverage must count distinct issuer_id values, not rows")

    def test_one_reusable_profile_counts_once_across_two_themes(self):
        self.mapping("theme-a", ("L1",))
        self.mapping("theme-b", ("L2",))
        profile = self.profile([
            {"chain_id": "theme-a", "link_id": "L1"},
            {"chain_id": "theme-b", "link_id": "L2"},
        ])
        (self.root / "data" / "companies" / "ISS-A.json").write_text(json.dumps(profile))
        campaign = {
            "themes": [
                {"theme_id": "A", "chain_id": "theme-a"},
                {"theme_id": "B", "chain_id": "theme-b"},
            ],
            "targets": dict(check_campaign.LOCKED_TARGETS),
        }
        computed = check_campaign.compute_campaign_completion(self.root, campaign)
        self.assertEqual(computed["completed_profiles"], 1)
        self.assertEqual([row["completed_profiles"] for row in computed["per_theme"]], [1, 1])

    def test_map_calibration_reports_the_four_campaign_depths(self):
        self.mapping("theme-a", ("L1", "L2", "L3", "L4"))
        profile = {
            "issuer_id": "ISS-A",
            "status": "COMPLETE",
            "opportunity_tier": "O2",
            "placements": [
                {"chain_id": "theme-a", "link_id": link_id}
                for link_id in ("L2", "L3", "L4")
            ],
        }
        (self.root / "data" / "companies" / "ISS-A.json").write_text(json.dumps(profile))
        screen = {
            "id": "screen-a",
            "chain_id": "theme-a",
            "buckets": {"pure_play": [
                {"ticker": "AAA", "link_id": "L3"},
                {"ticker": "AAA", "link_id": "L4"},
            ]},
        }
        (self.root / "data" / "screens" / "screen-a.json").write_text(json.dumps(screen))
        stock = {"ticker": "AAA", "chain_id": "theme-a", "link_id": "L4"}
        (self.root / "data" / "stocks" / "AAA__theme-a.json").write_text(json.dumps(stock))
        calibration = map_calibrate.build_calibration(self.root)
        self.assertEqual(calibration["link_yield"]["depth"], {
            "MAPPED": 1,
            "PROFILED": 1,
            "SCREENED": 1,
            "DIVED": 1,
            "UNMAPPED": 0,
            "SCORED": 0,
        })
        self.assertEqual(calibration["link_yield"]["mapping_mode"], "normalized")


class TestProfileDiscipline(CampaignTree):
    def setUp(self):
        super().setUp()
        self.mapping("theme-a")
        self.path = self.root / "data" / "companies" / "ISS-A.json"

    def test_t3_official_source_inference_is_not_penalized(self):
        profile = self.profile()
        self.assertEqual(check_profile.validate_profile(self.root, self.path, profile), [])
        profile["metrics"]["revenue"]["official_source"] = False
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertTrue(any("official_source: true" in f for f in failures))

    def test_profile_cannot_leak_final_verdict_vocabulary(self):
        profile = self.profile()
        profile["disposition"] = {"state": "WATCH", "basis": "looks interesting"}
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertTrue(any("final verdict vocabulary" in f for f in failures))

    def test_every_numeric_metric_is_source_backed(self):
        profile = self.profile()
        profile["metrics"]["revenue"].pop("url")
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertTrue(any("numeric field has no source_name" in f for f in failures))


class TestCampaignCompleteBoundaries(CampaignTree):
    def setUp(self):
        super().setUp()
        self.campaign = {
            "status": "COMPLETE",
            "targets": dict(check_campaign.LOCKED_TARGETS),
            "themes": [{"theme_id": f"T{i}"} for i in range(10)],
        }
        self.computed = {
            "completed_profiles": 200,
            "opportunity_tiers": {"O1": 30, "O2": 170, "O3": 0},
            "o1_complete": 30,
            "o1_final": 30,
            "themes_complete": 10,
            "per_theme": [
                {"theme_id": f"T{i}", "completed_profiles": 20}
                for i in range(10)
            ],
        }

    def test_exact_complete_boundary_passes(self):
        self.assertEqual(check_campaign.completion_gate_failures(
            self.campaign, self.computed), [])

    def test_theme_profile_o1_and_final_boundaries_fail_closed(self):
        probes = [
            ("themes", 9, "exactly 10"),
            ("completed_profiles", 199, ">=200"),
            ("o1", 29, "30-60"),
            ("o1", 61, "30-60"),
            ("o1_final", 29, "FINAL coverage"),
        ]
        for field, value, message in probes:
            with self.subTest(field=field, value=value):
                campaign = copy.deepcopy(self.campaign)
                computed = copy.deepcopy(self.computed)
                if field == "themes":
                    campaign["themes"] = campaign["themes"][:value]
                elif field == "o1":
                    computed["opportunity_tiers"]["O1"] = value
                    computed["o1_complete"] = value
                    computed["o1_final"] = value
                else:
                    computed[field] = value
                failures = check_campaign.completion_gate_failures(campaign, computed)
                self.assertTrue(any(message in finding for finding in failures), failures)


if __name__ == "__main__":
    unittest.main()
