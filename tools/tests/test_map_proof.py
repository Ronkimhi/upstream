#!/usr/bin/env python3
"""Isolated adversarial tests for issuer-map proof and audit freshness."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_map  # noqa: E402


class MapProofTree(unittest.TestCase):
    TARGET_LINK = "target-link"
    EXHAUSTED_LINK = "exhausted-link"
    BOUNDARY = (
        "Public legal issuers currently performing the exact mapped chain role"
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data" / "chains").mkdir(parents=True)
        (self.root / "data" / "mappings").mkdir(parents=True)
        self.chain_id = "proof-theme"
        self.path = (
            self.root / "data" / "mappings" / f"{self.chain_id}.json"
        )

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
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

    @staticmethod
    def role_evidence(index, link_id):
        return {
            "claim": f"Issuer {index} performs the exact role on {link_id}.",
            "source_name": f"Issuer {index} annual report",
            "source_date": "2026-08-01",
            "url": f"https://issuer{index}.example/report",
            "tag": "VERIFIED",
        }

    def search(self, *, official, accepted, rejected):
        source_type = "OFFICIAL_EXCHANGE" if official else "CREDIBLE_INDUSTRY"
        domain = "exchange.example" if official else "industry.example"
        result_status = (
            "MIXED_RESULTS"
            if accepted and rejected
            else "QUALIFYING_NAMES_FOUND"
            if accepted
            else "NO_QUALIFYING_NAMES"
        )
        return {
            "query": (
                "official exchange exact-role issuer census"
                if official
                else "industry exact-role supplier census"
            ),
            "source_name": (
                "Official Exchange issuer directory"
                if official
                else "Credible Industry supplier directory"
            ),
            "source_date": "2026-08-01",
            "url": f"https://{domain}/role-census",
            "tag": "VERIFIED",
            "source_type": source_type,
            "control_probe_passed": True,
            "hits_examined": len(accepted) + len(rejected),
            "accepted_names": accepted,
            "rejected_names": [
                {"name": name, "reason": "Does not perform the exact mapped role"}
                for name in rejected
            ],
            "result_status": result_status,
            "link_scope": {
                "link_id": self.EXHAUSTED_LINK,
                "qualification_boundary": self.BOUNDARY,
            },
            "exhaustion_conclusion": (
                "The source was fully dispositioned against the exact link boundary."
            ),
        }

    def mapping(self):
        links = (self.TARGET_LINK, self.EXHAUSTED_LINK)
        (self.root / "data" / "chains" / f"{self.chain_id}.json").write_text(
            json.dumps({
                "id": self.chain_id,
                "links": [{"id": link_id} for link_id in links],
            })
        )
        issuers = [
            {"issuer_id": f"ISS-{index}", "name": f"Issuer {index}"}
            for index in range(10)
        ]
        listings = []
        for index, issuer in enumerate(issuers):
            ticker = f"I{index}"
            listings.append({
                "listing_id": f"XNAS:{ticker}",
                "issuer_id": issuer["issuer_id"],
                "ticker": ticker,
                "exchange": "XNAS",
                "identity_evidence": [
                    self.listing_evidence(issuer["name"], "XNAS", ticker)
                ],
            })
        placements = [
            {
                "chain_id": self.chain_id,
                "link_id": self.TARGET_LINK,
                "issuer_id": issuer["issuer_id"],
                "role": "Qualified public supplier for the target-met link",
                "status": "ACTIVE",
                "evidence": [self.role_evidence(index, self.TARGET_LINK)],
            }
            for index, issuer in enumerate(issuers)
        ]
        placements.extend({
            "chain_id": self.chain_id,
            "link_id": self.EXHAUSTED_LINK,
            "issuer_id": issuers[index]["issuer_id"],
            "role": "Qualified public supplier in the bounded exhausted census",
            "status": "ACTIVE",
            "evidence": [self.role_evidence(index, self.EXHAUSTED_LINK)],
        } for index in range(2))
        searches = [
            self.search(
                official=True,
                accepted=["Issuer 0", "Issuer 1"],
                rejected=[],
            ),
            self.search(
                official=False,
                accepted=[],
                rejected=["Private Supplier"],
            ),
        ]
        mapping = {
            "id": f"MAP-{self.chain_id}",
            "chain_id": self.chain_id,
            "as_of": "2026-08-01",
            "status": "COMPLETE",
            "target_issuers_per_link": 10,
            "issuers": issuers,
            "listings": listings,
            "placements": placements,
            "link_coverage": [
                {
                    "link_id": self.TARGET_LINK,
                    "status": "TARGET_MET",
                    "distinct_issuer_count": 10,
                },
                {
                    "link_id": self.EXHAUSTED_LINK,
                    "status": "EXHAUSTED",
                    "distinct_issuer_count": 2,
                    "exhausted_reason": (
                        "The bounded official and industry census found two issuers."
                    ),
                    "combined_search_scope": {
                        "link_id": self.EXHAUSTED_LINK,
                        "qualification_boundary": self.BOUNDARY,
                        "domains_covered": [
                            "exchange.example",
                            "industry.example",
                        ],
                        "source_types_covered": [
                            "OFFICIAL_EXCHANGE",
                            "CREDIBLE_INDUSTRY",
                        ],
                        "coverage_statement": (
                            "The combined exchange and industry planes cover the "
                            "defined public-issuer boundary."
                        ),
                    },
                    "searches": searches,
                },
            ],
            "confidence_audit": {"verified": len(placements)},
            "changelog": [{
                "ts": "2026-08-30T01:00:00Z",
                "by": "atlas-cartographer",
                "kind": "BUILD",
                "change": "Built the material issuer mapping and exact link census.",
                "prior": None,
            }],
        }
        self.attach_audit(mapping)
        self.path.write_text(json.dumps(mapping))
        return mapping

    def attach_audit(
            self,
            mapping,
            *,
            audited_at="2026-08-30T02:00:00Z",
            status="PASS",
            review_mode="FRESH_CONTEXT"):
        checks = []
        placed_issuer_ids = {
            placement["issuer_id"] for placement in mapping["placements"]
        }
        for listing in mapping["listings"]:
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
        target_seen = 0
        for placement in mapping["placements"]:
            if placement["link_id"] == self.TARGET_LINK:
                if target_seen >= 2:
                    continue
                target_seen += 1
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
        for coverage in mapping["link_coverage"]:
            if coverage["status"] != "EXHAUSTED":
                continue
            for search in coverage["searches"]:
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
            for row in mapping["link_coverage"]
            if row["status"] == "TARGET_MET"
        }
        exhausted_links = {
            row["link_id"]
            for row in mapping["link_coverage"]
            if row["status"] == "EXHAUSTED"
        }
        mapping["audit"] = {
            "audited_at": audited_at,
            "reviewed_by": "atlas-fresh-context",
            "agent_id": "atlas-fresh-context",
            "transcript_ref": "audit-transcript-20260830-01",
            "review_mode": review_mode,
            "independence_limitation": (
                "Repository declarations cannot prove fresh-context independence."
                if review_mode == "FRESH_CONTEXT"
                else "This self-review is same-context and not independent."
            ),
            "status": status,
            "mapping_fingerprint": check_map.mapping_fingerprint(mapping),
            "links_examined": len(mapping["link_coverage"]),
            "placements_examined": sum(
                1 for check in checks if "issuer_id" in check
                and "listing_id" not in check
            ),
            "listings_examined": sum(
                1 for check in checks if "listing_id" in check
            ),
            "searches_examined": sum(
                1 for check in checks if "search" in check
            ),
            "target_met_links_examined": len(target_links),
            "exhausted_links_examined": len(exhausted_links),
            "sampled_checks": checks,
            "identity_conflicts": [],
            "role_conflicts": [],
            "source_date_conflicts": [],
            "amendments_required": [],
            "surviving_limitation": (
                "Private supplier relationships may remain undisclosed."
            ),
        }

    def failures(self, mapping):
        return check_map.validate_mapping(self.root, self.path, mapping)


class TestMapProof(MapProofTree):
    def test_complete_proof_fixture_passes(self):
        mapping = self.mapping()
        self.assertEqual(self.failures(mapping), [])

    def test_placement_status_is_closed_and_only_active_counts(self):
        mapping = self.mapping()
        for status in ("PENDING", "REJECTED", "SUPERSEDED", None, "UNKNOWN", "INACTIVE"):
            with self.subTest(status=status):
                probe = copy.deepcopy(mapping)
                probe["placements"][0]["status"] = status
                findings = self.failures(probe)
                if status in {"PENDING", "REJECTED", "SUPERSEDED"}:
                    self.assertFalse(any("placements[0].status" in item
                                         for item in findings), findings)
                else:
                    self.assertTrue(any("placements[0].status" in item
                                        for item in findings), findings)
                self.assertTrue(any("computed 9" in item
                                    for item in findings), findings)

        inactive_flag = copy.deepcopy(mapping)
        inactive_flag["placements"][0]["active"] = False
        findings = self.failures(inactive_flag)
        self.assertTrue(any("inactive flags are forbidden" in item
                            for item in findings), findings)

    def test_rev_public_listing_and_one_zero_search_attack_fails(self):
        """Exact REV-20260830-01 MADEUP/FAKE plus one shaped zero-hit attack."""
        mapping = self.mapping()
        mapping["issuers"] = [mapping["issuers"][0]]
        mapping["listings"] = [{
            "listing_id": "MADEUP:FAKE",
            "issuer_id": "ISS-0",
            "ticker": "FAKE",
            "exchange": "MADEUP",
        }]
        mapping["placements"] = [
            placement
            for placement in mapping["placements"]
            if placement["link_id"] == self.EXHAUSTED_LINK
            and placement["issuer_id"] == "ISS-0"
        ]
        mapping["link_coverage"] = [{
            "link_id": self.EXHAUSTED_LINK,
            "status": "EXHAUSTED",
            "distinct_issuer_count": 1,
            "exhausted_reason": "No more issuers.",
            "searches": [{
                "query": "public issuer",
                "source_name": "Shaped search",
                "source_date": "2026-08-01",
                "url": "https://search.example/zero",
                "tag": "VERIFIED",
                "hits_examined": 0,
                "accepted_names": [],
                "rejected_names": [],
                "result_status": "DONE",
                "exhaustion_conclusion": "No more issuers.",
            }],
        }]
        mapping["status"] = "COMPLETE"
        mapping["audit"] = {
            "audited_at": "2026-08-30T02:00:00Z",
            "reviewed_by": "atlas-fresh-context",
            "status": "PASS",
            "mapping_fingerprint": check_map.mapping_fingerprint(mapping),
            "links_examined": 1,
            "placements_examined": 1,
            "sampled_checks": [{
                "chain_id": self.chain_id,
                "link_id": self.EXHAUSTED_LINK,
                "issuer_id": "ISS-0",
                "identity_ok": True,
                "role_ok": True,
                "source_date_ok": True,
                "source_opened": "https://issuer0.example/report",
            }],
            "identity_conflicts": [],
            "role_conflicts": [],
            "source_date_conflicts": [],
            "exhausted_links_examined": 1,
            "amendments_required": [],
            "surviving_limitation": "No known limitation.",
        }
        failures = self.failures(mapping)
        for expected in (
            "identity_evidence",
            "at least two distinct source domains",
            "control_probe_passed",
            "result_status 'DONE' not in",
            "combined_search_scope",
            "audit missing keys",
            "record_digest",
        ):
            self.assertTrue(
                any(expected in finding for finding in failures),
                f"{expected!r} was not refused: {failures}",
            )

    def test_listing_identity_must_match_issuer_exchange_and_ticker(self):
        mapping = self.mapping()
        evidence = mapping["listings"][0]["identity_evidence"][0]
        evidence.update({
            "legal_issuer": "Different Legal Issuer",
            "exchange": "MADEUP",
            "ticker": "FAKE",
            "source_type": "BLOG",
        })
        failures = self.failures(mapping)
        for field in ("legal_issuer", "exchange", "ticker", "source_type"):
            self.assertTrue(
                any(field in finding for finding in failures), failures
            )
        self.assertTrue(
            any("validated public listing" in finding for finding in failures),
            failures,
        )

    def test_listing_identity_excerpt_must_bind_exact_claim_and_audit_sample(self):
        """Cass's mismatched-excerpt probe: valid metadata cannot bind alien text."""
        mapping = self.mapping()
        evidence = mapping["listings"][0]["identity_evidence"][0]
        evidence["source_excerpt"] = (
            "Unrelated Issuer is listed on XNYS under the symbol WRONG."
        )
        mapping["audit"]["mapping_fingerprint"] = check_map.mapping_fingerprint(mapping)
        failures = self.failures(mapping)
        for expected in ("exact legal_issuer", "exact exchange", "exact ticker"):
            self.assertTrue(
                any(expected in finding for finding in failures), failures
            )

        mapping = self.mapping()
        mapping["listings"][0]["identity_evidence"][0].pop("source_excerpt")
        failures = self.failures(mapping)
        self.assertTrue(
            any("source_excerpt must be substantive official listing evidence"
                in finding for finding in failures),
            failures,
        )

        mapping = self.mapping()
        listing_check = next(
            check for check in mapping["audit"]["sampled_checks"]
            if check.get("listing_id") == "XNAS:I0"
        )
        listing_check["source_excerpt"] = (
            "Unrelated Issuer is listed on XNAS under the symbol I0."
        )
        mapping["audit"]["mapping_fingerprint"] = check_map.mapping_fingerprint(mapping)
        failures = self.failures(mapping)
        self.assertTrue(
            any("does not resolve exactly one current listing identity claim" in finding
                for finding in failures),
            failures,
        )

        mapping = self.mapping()
        listing = mapping["listings"][0]
        listing_check = next(
            check for check in mapping["audit"]["sampled_checks"]
            if check.get("listing_id") == "XNAS:I0"
        )
        old_listing_digest = check_map.listing_identity_digest(listing)
        old_mapping_fingerprint = check_map.mapping_fingerprint(mapping)
        listing["identity_evidence"][0]["source_excerpt"] = (
            "Issuer 0 trades on XNAS with the symbol I0."
        )
        listing_check["record_digest"] = check_map.listing_identity_digest(listing)
        mapping["audit"]["mapping_fingerprint"] = check_map.mapping_fingerprint(mapping)
        self.assertNotEqual(check_map.listing_identity_digest(listing), old_listing_digest)
        self.assertNotEqual(
            check_map.mapping_fingerprint(mapping), old_mapping_fingerprint
        )
        failures = self.failures(mapping)
        self.assertTrue(
            any("does not resolve exactly one current listing identity claim" in finding
                for finding in failures),
            failures,
        )

    def test_listing_identity_excerpt_supports_distinct_multi_evidence(self):
        mapping = self.mapping()
        listing = mapping["listings"][0]
        second = copy.deepcopy(listing["identity_evidence"][0])
        second.update({
            "source_name": "Official Registry listing record",
            "url": "https://registry.example/listings/I0",
            "source_type": "OFFICIAL_REGISTRY",
            "source_excerpt": (
                "Issuer 0 is registered on XNAS under the symbol I0."
            ),
        })
        listing["identity_evidence"].append(second)
        self.attach_audit(mapping)
        self.assertEqual(self.failures(mapping), [])

        mapping = self.mapping()
        listing = mapping["listings"][0]
        listing["identity_evidence"].append(
            copy.deepcopy(listing["identity_evidence"][0])
        )
        self.attach_audit(mapping)
        failures = self.failures(mapping)
        self.assertTrue(
            any("duplicate ambiguous official identity evidence" in finding
                for finding in failures),
            failures,
        )

    def test_exhausted_requires_source_breadth_and_exact_dispositions(self):
        mapping = self.mapping()
        coverage = mapping["link_coverage"][1]
        second = coverage["searches"][1]
        second["url"] = "https://sub.exchange.example/industry"
        second["source_type"] = "OFFICIAL_EXCHANGE"
        coverage["combined_search_scope"]["domains_covered"] = ["exchange.example"]
        coverage["combined_search_scope"]["source_types_covered"] = [
            "OFFICIAL_EXCHANGE"
        ]
        second["rejected_names"] = [{
            "name": "Issuer 0",
            "reason": "Rejected despite being counted",
        }]
        failures = self.failures(mapping)
        for expected in (
            "at least two distinct source domains",
            "at least two distinct source_types",
            "primary issuer or credible industry",
            "rejected_names are counted as placements",
            "names cannot be both accepted and rejected",
        ):
            self.assertTrue(
                any(expected in finding for finding in failures),
                f"{expected!r} was not refused: {failures}",
            )

    def test_genuine_zero_needs_same_plane_control_and_can_pass(self):
        mapping = self.mapping()
        zero = mapping["link_coverage"][1]["searches"][1]
        zero["hits_examined"] = 0
        zero["rejected_names"] = []
        zero["result_status"] = "NO_QUALIFYING_NAMES"
        self.attach_audit(mapping)
        failures = self.failures(mapping)
        self.assertTrue(
            any("control_probe must document" in finding for finding in failures),
            failures,
        )

        zero["control_probe"] = {
            "query": "known industry supplier control",
            "expected_name": "Known Industry Supplier",
            "url": "https://wrong.example/control",
            "source_type": "CREDIBLE_INDUSTRY",
        }
        self.attach_audit(mapping)
        failures = self.failures(mapping)
        self.assertTrue(
            any("same source domain" in finding for finding in failures), failures
        )

        zero["control_probe"]["url"] = "https://industry.example/control"
        self.attach_audit(mapping)
        self.assertEqual(self.failures(mapping), [])

    def test_rev_later_material_amendment_and_stale_sample_fail(self):
        """Exact REV-20260830-01 stale-role audit attack with recomputed fingerprint."""
        mapping = self.mapping()
        mapping["placements"][0]["role"] = "Material role changed after audit"
        mapping["changelog"].append({
            "ts": "2026-08-30T03:00:00Z",
            "by": "atlas-cartographer",
            "kind": "AMEND",
            "change": "Changed the material role after the semantic audit.",
            "prior": "Earlier role wording.",
        })
        mapping["audit"]["mapping_fingerprint"] = check_map.mapping_fingerprint(
            mapping
        )
        failures = self.failures(mapping)
        self.assertTrue(
            any("earlier than material mapping changelog" in item
                for item in failures),
            failures,
        )
        self.assertTrue(
            any("record_digest is stale for the current placement claim" in item
                for item in failures),
            failures,
        )

    def test_audit_rejects_vacuous_unmatched_and_stale_samples(self):
        mapping = self.mapping()
        check = mapping["audit"]["sampled_checks"][0]
        check["source_excerpt"] = "OK"
        check["source_url"] = "https://unmatched.example/source"
        check["record_digest"] = "0" * 64
        failures = self.failures(mapping)
        for expected in (
            "source_excerpt must be substantive",
            "record_digest is stale",
        ):
            self.assertTrue(
                any(expected in finding for finding in failures), failures
            )

    def test_audit_requires_all_searches_two_target_samples_and_denominators(self):
        mapping = self.mapping()
        checks = mapping["audit"]["sampled_checks"]
        first_search = next(
            index for index, check in enumerate(checks) if "search" in check
        )
        checks.pop(first_search)
        second_target = next(
            index
            for index, check in enumerate(checks)
            if check.get("link_id") == self.TARGET_LINK
            and check.get("issuer_id") == "ISS-1"
        )
        checks.pop(second_target)
        failures = self.failures(mapping)
        for expected in (
            "did not examine every search",
            "needs at least 2",
            "resolved current-content denominator",
        ):
            self.assertTrue(
                any(expected in finding for finding in failures), failures
            )

    def test_pass_audit_content_binds_every_counted_listing_identity(self):
        mapping = self.mapping()
        listing_check = next(
            check for check in mapping["audit"]["sampled_checks"]
            if check.get("listing_id") == "XNAS:I0"
        )
        mapping["listings"][0]["identity_evidence"][0]["ticker"] = "CHANGED"
        mapping["audit"]["mapping_fingerprint"] = check_map.mapping_fingerprint(mapping)
        failures = self.failures(mapping)
        self.assertTrue(
            any("stale for the current listing identity" in finding
                for finding in failures),
            failures,
        )

        mapping = self.mapping()
        mapping["audit"]["sampled_checks"] = [
            check for check in mapping["audit"]["sampled_checks"]
            if check.get("listing_id") != "XNAS:I0"
        ]
        mapping["audit"]["listings_examined"] -= 1
        failures = self.failures(mapping)
        self.assertTrue(
            any("did not examine every listing counted by placements" in finding
                for finding in failures),
            failures,
        )

        mapping = self.mapping()
        listing_check = next(
            check for check in mapping["audit"]["sampled_checks"]
            if check.get("listing_id") == "XNAS:I0"
        )
        listing_check["source_url"] = "https://issuer.example/blog/listing"
        failures = self.failures(mapping)
        self.assertTrue(
            any("self-labelled blog" in finding for finding in failures),
            failures,
        )

        for field, value in (
                ("issuer_id", "ISS-1"),
                ("source_type", "OFFICIAL_REGISTRY"),
                ("source_url", "https://exchange.example/issuer/WRONG"),
                ("source_date", "2026-08-02")):
            with self.subTest(field=field):
                mapping = self.mapping()
                listing_check = next(
                    check for check in mapping["audit"]["sampled_checks"]
                    if check.get("listing_id") == "XNAS:I0"
                )
                listing_check[field] = value
                failures = self.failures(mapping)
                self.assertTrue(
                    any("does not resolve exactly one current listing identity claim"
                        in finding for finding in failures),
                    failures,
                )

        mapping = self.mapping()
        listing_check = next(
            check for check in mapping["audit"]["sampled_checks"]
            if check.get("listing_id") == "XNAS:I0"
        )
        duplicate = copy.deepcopy(listing_check)
        mapping["audit"]["sampled_checks"].append(duplicate)
        failures = self.failures(mapping)
        self.assertTrue(
            any("duplicate placement, listing, or search" in finding
                for finding in failures),
            failures,
        )

    def test_audit_provenance_and_self_review_limitation_are_explicit(self):
        mapping = self.mapping()
        for field in ("agent_id", "transcript_ref", "independence_limitation"):
            broken = copy.deepcopy(mapping)
            broken["audit"].pop(field)
            failures = self.failures(broken)
            self.assertTrue(
                any(field in finding for finding in failures),
                f"{field!r} was not refused: {failures}",
            )

        mapping["audit"]["review_mode"] = "SELF_REVIEW"
        mapping["audit"]["independence_limitation"] = (
            "Context provenance is declared for this audit record."
        )
        failures = self.failures(mapping)
        self.assertTrue(
            any("SELF_REVIEW independence_limitation" in item
                for item in failures),
            failures,
        )

        mapping["audit"]["independence_limitation"] = (
            "This self-review is same-context and not independent."
        )
        self.assertEqual(self.failures(mapping), [])

    def test_complete_to_active_reopen_remains_safe(self):
        prior = self.mapping()
        current = copy.deepcopy(prior)
        current["status"] = "ACTIVE"
        current["audit"]["status"] = "FAIL"
        current["audit"]["audited_at"] = "2026-08-30T03:00:00Z"
        current["audit"]["identity_conflicts"] = [
            "Official listing identity needs correction"
        ]
        current["audit"]["amendments_required"] = [
            "Correct the official listing identity before re-audit"
        ]
        current["audit"]["mapping_fingerprint"] = (
            check_map.mapping_fingerprint(current)
        )
        current["changelog"].append({
            "ts": "2026-08-30T03:00:00Z",
            "by": "atlas-fresh-context",
            "kind": "AMEND",
            "change": "Semantic audit FAIL reopens the map as ACTIVE for correction.",
            "prior": "The map was COMPLETE with a prior PASS audit.",
        })
        self.assertEqual(self.failures(current), [])
        with mock.patch.object(check_map, "_head_json", return_value=prior):
            self.assertEqual(
                check_map.preservation_failures(self.root, self.path, current),
                [],
            )


if __name__ == "__main__":
    unittest.main()
