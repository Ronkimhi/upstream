#!/usr/bin/env python3
"""Isolated pressure tests for the ten-theme campaign foundation."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_campaign  # noqa: E402
import check_map  # noqa: E402
import check_profile  # noqa: E402
import map_calibrate  # noqa: E402


BOUNDARY = (
    "Public legal issuers currently performing the exact mapped chain role"
)


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


def listing_evidence(name, exchange, ticker):
    return {
        "legal_issuer": name,
        "exchange": exchange,
        "ticker": ticker,
        "source_excerpt": (
            f"{name} is listed on {exchange} under the symbol {ticker}."
        ),
        "source_name": "Official Exchange issuer directory",
        "source_date": "2026-08-01",
        "url": f"https://exchange.example/issuer/{ticker}",
        "tag": "VERIFIED",
        "source_type": "OFFICIAL_EXCHANGE",
    }


def inferred_metric(value, basis, **extra):
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


def null_metric(field, label=None):
    return {
        "value": None,
        "tag": "NULL",
        "basis": f"{label or field.replace('_', ' ')} is not disclosed",
    }


def substantive_metrics():
    return {
        "revenue": {
            "latest_fy": inferred_metric(125.0, "Official fiscal-year revenue table"),
        },
        "growth": {
            "revenue_cagr_3y": inferred_metric(
                0.12, "Derived from three official annual revenue totals"),
        },
        "margins": {
            "operating_margin": inferred_metric(
                0.18, "Operating profit divided by official revenue"),
        },
        "cash_conversion": {
            "fcf_margin": inferred_metric(
                0.11, "Official cash flow less capex divided by revenue"),
        },
        "leverage": {
            "net_debt_to_ebitda": inferred_metric(
                1.4, "Official net debt divided by reported EBITDA"),
        },
        "quality": {
            "piotroski": inferred_metric(
                7, "Nine Piotroski criteria derived from official statements"),
            "beneish_state": inferred_metric(
                -2.1, "Beneish score derived from official statements"),
        },
        "valuation": {
            "market_cap": inferred_metric(
                1_250_000_000, "Official shares outstanding times quoted price"),
            "pe_ratio": inferred_metric(
                14.2, "Market capitalization divided by official net income"),
        },
        "reverse_dcf": {
            "implied_fcf_cagr": inferred_metric(
                0.09, "Reverse DCF using the disclosed assumption set"),
            "horizon_spread": inferred_metric(
                0.03, "Difference between the five- and ten-year solves"),
        },
    }


def exhausted_searches(link_id, *, accepted_names, rejected_names):
    mixed = bool(accepted_names and rejected_names)
    result_status = (
        "MIXED_RESULTS" if mixed
        else "QUALIFYING_NAMES_FOUND" if accepted_names
        else "NO_QUALIFYING_NAMES"
    )
    official = {
        "query": f"{link_id} official exchange issuer census",
        "tag": "VERIFIED",
        "source_name": "Official Exchange issuer directory",
        "source_date": "2026-08-01",
        "url": "https://exchange.example/search",
        "source_type": "OFFICIAL_EXCHANGE",
        "link_scope": {
            "link_id": link_id,
            "qualification_boundary": BOUNDARY,
        },
        "control_probe_passed": True,
        "hits_examined": len(accepted_names) + len(rejected_names),
        "accepted_names": accepted_names,
        "rejected_names": rejected_names,
        "result_status": result_status,
        "exhaustion_conclusion": (
            f"The official exchange census was fully dispositioned for {link_id}."
        ),
    }
    industry = {
        "query": f"{link_id} industry supplier census",
        "tag": "VERIFIED",
        "source_name": "Credible Industry supplier directory",
        "source_date": "2026-08-01",
        "url": "https://industry.example/search",
        "source_type": "CREDIBLE_INDUSTRY",
        "link_scope": {
            "link_id": link_id,
            "qualification_boundary": BOUNDARY,
        },
        "control_probe_passed": True,
        "hits_examined": 0,
        "accepted_names": [],
        "rejected_names": [],
        "result_status": "NO_QUALIFYING_NAMES",
        "control_probe": {
            "query": "known industry supplier control",
            "expected_name": "Known Industry Supplier",
            "url": "https://industry.example/control",
            "source_type": "CREDIBLE_INDUSTRY",
        },
        "exhaustion_conclusion": (
            f"The industry census found no further qualifying issuers for {link_id}."
        ),
    }
    return [official, industry]


def exhausted_coverage(link_id, *, issuer_name="Issuer A"):
    rejected = [{
        "name": "Private Supplier",
        "reason": "No validated public listing",
    }]
    return {
        "link_id": link_id,
        "status": "EXHAUSTED",
        "distinct_issuer_count": 1,
        "exhausted_reason": "Only one public issuer disclosed this role",
        "combined_search_scope": {
            "link_id": link_id,
            "qualification_boundary": BOUNDARY,
            "domains_covered": ["exchange.example", "industry.example"],
            "source_types_covered": ["OFFICIAL_EXCHANGE", "CREDIBLE_INDUSTRY"],
            "coverage_statement": (
                "The combined exchange and industry planes cover the "
                "defined public-issuer boundary."
            ),
        },
        "searches": exhausted_searches(
            link_id,
            accepted_names=[issuer_name],
            rejected_names=rejected,
        ),
    }


class CampaignTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for folder in (
                "chains", "mappings", "companies", "signals", "screens", "stocks",
                "campaigns"):
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
                "status": "ACTIVE",
                "evidence": [source()],
            }
            for link_id in links
        ]
        coverage = [
            exhausted_coverage(link_id)
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
                "identity_evidence": [
                    listing_evidence("Issuer A", "XNYS", "AAA"),
                ],
            }],
            "placements": placements,
            "link_coverage": coverage,
            "confidence_audit": {"verified": len(links)},
            "changelog": [{
                "ts": "2026-08-30T01:00:00Z",
                "by": "atlas-cartographer",
                "kind": "BUILD",
                "change": "Built the material issuer mapping and exact link census.",
                "prior": None,
            }],
        }
        self.attach_audit(obj)
        path = self.root / "data" / "mappings" / f"{chain_id}.json"
        path.write_text(json.dumps(obj))
        return path, obj

    @staticmethod
    def attach_audit(mapping, *, audited_at="2026-08-30T02:00:00Z"):
        checks = []
        placed_issuer_ids = {
            placement["issuer_id"] for placement in mapping.get("placements") or []
        }
        for listing in mapping.get("listings") or []:
            if listing["issuer_id"] not in placed_issuer_ids:
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
        for coverage in mapping.get("link_coverage") or []:
            if coverage.get("status") != "EXHAUSTED":
                continue
            for search in coverage.get("searches") or []:
                checks.append({
                    "chain_id": mapping["chain_id"],
                    "link_id": coverage["link_id"],
                    "search": search["query"],
                    "identity_ok": True,
                    "role_ok": True,
                    "source_date_ok": True,
                    "source_url": search["url"],
                    "source_date": search["source_date"],
                    "source_excerpt": search["exhaustion_conclusion"],
                    "record_digest": check_map.search_record_digest(search),
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
        mapping["audit"] = {
            "audited_at": audited_at,
            "reviewed_by": "atlas-fresh-context",
            "agent_id": "atlas-fresh-context",
            "transcript_ref": "audit-transcript-20260830-01",
            "review_mode": "FRESH_CONTEXT",
            "independence_limitation": (
                "Repository declarations cannot prove fresh-context independence."
            ),
            "status": "PASS",
            "mapping_fingerprint": check_map.mapping_fingerprint(mapping),
            "links_examined": len(mapping.get("link_coverage") or []),
            "placements_examined": sum(
                1 for row in checks if row.get("issuer_id")
                and not row.get("listing_id")
            ),
            "listings_examined": sum(
                1 for row in checks if row.get("listing_id")
            ),
            "searches_examined": sum(
                1 for row in checks if row.get("search")
            ),
            "target_met_links_examined": len(target_links),
            "exhausted_links_examined": len(exhausted_links),
            "sampled_checks": checks,
            "identity_conflicts": [],
            "role_conflicts": [],
            "source_date_conflicts": [],
            "amendments_required": [],
            "surviving_limitation": (
                "The audit verifies current public sources but cannot prove undisclosed "
                "private supplier relationships."
            ),
        }

    def profile(self, placements=None):
        placements = placements or [{"chain_id": "theme-a", "link_id": "L1"}]
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
            "metrics": copy.deepcopy(substantive_metrics()),
            "crowdedness_caveats": ["Foreign listing coverage is thinner than US coverage"],
            "catalysts": ["Capacity commissioning"],
            "risks": ["Customer concentration"],
            "data_gaps": ["No quarterly segment margin"],
            "disposition": {"state": "RETAIN", "basis": "Profile complete; keep ranked"},
            "confidence_audit": {"verified": 0, "inferred": 10},
            "changelog": [],
        }

    def screen(self, chain_id="theme-a", rows=None):
        rows = rows or [{
            "ticker": "AAA",
            "issuer_id": "ISS-A",
            "listing_id": "XNYS:AAA",
            "link_id": "L1",
        }]
        obj = {
            "id": chain_id,
            "chain_id": chain_id,
            "buckets": {"pure_play": rows},
        }
        path = self.root / "data" / "screens" / f"{chain_id}.json"
        path.write_text(json.dumps(obj))
        return path, obj

    def selection_basis(self, chain_id="theme-a", link_id="L1",
                        listing_id="XNYS:AAA", screen_ref="theme-a"):
        return {
            dimension: {"basis": f"{dimension} reviewed"}
            for dimension in check_profile.SELECTION_DIMENSIONS
        } | {
            "screen_handoff": {
                "screen_ref": screen_ref,
                "chain_id": chain_id,
                "link_id": link_id,
                "listing_id": listing_id,
            }
        }

    def campaign_candidate(self, index, *, selected=False):
        signal_id = f"SIG-20260829-{index:02d}" if selected else None
        row = {
            "candidate_id": f"CAND-{index:02d}",
            "title": f"Candidate {index}",
            "occurrence": {
                "reference": f"Occurrence {index}",
                "claim": f"Occurrence {index} happened",
                "source_name": "Primary source",
                "source_date": "2026-08-01",
                "url": f"https://example.com/occurrence/{index}",
            },
            "dimensions": {
                dimension: f"{dimension} assessment {index}"
                for dimension in check_campaign.SELECTION_DIMENSIONS
            },
            "disposition": "SELECTED" if selected else "EXCLUDED",
            "reason": "Ranks inside the selected ten" if selected else "Ranks below ten",
        }
        if signal_id:
            row["signal_id"] = signal_id
        return row

    def campaign_manifest(self):
        candidates = [
            self.campaign_candidate(i, selected=i < 10) for i in range(25)
        ]
        themes = []
        for i in range(10):
            signal_id = f"SIG-20260829-{i:02d}"
            (self.root / "data" / "signals" / f"{signal_id}.json").write_text("{}")
            themes.append({
                "theme_id": f"THEME-{i:02d}",
                "signal_id": signal_id,
                "chain_id": f"theme-{i:02d}",
                "title": f"Theme {i}",
                "rank": i + 1,
                "rationale": f"Rank {i + 1} on the frozen five-dimension review",
                "stage": "SELECTED",
            })
        campaign = {
            "id": "CAMP-20260829-01",
            "as_of": "2026-08-29",
            "status": "SELECTED",
            "selection_basis": {
                "as_of": "2026-08-29",
                "candidates_examined": len(candidates),
                "criteria": ["Rank all candidates on the five recorded dimensions"],
                "frozen": True,
                "candidates": candidates,
            },
            "themes": themes,
            "alternates": [],
            "exclusions": [],
            "targets": dict(check_campaign.LOCKED_TARGETS),
            "completion": {},
            "blockers": [],
            "confidence_audit": {},
            "changelog": [],
        }
        campaign["completion"] = check_campaign.compute_campaign_completion(
            self.root, campaign)
        return campaign


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

    def test_unlisted_issuer_cannot_satisfy_placement_or_coverage(self):
        path, mapping = self.mapping()
        mapping["target_issuers_per_link"] = 1
        mapping["link_coverage"][0]["status"] = "TARGET_MET"
        mapping["listings"] = []
        failures = check_map.validate_mapping(self.root, path, mapping)
        self.assertTrue(any("validated public listing" in f for f in failures), failures)
        self.assertTrue(any("computed 0" in f for f in failures), failures)
        self.assertTrue(any("TARGET_MET with 0/1" in f for f in failures), failures)

    def test_exhausted_search_requires_non_vacuous_audit_record(self):
        path, mapping = self.mapping()
        search = mapping["link_coverage"][0]["searches"][0]
        for field in (
                "hits_examined", "accepted_names", "rejected_names",
                "result_status", "exhaustion_conclusion"):
            search.pop(field)
        failures = check_map.validate_mapping(self.root, path, mapping)
        for field in (
                "hits_examined", "accepted_names", "rejected_names",
                "result_status", "exhaustion_conclusion"):
            self.assertTrue(any(field in finding for finding in failures),
                            f"{field} was not enforced: {failures}")

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

    def test_map_calibration_uses_mode_per_chain(self):
        self.mapping("normalized-chain", ("N1", "N2"))
        self.chain("legacy-chain", ("L1", "L2"))
        legacy_screen = {
            "id": "legacy-chain",
            "chain_id": "legacy-chain",
            "buckets": {"pure_play": [{"ticker": "LEG", "link_id": "L1"}]},
        }
        (self.root / "data" / "screens" / "legacy-chain.json").write_text(
            json.dumps(legacy_screen))

        calibration = map_calibrate.build_calibration(self.root)
        link_yield = calibration["link_yield"]
        self.assertEqual(link_yield["mapping_mode"], "mixed")
        self.assertEqual(
            calibration["per_chain"]["normalized-chain"]["mapping_mode"], "normalized")
        self.assertEqual(
            calibration["per_chain"]["legacy-chain"]["mapping_mode"],
            "legacy_chain_fallback")
        self.assertEqual(
            calibration["per_chain"]["legacy-chain"]["yielded_a_name"], 1)
        self.assertEqual(link_yield["historical_screened_yield"], {
            "yielded": 1,
            "links_total": 4,
            "yield_rate": 0.25,
        })
        self.assertEqual(link_yield["normalized_mapped_yield"], {
            "yielded": 2,
            "links_total": 2,
            "yield_rate": 1.0,
        })


class TestSemanticUniverseAudit(CampaignTree):
    def target_mapping(self):
        path, mapping = self.mapping("target-theme", ("L1",))
        mapping["issuers"] = [
            {"issuer_id": f"ISS-{i}", "name": f"Issuer {i}"} for i in range(10)
        ]
        mapping["listings"] = [
            {
                "listing_id": f"XNAS:I{i}",
                "issuer_id": f"ISS-{i}",
                "ticker": f"I{i}",
                "exchange": "XNAS",
                "identity_evidence": [
                    listing_evidence(f"Issuer {i}", "XNAS", f"I{i}"),
                ],
            }
            for i in range(10)
        ]
        mapping["placements"] = [
            {
                "chain_id": "target-theme",
                "link_id": "L1",
                "issuer_id": f"ISS-{i}",
                "role": "Qualified public supplier",
                "status": "ACTIVE",
                "evidence": [source(f"Issuer {i} performs the mapped role")],
            }
            for i in range(10)
        ]
        mapping["link_coverage"] = [{
            "link_id": "L1",
            "status": "TARGET_MET",
            "distinct_issuer_count": 10,
        }]
        self.attach_audit(mapping)
        listing_checks = [
            row for row in mapping["audit"]["sampled_checks"]
            if row.get("listing_id")
        ]
        placement_checks = [
            row for row in mapping["audit"]["sampled_checks"]
            if row.get("issuer_id") and not row.get("listing_id")
        ]
        mapping["audit"]["sampled_checks"] = listing_checks + placement_checks[:2]
        mapping["audit"]["placements_examined"] = 2
        mapping["audit"]["target_met_links_examined"] = 1
        mapping["audit"]["mapping_fingerprint"] = check_map.mapping_fingerprint(mapping)
        path.write_text(json.dumps(mapping))
        return path, mapping

    def test_fingerprint_is_deterministic_and_excludes_resolution_metadata(self):
        _path, mapping = self.mapping()
        expected = check_map.mapping_fingerprint(mapping)
        amended = copy.deepcopy(mapping)
        amended["status"] = "ACTIVE"
        amended["as_of"] = "2026-08-29"
        amended["audit"] = {"status": "FAIL"}
        amended["changelog"].append({"change": "Audit metadata only"})
        self.assertEqual(check_map.mapping_fingerprint(amended), expected)

        amended["placements"][0]["role"] = "Changed material role"
        self.assertNotEqual(check_map.mapping_fingerprint(amended), expected)

    def test_complete_requires_a_current_fresh_context_pass(self):
        path, mapping = self.mapping()
        mapping.pop("audit")
        failures = check_map.validate_mapping(self.root, path, mapping)
        self.assertTrue(
            any("COMPLETE mapping needs a current PASS audit" in item
                for item in failures),
            failures,
        )

        self.attach_audit(mapping)
        mapping["placements"][0]["role"] = "Material edit after audit"
        failures = check_map.validate_mapping(self.root, path, mapping)
        self.assertTrue(
            any("fingerprint does not match" in item for item in failures),
            failures,
        )

        mapping["status"] = "ACTIVE"
        mapping.pop("audit")
        self.assertEqual(
            check_map.validate_mapping(self.root, path, mapping),
            [],
            "ACTIVE correction without audit does not require a current PASS",
        )

    def test_target_met_samples_two_distinct_placements(self):
        path, mapping = self.target_mapping()
        self.assertEqual(check_map.validate_mapping(self.root, path, mapping), [])
        mapping["audit"]["sampled_checks"].pop()
        mapping["audit"]["placements_examined"] = 1
        failures = check_map.validate_mapping(self.root, path, mapping)
        self.assertTrue(
            any("needs at least 2" in item for item in failures),
            failures,
        )

    def test_exhausted_samples_every_placement_and_search(self):
        path, mapping = self.mapping("exhausted-theme", ("L1",))
        mapping["audit"]["sampled_checks"] = [
            row
            for row in mapping["audit"]["sampled_checks"]
            if "search" not in row
        ]
        failures = check_map.validate_mapping(self.root, path, mapping)
        self.assertTrue(
            any("did not examine every search" in item for item in failures),
            failures,
        )

    def test_pass_requires_true_checks_empty_conflicts_and_exact_denominators(self):
        path, mapping = self.mapping()
        mapping["audit"]["sampled_checks"][0]["identity_ok"] = False
        mapping["audit"]["identity_conflicts"] = ["Issuer name does not match listing"]
        mapping["audit"]["links_examined"] = 99
        failures = check_map.validate_mapping(self.root, path, mapping)
        self.assertTrue(any("every sampled check" in item for item in failures), failures)
        self.assertTrue(any("empty conflict" in item for item in failures), failures)
        self.assertTrue(any("links_examined is 99" in item for item in failures), failures)

    def test_complete_reopens_only_for_fresh_explained_fail_audit(self):
        path, prior = self.mapping("reopen-theme", ("L1",))
        current = copy.deepcopy(prior)
        current["status"] = "ACTIVE"
        current["audit"]["status"] = "FAIL"
        current["audit"]["audited_at"] = "2026-08-30T03:00:00Z"
        current["audit"]["identity_conflicts"] = ["Issuer identity is ambiguous"]
        current["audit"]["amendments_required"] = [
            "Resolve the issuer identity before re-audit"
        ]
        current["audit"]["mapping_fingerprint"] = check_map.mapping_fingerprint(current)
        current["changelog"].append({
            "ts": "2026-08-30T03:00:00Z",
            "by": "atlas-fresh-context",
            "kind": "AMEND",
            "change": "Semantic audit failed and reopened the mapping as ACTIVE.",
            "prior": "COMPLETE with a PASS audit.",
        })
        self.assertEqual(check_map.validate_mapping(self.root, path, current), [])
        with mock.patch.object(check_map, "_head_json", return_value=prior):
            self.assertEqual(
                check_map.preservation_failures(self.root, path, current),
                [],
            )

        unexplained = copy.deepcopy(current)
        unexplained["changelog"] = copy.deepcopy(prior["changelog"])
        with mock.patch.object(check_map, "_head_json", return_value=prior):
            failures = check_map.preservation_failures(
                self.root, path, unexplained
            )
        self.assertTrue(
            any("status regressed" in item and "changelog" in item
                for item in failures),
            failures,
        )

        material_edit = copy.deepcopy(current)
        material_edit["placements"][0]["role"] = "Audit also rewrote the placement"
        material_edit["audit"]["mapping_fingerprint"] = (
            check_map.mapping_fingerprint(material_edit)
        )
        with mock.patch.object(check_map, "_head_json", return_value=prior):
            failures = check_map.preservation_failures(
                self.root, path, material_edit
            )
        self.assertTrue(
            any("outside audit, status, and changelog" in item for item in failures),
            failures,
        )

    def test_missing_mapping_directory_is_a_clean_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "check_map.py"),
                    "--root",
                    tmp,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no data/mappings files", result.stdout)


class TestProfileDiscipline(CampaignTree):
    def setUp(self):
        super().setUp()
        self.mapping("theme-a")
        self.path = self.root / "data" / "companies" / "ISS-A.json"

    def test_t3_official_source_inference_is_not_penalized(self):
        profile = self.profile()
        self.assertEqual(check_profile.validate_profile(self.root, self.path, profile), [])
        # official_source false on a NON-vendor source is still refused: the allowance is
        # for the fetch plane's vendor block by name, not for any unofficial number.
        profile["metrics"]["revenue"]["latest_fy"]["official_source"] = False
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertTrue(any("official_source: true" in f for f in failures))

    def _vendor(self, value, basis="from data/market fundamentals (yfinance-statements)"):
        return {
            "value": value, "tag": "INFERRED", "official_source": False, "basis": basis,
            "source_name": "yfinance-statements", "source_date": "2026-09-01",
            "url": "https://finance.yahoo.com/quote/AAA/financials",
        }

    def test_t3_vendor_aggregate_metric_is_admitted(self):
        """Ron's decision 2026-09-01: a T2/T3 canonical metric may rest on the vendor block."""
        profile = self.profile()
        profile["metrics"]["revenue"]["latest_fy"] = self._vendor(125.0)
        profile["metrics"]["margins"]["operating_margin"] = self._vendor(0.18)
        self.assertEqual(check_profile.validate_profile(self.root, self.path, profile), [])

    def test_vendor_aggregate_needs_a_basis_and_a_vendor_source_name(self):
        profile = self.profile()
        profile["metrics"]["revenue"]["latest_fy"] = self._vendor(125.0, basis="")
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertTrue(any("derivation basis" in f for f in failures), failures)
        profile = self.profile()
        item = self._vendor(125.0)
        item["source_name"] = "Some blog"
        profile["metrics"]["revenue"]["latest_fy"] = item
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertTrue(any("official_source: true" in f for f in failures), failures)

    def test_t1_filer_may_not_use_a_vendor_aggregate(self):
        profile = self.profile()
        profile["data_tier"] = "T1"
        profile["metrics"]["revenue"]["latest_fy"] = self._vendor(125.0)
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertTrue(any("official_source: true" in f for f in failures), failures)

    def test_o1_requires_official_crosscheck_on_revenue_and_cash_conversion(self):
        self.screen()
        profile = self.profile()
        profile["opportunity_tier"] = "O1"
        profile["selection_basis"] = self.selection_basis()
        profile["metrics"]["margins"]["operating_margin"] = self._vendor(0.18)
        self.assertEqual(check_profile.validate_profile(self.root, self.path, profile), [])
        profile["metrics"]["revenue"]["latest_fy"] = self._vendor(125.0)
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertTrue(any("official-source cross-check" in f for f in failures), failures)

    def test_a_null_basis_claiming_a_missing_market_file_must_be_true(self):
        """AFCONS.NS, 2026-09-01: the basis said data/market/AFCONS.NS.json did not exist
        while data/market/AFCONS-NS.json held 20/20 fields. The claim is now checked."""
        (self.root / "data" / "market").mkdir(exist_ok=True)
        (self.root / "data" / "market" / "AAA-NS.json").write_text("{}")
        profile = self.profile()
        profile["as_of"] = "2026-09-02"
        profile["metrics"]["revenue"]["latest_fy"] = {
            "value": None, "tag": "NULL",
            "basis": "latest fiscal-year revenue unavailable: no market file has ever been "
                     "fetched for AAA.NS (data/market/AAA.NS.json does not exist)",
        }
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertTrue(any("filenames map '.' to '-'" in f for f in failures), failures)
        # The same claim is true when the file is absent, and passes.
        (self.root / "data" / "market" / "AAA-NS.json").unlink()
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertFalse(any("filenames map" in f for f in failures), failures)

    def test_profile_cannot_leak_final_verdict_vocabulary(self):
        profile = self.profile()
        profile["disposition"] = {"state": "WATCH", "basis": "looks interesting"}
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertTrue(any("final verdict vocabulary" in f for f in failures))

    def test_every_numeric_metric_is_source_backed(self):
        profile = self.profile()
        profile["metrics"]["revenue"]["latest_fy"].pop("url")
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertTrue(any("numeric field has no source_name" in f for f in failures))

    def test_a_field_missing_inside_an_existing_market_file_is_not_a_missing_file_claim(self):
        """2026-09-02: the first regex matched 'data/market/ETN.json has fcf_margin missing'
        on nine profiles whose market file was on disk. Only an explicit claim that the
        FILE does not exist is checked against disk."""
        (self.root / "data" / "market").mkdir(exist_ok=True)
        (self.root / "data" / "market" / "AAA.json").write_text("{}")
        profile = self.profile()
        profile["as_of"] = "2026-09-02"
        profile["metrics"]["cash_conversion"]["fcf_margin"] = {
            "value": None, "tag": "NULL",
            "basis": "fcf margin cannot be computed: data/market/AAA.json has fcf_margin "
                     "missing (no capex series in fundamentals)",
        }
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertFalse(any("filenames map" in f for f in failures), failures)
        profile["metrics"]["cash_conversion"]["fcf_margin"]["basis"] = (
            "fcf margin unavailable: data/market/AAA.json does not exist")
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertTrue(any("filenames map" in f for f in failures), failures)

    def test_hollow_metric_groups_do_not_make_a_complete_profile(self):
        profile = self.profile()
        profile["metrics"] = {group: {} for group in check_profile.METRIC_GROUPS}
        failures = check_profile.validate_profile(self.root, self.path, profile)
        for group in check_profile.METRIC_GROUPS:
            self.assertTrue(any(f"metrics.{group} needs" in finding
                                for finding in failures), failures)
        self.assertTrue(any("required fields complete" in finding for finding in failures))

    def test_blocked_o1_fails_the_bidirectional_tier_invariant(self):
        profile = self.profile()
        profile["status"] = "BLOCKED"
        profile["opportunity_tier"] = "O1"
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertTrue(any("BLOCKED profiles must be O3" in finding
                            for finding in failures), failures)
        self.assertTrue(any("O1 profiles must be COMPLETE" in finding
                            for finding in failures), failures)

    def test_o1_requires_exact_mapped_screen_handoff(self):
        self.screen()
        profile = self.profile()
        profile["opportunity_tier"] = "O1"
        profile["selection_basis"] = self.selection_basis()
        self.assertEqual(
            check_profile.validate_profile(self.root, self.path, profile), [])

        profile["selection_basis"]["screen_handoff"]["listing_id"] = "XNYS:OTHER"
        failures = check_profile.validate_profile(self.root, self.path, profile)
        self.assertTrue(any("listing identity does not resolve" in finding
                            for finding in failures), failures)
        self.assertTrue(any("no exact issuer_id/listing_id/link_id row" in finding
                            for finding in failures), failures)


