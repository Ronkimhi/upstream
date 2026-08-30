#!/usr/bin/env python3
"""Issuer-denominator probes shared by campaign completion and the dashboard."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_campaign  # noqa: E402


def _load_build():
    spec = importlib.util.spec_from_file_location(
        "campaign_denominator_build", ROOT / "app" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build()


class DenominatorFixture:
    def __init__(self, root):
        self.root = Path(root)
        self.data = self.root / "data"
        for folder in ("campaigns", "mappings", "companies"):
            (self.data / folder).mkdir(parents=True)
        self.themes = []
        self.chains = []
        self.requests = {"requests": []}

    @staticmethod
    def listing(issuer_id, listing_id, ticker, exchange):
        issuer_name = f"Issuer {issuer_id.rsplit('-', 1)[-1]}"
        return {
            "issuer_id": issuer_id,
            "listing_id": listing_id,
            "ticker": ticker,
            "exchange": exchange,
            "identity_evidence": [{
                "claim": f"{issuer_name} is listed as {exchange}:{ticker}",
                "tag": "VERIFIED",
                "source_name": f"{exchange} issuer directory",
                "source_date": "2026-08-30",
                "url": "https://example.invalid/official-listing",
                "source_type": "OFFICIAL_EXCHANGE",
                "legal_issuer": issuer_name,
                "exchange": exchange,
                "ticker": ticker,
            }],
        }

    def add_theme(self, chain_id, *, issuers, listings, placements, links=("L1",)):
        placements = [
            {**placement, "status": placement.get("status", "ACTIVE")}
            for placement in placements
        ]
        self.themes.append({
            "theme_id": f"THEME-{chain_id.upper()}",
            "chain_id": chain_id,
            "title": chain_id,
            "stage": "MAPPED",
        })
        self.chains.append({
            "id": chain_id,
            "title": chain_id,
            "links": [
                {"id": link_id, "name": link_id, "position": index + 1}
                for index, link_id in enumerate(links)
            ],
        })
        mapping = {
            "id": f"MAP-{chain_id}",
            "chain_id": chain_id,
            "as_of": "2026-08-30",
            "status": "ACTIVE",
            "target_issuers_per_link": 10,
            "issuers": issuers,
            "listings": listings,
            "placements": placements,
            "link_coverage": [
                {
                    "link_id": link_id,
                    "status": "OPEN",
                    "distinct_issuer_count": 0,
                }
                for link_id in links
            ],
        }
        (self.data / "mappings" / f"{chain_id}.json").write_text(
            json.dumps(mapping))

    def add_profile(self, issuer_id, placements, *, tier="O2", status="COMPLETE"):
        profile = {
            "issuer_id": issuer_id,
            "issuer_name": issuer_id,
            "status": status,
            "opportunity_tier": tier,
            "placements": placements,
        }
        (self.data / "companies" / f"{issuer_id}.json").write_text(
            json.dumps(profile))

    def project(self):
        campaign = {
            "id": "CAMP-20260830-01",
            "title": "Denominator fixture",
            "as_of": "2026-08-30",
            "status": "ACTIVE",
            "selection_basis": {"as_of": "2026-08-30"},
            "themes": self.themes,
            "targets": dict(check_campaign.LOCKED_TARGETS),
            "blockers": [],
        }
        completion = check_campaign.compute_campaign_completion(self.root, campaign)
        campaign["completion"] = completion
        (self.data / "campaigns" / f"{campaign['id']}.json").write_text(
            json.dumps(campaign))
        projection = build.build_campaign_ix(
            self.data,
            chains=self.chains,
            screens=[],
            stocks=[],
            requests=self.requests,
        )
        return completion, projection


class TestCanonicalCampaignDenominators(unittest.TestCase):
    def test_cass_probe_two_rows_one_placed_listed_issuer_reports_one_everywhere(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = DenominatorFixture(td)
            fixture.add_theme(
                "theme-a",
                issuers=[
                    {"issuer_id": "ISS-A", "name": "Issuer A"},
                    {"issuer_id": "ISS-ORPHAN", "name": "Orphan row"},
                ],
                listings=[
                    fixture.listing("ISS-A", "XNYS:AAA", "AAA", "XNYS"),
                ],
                placements=[
                    {"chain_id": "theme-a", "link_id": "L1", "issuer_id": "ISS-A"},
                ],
            )
            fixture.add_profile(
                "ISS-A", [{"chain_id": "theme-a", "link_id": "L1"}], tier="O1")
            fixture.add_profile(
                "ISS-ORPHAN", [{"chain_id": "theme-a", "link_id": "L1"}], tier="O1")
            fixture.requests["requests"] = [
                {"issuer_id": "ISS-A", "chain_id": "theme-a", "status": "PENDING"},
                {"issuer_id": "ISS-A", "chain_id": "theme-a", "status": "PENDING"},
                {"issuer_id": "ISS-ORPHAN", "chain_id": "theme-a",
                 "status": "PENDING"},
            ]
            completion, projection = fixture.project()

        self.assertEqual(completion["distinct_mapped_issuers"], 1)
        self.assertEqual(completion["per_theme"][0]["distinct_mapped_issuers"], 1)
        self.assertEqual(completion["opportunity_tiers"]["O1"], 1)
        self.assertEqual(projection["counts"]["mapped_issuers"], 1)
        self.assertEqual(projection["themes"][0]["counts"]["mapped_issuers"], 1)
        self.assertEqual(projection["themes"][0]["links"][0]["mapped"], 1)
        self.assertEqual(projection["counts"]["o1"], 1)
        self.assertEqual([row["issuer_id"] for row in projection["o1"]], ["ISS-A"])
        self.assertEqual(projection["counts"]["pending"], 1)
        self.assertEqual(projection["themes"][0]["counts"]["pending"], 1)
        self.assertEqual(projection["themes"][0]["links"][0]["pending"], 1)

    def test_same_issuer_across_themes_counts_once_campaign_wide(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = DenominatorFixture(td)
            for chain_id in ("theme-a", "theme-b"):
                fixture.add_theme(
                    chain_id,
                    issuers=[{"issuer_id": "ISS-A", "name": "Issuer A"}],
                    listings=[
                        fixture.listing("ISS-A", "XNYS:AAA", "AAA", "XNYS"),
                    ],
                    placements=[
                        {"chain_id": chain_id, "link_id": "L1",
                         "issuer_id": "ISS-A"},
                    ],
                )
            fixture.add_profile("ISS-A", [
                {"chain_id": "theme-a", "link_id": "L1"},
                {"chain_id": "theme-b", "link_id": "L1"},
            ])
            completion, projection = fixture.project()

        self.assertEqual(completion["distinct_mapped_issuers"], 1)
        self.assertEqual(completion["completed_profiles"], 1)
        self.assertEqual(
            [row["distinct_mapped_issuers"] for row in completion["per_theme"]],
            [1, 1],
        )
        self.assertEqual(projection["counts"]["mapped_issuers"], 1)
        self.assertEqual(
            [theme["counts"]["mapped_issuers"] for theme in projection["themes"]],
            [1, 1],
        )
        self.assertEqual(
            [theme["links"][0]["mapped"] for theme in projection["themes"]],
            [1, 1],
        )

    def test_same_issuer_on_multiple_links_counts_once_per_theme(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = DenominatorFixture(td)
            fixture.add_theme(
                "theme-a",
                issuers=[{"issuer_id": "ISS-A", "name": "Issuer A"}],
                listings=[
                    fixture.listing("ISS-A", "XNYS:AAA", "AAA", "XNYS"),
                ],
                placements=[
                    {"chain_id": "theme-a", "link_id": link_id,
                     "issuer_id": "ISS-A"}
                    for link_id in ("L1", "L2")
                ],
                links=("L1", "L2"),
            )
            fixture.add_profile("ISS-A", [
                {"chain_id": "theme-a", "link_id": "L1"},
                {"chain_id": "theme-a", "link_id": "L2"},
            ])
            completion, projection = fixture.project()

        self.assertEqual(completion["distinct_mapped_issuers"], 1)
        self.assertEqual(completion["per_theme"][0]["distinct_mapped_issuers"], 1)
        self.assertEqual(projection["counts"]["mapped_issuers"], 1)
        self.assertEqual(projection["themes"][0]["counts"]["mapped_issuers"], 1)
        self.assertEqual(
            [row["mapped"] for row in projection["themes"][0]["links"]],
            [1, 1],
        )

    def test_dual_listing_rejections_unresolved_rows_and_aliases_do_not_pad(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = DenominatorFixture(td)
            listings = [
                fixture.listing("ISS-A", "XNYS:AAA", "AAA", "XNYS"),
                fixture.listing("ISS-A", "XTKS:AAA", "AAA", "XTKS"),
                fixture.listing("ISS-A", "XNYS:AAA", "AAA", "XNYS"),
                fixture.listing("ISS-B", "XNAS:BBB", "BBB", "XNAS"),
                {
                    "issuer_id": "ISS-C",
                    "listing_id": "XCME:CCC",
                    "ticker": "CCC",
                    "exchange": "",
                },
            ]
            fixture.add_theme(
                "theme-a",
                issuers=[
                    {"issuer_id": "ISS-A", "name": "Issuer A"},
                    {"issuer_id": "ISS-B", "name": "Issuer B"},
                    {"issuer_id": "ISS-C", "name": "Issuer C"},
                    {"issuer_id": "ISS-A-ALIAS", "name": "Issuer A alias"},
                ],
                listings=listings,
                placements=[
                    {"chain_id": "theme-a", "link_id": "L1",
                     "issuer_id": "ISS-A"},
                    {"chain_id": "theme-a", "link_id": "L1",
                     "issuer_id": "ISS-B", "status": "REJECTED"},
                    {"chain_id": "theme-a", "link_id": "L1",
                     "issuer_id": "ISS-C"},
                    {"chain_id": "theme-a", "link_id": "L1",
                     "issuer_id": "ISS-A-ALIAS", "ticker": "AAA"},
                ],
            )
            completion, projection = fixture.project()

        self.assertEqual(len(check_campaign.validated_public_listings({
            "issuers": [
                {"issuer_id": "ISS-A", "name": "Issuer A"},
                {"issuer_id": "ISS-B", "name": "Issuer B"},
                {"issuer_id": "ISS-C", "name": "Issuer C"},
            ],
            "listings": listings,
        })), 3)
        self.assertEqual(completion["distinct_mapped_issuers"], 1)
        self.assertEqual(completion["per_theme"][0]["distinct_mapped_issuers"], 1)
        self.assertEqual(projection["counts"]["mapped_issuers"], 1)
        self.assertEqual(projection["themes"][0]["counts"]["mapped_issuers"], 1)
        self.assertEqual(projection["themes"][0]["links"][0]["mapped"], 1)


if __name__ == "__main__":
    unittest.main()