class TestFrozenCampaignSelection(CampaignTree):
    def setUp(self):
        super().setUp()
        self.path = self.root / "data" / "campaigns" / "CAMP-20260829-01.json"

    def test_selected_campaign_requires_candidate_records_not_a_count(self):
        campaign = self.campaign_manifest()
        self.assertEqual(
            check_campaign.validate_campaign(self.root, self.path, campaign), [])
        campaign["selection_basis"]["candidates"].pop()
        failures = check_campaign.validate_campaign(self.root, self.path, campaign)
        self.assertTrue(any("must equal the candidate-record count" in finding
                            for finding in failures), failures)

    def test_candidate_dimensions_and_occurrence_evidence_are_required(self):
        campaign = self.campaign_manifest()
        candidate = campaign["selection_basis"]["candidates"][20]
        candidate["dimensions"].pop("overlap")
        candidate["occurrence"].pop("url")
        failures = check_campaign.validate_campaign(self.root, self.path, campaign)
        self.assertTrue(any("dimensions.overlap" in finding for finding in failures))
        self.assertTrue(any("fetchable http(s) url" in finding for finding in failures))

    def test_frozen_basis_theme_identity_and_history_cannot_mutate(self):
        prior = self.campaign_manifest()
        current = copy.deepcopy(prior)
        current["selection_basis"]["candidates"][20]["reason"] = "Rewritten history"
        current["themes"][0]["title"] = "Rewritten theme"
        current["alternates"] = [{"theme_id": "ALT-1", "reason": "First alternate"}]
        prior["alternates"] = copy.deepcopy(current["alternates"])
        current["alternates"][0]["reason"] = "Rewritten alternate"
        with mock.patch.object(check_campaign, "_head_json", return_value=prior):
            failures = check_campaign.preservation_failures(
                self.root, self.path, current)
        self.assertTrue(any("frozen selection_basis changed" in finding
                            for finding in failures), failures)
        self.assertTrue(any("frozen title changed" in finding
                            for finding in failures), failures)
        self.assertTrue(any("alternates is not append-only" in finding
                            for finding in failures), failures)

    def test_a_theme_may_acquire_its_first_chain_id(self):
        """`run chain` fills a null chain_id, and freezing that was a funnel deadlock.

        A theme is frozen at stage SELECTED with chain_id null -- the gate itself only
        requires a chain to resolve once the stage is past SELECTED. Treating null -> value
        as a frozen-field mutation meant no theme could ever be chained and the campaign
        could never leave SELECTED. Same shape as the O1/O2 screen deadlock closed the same
        day: a field frozen at the value the funnel exists to move it out of.
        """
        prior = self.campaign_manifest()
        current = copy.deepcopy(prior)
        prior["themes"][0]["chain_id"] = None
        current["themes"][0]["chain_id"] = prior["themes"][0]["signal_id"] and "theme-a"
        with mock.patch.object(check_campaign, "_head_json", return_value=prior):
            failures = check_campaign.preservation_failures(
                self.root, self.path, current)
        self.assertFalse(any("frozen chain_id changed" in finding for finding in failures),
                         failures)

    def test_a_named_chain_id_still_cannot_be_rewritten_or_cleared(self):
        """The narrow exemption is null -> value only. Both other transitions stay refused."""
        for label, was, now in (("rewritten", "theme-a", "theme-b"),
                                ("cleared", "theme-a", None)):
            with self.subTest(transition=label):
                prior = self.campaign_manifest()
                current = copy.deepcopy(prior)
                prior["themes"][0]["chain_id"] = was
                current["themes"][0]["chain_id"] = now
                with mock.patch.object(check_campaign, "_head_json", return_value=prior):
                    failures = check_campaign.preservation_failures(
                        self.root, self.path, current)
                self.assertTrue(any("frozen chain_id changed" in finding
                                    for finding in failures), failures)


class TestExactFinalAttribution(CampaignTree):
    def _two_issuer_theme(self):
        path, mapping = self.mapping("theme-a", ("L1",))
        mapping["issuers"].append({"issuer_id": "ISS-B", "name": "Issuer B"})
        mapping["listings"].append({
            "listing_id": "XNAS:AAA",
            "issuer_id": "ISS-B",
            "ticker": "AAA",
            "exchange": "XNAS",
            "identity_evidence": [
                listing_evidence("Issuer B", "XNAS", "AAA"),
            ],
        })
        mapping["placements"].append({
            "chain_id": "theme-a",
            "link_id": "L1",
            "issuer_id": "ISS-B",
            "role": "Second issuer on L1",
            "status": "ACTIVE",
            "evidence": [source()],
        })
        mapping["link_coverage"][0]["distinct_issuer_count"] = 2
        path.write_text(json.dumps(mapping))

        chain_path = self.root / "data" / "chains" / "theme-a.json"
        chain = json.loads(chain_path.read_text())
        chain["links"][0]["heat"] = {"verdict": "UNDISCOVERED"}
        chain["scenarios"] = [{"id": "S1"}]
        chain_path.write_text(json.dumps(chain))

        screen_rows = [
            {
                "ticker": "AAA", "issuer_id": "ISS-A",
                "listing_id": "XNYS:AAA", "link_id": "L1",
            },
            {
                "ticker": "AAA", "issuer_id": "ISS-B",
                "listing_id": "XNAS:AAA", "link_id": "L1",
            },
        ]
        self.screen("theme-a", screen_rows)
        for issuer_id, issuer_name, listing_id in (
                ("ISS-A", "Issuer A", "XNYS:AAA"),
                ("ISS-B", "Issuer B", "XNAS:AAA")):
            profile = self.profile()
            profile.update({
                "issuer_id": issuer_id,
                "issuer_name": issuer_name,
                "opportunity_tier": "O1",
                "listing_refs": [listing_id],
                "selection_basis": self.selection_basis(listing_id=listing_id),
            })
            (self.root / "data" / "companies" / f"{issuer_id}.json").write_text(
                json.dumps(profile))
        return {
            "themes": [{"theme_id": "T1", "chain_id": "theme-a"}],
            "targets": {"profiles_per_theme_min": 0},
        }

    def test_one_ticker_cannot_satisfy_two_issuers(self):
        campaign = self._two_issuer_theme()
        stock = {
            "ticker": "AAA",
            "issuer_id": "ISS-A",
            "listing_id": "XNYS:AAA",
            "chain_id": "theme-a",
            "link_id": "L1",
            "status": "FINAL",
        }
        stock_path = self.root / "data" / "stocks" / "AAA__theme-a.json"
        stock_path.write_text(json.dumps(stock))
        computed = check_campaign.compute_campaign_completion(self.root, campaign)
        self.assertEqual(computed["opportunity_tiers"]["O1"], 2)
        self.assertEqual(computed["o1_final"], 1)
        self.assertEqual(computed["per_theme"][0]["o1_final"], 1)
        self.assertEqual(computed["per_theme"][0]["stage_computed"], "DIVED")

        stock.pop("issuer_id")
        stock.pop("listing_id")
        stock_path.write_text(json.dumps(stock))
        computed = check_campaign.compute_campaign_completion(self.root, campaign)
        self.assertEqual(computed["o1_final"], 0,
                         "ticker fallback must not credit either issuer")

    def test_theme_completes_only_after_every_o1_is_final(self):
        campaign = self._two_issuer_theme()
        for suffix, issuer_id, listing_id in (
                ("a", "ISS-A", "XNYS:AAA"), ("b", "ISS-B", "XNAS:AAA")):
            stock = {
                "ticker": "AAA",
                "issuer_id": issuer_id,
                "listing_id": listing_id,
                "chain_id": "theme-a",
                "link_id": "L1",
                "status": "FINAL",
            }
            (self.root / "data" / "stocks" / f"AAA-{suffix}__theme-a.json").write_text(
                json.dumps(stock))
        computed = check_campaign.compute_campaign_completion(self.root, campaign)
        self.assertEqual(computed["per_theme"][0]["o1_final"], 2)
        self.assertEqual(computed["per_theme"][0]["stage_computed"], "COMPLETE")

    def test_zero_o1_theme_needs_sourced_no_candidate_finding(self):
        campaign = self._two_issuer_theme()
        for path in (self.root / "data" / "companies").glob("*.json"):
            profile = json.loads(path.read_text())
            profile["opportunity_tier"] = "O2"
            profile.pop("selection_basis", None)
            path.write_text(json.dumps(profile))
        computed = check_campaign.compute_campaign_completion(self.root, campaign)
        self.assertEqual(computed["per_theme"][0]["stage_computed"], "SCREENED")

        campaign["themes"][0]["no_candidate_finding"] = source(
            "No profile cleared the O1 selection bar")
        computed = check_campaign.compute_campaign_completion(self.root, campaign)
        self.assertEqual(computed["per_theme"][0]["stage_computed"], "COMPLETE")


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


class TestDepthCampaignBoundaries(CampaignTree):
    """Ron, 2026-09-01: a DEPTH campaign closes on verdicts, not on 200 profiles."""

    def setUp(self):
        super().setUp()
        self.campaign = {
            "status": "COMPLETE",
            "targets": dict(check_campaign.DEPTH_TARGETS),
            "themes": [{"theme_id": f"T{i}"} for i in range(10)],
        }
        self.computed = {
            "completed_profiles": 24,
            "opportunity_tiers": {"O1": 12, "O2": 12, "O3": 40},
            "o1_complete": 12,
            "o1_final": 12,
            "themes_complete": 10,
            "per_theme": [{"theme_id": f"T{i}", "completed_profiles": 2,
                           "o1": 2 if i < 2 else 1} for i in range(10)],
        }

    def test_depth_complete_boundary_passes(self):
        self.assertEqual(check_campaign.completion_gate_failures(
            self.campaign, self.computed), [])

    def test_depth_boundaries_fail_closed(self):
        probes = [
            ("o1_final", 9, "10-20 FINAL"),
            ("o1_final", 21, "10-20 FINAL"),
            ("themes_complete", 9, "every theme complete"),
            ("theme_o1", 4, "needs 1-3"),
            ("theme_o1", 0, "needs 1-3"),
        ]
        for field, value, message in probes:
            with self.subTest(field=field, value=value):
                computed = copy.deepcopy(self.computed)
                if field == "o1_final":
                    computed["opportunity_tiers"]["O1"] = value
                    computed["o1_complete"] = value
                    computed["o1_final"] = value
                elif field == "theme_o1":
                    computed["per_theme"][5]["o1"] = value
                else:
                    computed[field] = value
                failures = check_campaign.completion_gate_failures(self.campaign, computed)
                self.assertTrue(any(message in f for f in failures), failures)

    def test_no_candidate_finding_closes_a_theme_with_zero_o1(self):
        self.campaign["themes"][5]["no_candidate_finding"] = {"claim": "nothing listed"}
        self.computed["per_theme"][5]["o1"] = 0
        self.assertEqual(check_campaign.completion_gate_failures(
            self.campaign, self.computed), [])

    def test_depth_targets_are_frozen_exactly(self):
        campaign = self.campaign
        campaign["targets"]["issuers_per_link"] = 7
        # validate_campaign needs more shape than this fixture has; the targets clause is
        # what is under test, so read it out of the full failure list.
        path = self.root / "data" / "campaigns" / "CAMP-20260901-01.json"
        failures = check_campaign.validate_campaign(self.root, path, campaign)
        self.assertTrue(any("targets.issuers_per_link must be 5" in f for f in failures),
                        failures)
        campaign["targets"]["issuers_per_link"] = 5
        campaign["targets"]["bonus"] = 1
        failures = check_campaign.validate_campaign(self.root, path, campaign)
        self.assertTrue(any("outside its frozen set" in f for f in failures), failures)

    def test_links_in_scope_reads_money_corner_and_undiscovered(self):
        chain = {"links": [
            {"id": "a", "heat": {"verdict": "CROWDED", "money_corner": True}},
            {"id": "b", "heat": {"verdict": "UNDISCOVERED", "money_corner": False}},
            {"id": "c", "heat": {"verdict": "QUIET", "money_corner": False}},
            {"id": "d"},
        ]}
        self.assertEqual(check_campaign.links_in_scope(chain, check_campaign.DEPTH_TARGETS),
                         {"a", "b"})
        self.assertEqual(check_campaign.links_in_scope(chain, check_campaign.LOCKED_TARGETS),
                         {"a", "b", "c", "d"})


if __name__ == "__main__":
    unittest.main()
