#!/usr/bin/env python3
"""Campaign projection and dashboard pressure tests.

The campaign source stores are intentionally much larger than the browser projection.
These tests fail if a future build starts inlining mapping evidence, full company
profiles, or additional market files into the single HTML artifact.
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PROJECTION_SCALE_BUDGET = 120_000
# Read from app/build.py rather than kept as a second number here. The two had already
# drifted: build.py warned at 2.0 MB while this asserted 1.5 MB, so a page between the two
# passed a real build with no warning and failed a test with no explanation. One threshold,
# owned by the file that actually enforces it during a build. Raised past 1.5 MB on
# 2026-08-30 when the eight agent contracts (109 KB) and the occurrence log (133 KB) were
# inlined; both are payload the page now renders, and the campaign projection this test
# stresses is still capped separately by CAMPAIGN_PROJECTION_MAX_BYTES.


def _load_build():
    spec = importlib.util.spec_from_file_location("campaign_build", ROOT / "app" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build()
HTML_SCALE_BUDGET = int(build.SIZE_WARN_MB * 1_000_000)


def public_listing(issuer_id, issuer_name, listing_id, ticker, exchange):
    return {
        "listing_id": listing_id,
        "issuer_id": issuer_id,
        "ticker": ticker,
        "exchange": exchange,
        "identity_evidence": [{
            "claim": f"{issuer_name} is listed as {exchange}:{ticker}",
            "tag": "VERIFIED",
            "source_name": f"{exchange} issuer directory",
            "source_date": "2026-08-29",
            "url": "https://example.invalid/official-listing",
            "source_type": "OFFICIAL_EXCHANGE",
            "legal_issuer": issuer_name,
            "exchange": exchange,
            "ticker": ticker,
        }],
    }


def _all_keys(value):
    keys = set()
    if isinstance(value, dict):
        keys.update(value)
        for child in value.values():
            keys.update(_all_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_all_keys(child))
    return keys


class CampaignScaleFixture:
    THEMES = 10
    LINKS_PER_THEME = 12
    PLACEMENTS_PER_LINK = 10
    PROFILES_PER_THEME = 20
    O1_PER_THEME = 6
    FINALS_PER_THEME = 3
    RAW_MARKER = "RAW-EVIDENCE-MUST-NOT-REACH-HTML"

    def __init__(self, root):
        self.data = Path(root) / "data"
        for folder in ("campaigns", "mappings", "companies"):
            (self.data / folder).mkdir(parents=True)
        self.chains = []
        self.screens = []
        self.stocks = []
        self.requests = {"requests": []}
        self._write()

    def ticker(self, theme_index, company_index):
        return f"C{theme_index:02d}{company_index:02d}"

    def issuer_id(self, theme_index, company_index):
        return f"ISSUER-{theme_index:02d}-{company_index:02d}"

    def _write(self):
        themes = []
        for theme_index in range(self.THEMES):
            chain_id = f"theme-{theme_index:02d}"
            links = []
            placements = []
            link_coverage = []
            for link_index in range(self.LINKS_PER_THEME):
                link_id = f"link-{link_index:02d}"
                links.append({
                    "id": link_id,
                    "name": f"Theme {theme_index} link {link_index}",
                    "position": link_index + 1,
                    "investability": "PURE_PLAYS_EXIST",
                })
                rows = []
                for placement_index in range(self.PLACEMENTS_PER_LINK):
                    company_index = (link_index * 5 + placement_index) % self.PROFILES_PER_THEME
                    rows.append({
                        "chain_id": chain_id,
                        "link_id": link_id,
                        "issuer_id": self.issuer_id(theme_index, company_index),
                        "role": self.RAW_MARKER + ("x" * 300),
                        "status": "ACTIVE",
                        "evidence": [{
                            "claim": self.RAW_MARKER + ("y" * 500),
                            "source_name": "scale fixture",
                            "source_date": "2026-08-29",
                            "url": "https://example.invalid/raw",
                            "tag": "VERIFIED",
                        }],
                    })
                placements.extend(rows)
                link_coverage.append({
                    "link_id": link_id,
                    "status": "TARGET_MET",
                    "distinct_issuer_count": self.PLACEMENTS_PER_LINK,
                })
            self.chains.append({
                "id": chain_id,
                "title": f"Theme {theme_index}",
                "heat_as_of": "2026-08-29",
                "links": links,
            })
            issuers = [{
                "issuer_id": self.issuer_id(theme_index, company_index),
                "name": f"Company {self.ticker(theme_index, company_index)}",
            } for company_index in range(self.PROFILES_PER_THEME)]
            listings = [
                public_listing(
                    self.issuer_id(theme_index, company_index),
                    f"Company {self.ticker(theme_index, company_index)}",
                    f"NASDAQ:{self.ticker(theme_index, company_index)}",
                    self.ticker(theme_index, company_index),
                    "NASDAQ",
                )
                for company_index in range(self.PROFILES_PER_THEME)
            ]
            mapping = {
                "id": f"MAP-{chain_id}",
                "chain_id": chain_id,
                "as_of": "2026-08-29",
                "status": "COMPLETE",
                "target_issuers_per_link": self.PLACEMENTS_PER_LINK,
                "issuers": issuers,
                "listings": listings,
                "placements": placements,
                "link_coverage": link_coverage,
                "confidence_audit": {},
                "changelog": [],
            }
            (self.data / "mappings" / f"{chain_id}.json").write_text(json.dumps(mapping))
            themes.append({
                "theme_id": f"THEME-{theme_index:02d}",
                "chain_id": chain_id,
                "signal_id": f"SIG-20260829-{theme_index:02d}",
                "title": f"Theme {theme_index}",
                "stage": "MAPPED",
                "as_of": "2026-08-29",
                "blockers": ["official-source coverage remains blocked"] if theme_index == 9 else [],
            })

            for company_index in range(self.PROFILES_PER_THEME):
                ticker = self.ticker(theme_index, company_index)
                issuer_id = self.issuer_id(theme_index, company_index)
                profile_placements = []
                for link_index in range(self.LINKS_PER_THEME):
                    members = {
                        (link_index * 5 + placement_index) % self.PROFILES_PER_THEME
                        for placement_index in range(self.PLACEMENTS_PER_LINK)
                    }
                    if company_index in members:
                        profile_placements.append({
                            "chain_id": chain_id,
                            "link_id": f"link-{link_index:02d}",
                        })
                profile = {
                    "issuer_id": issuer_id,
                    "issuer_name": f"Company {ticker}",
                    "data_tier": ("T1", "T2", "T3")[company_index % 3],
                    "opportunity_tier": "O1" if company_index < self.O1_PER_THEME else "O2",
                    "opportunity_rank": (
                        theme_index * self.O1_PER_THEME + company_index + 1
                        if company_index < self.O1_PER_THEME else None
                    ),
                    "opportunity": {
                        "tier": "O1" if company_index < self.O1_PER_THEME else "O2",
                        "rank": (
                            theme_index * self.O1_PER_THEME + company_index + 1
                            if company_index < self.O1_PER_THEME else None
                        ),
                        "basis": self.RAW_MARKER + " nested opportunity basis",
                    },
                    "status": "COMPLETE",
                    "as_of": "2026-08-29",
                    "business_summary": self.RAW_MARKER + ("z" * 2000),
                    "exposure_summary": self.RAW_MARKER + " exposure",
                    "crowdedness_caveats": [self.RAW_MARKER + " crowdedness"],
                    "catalysts": [self.RAW_MARKER + " catalyst"],
                    "risks": [self.RAW_MARKER + " risk"],
                    "data_gaps": [self.RAW_MARKER + " gap"],
                    "metrics": {
                        "revenue": {
                            "value": company_index,
                            "source": self.RAW_MARKER,
                            "basis": self.RAW_MARKER + " metric basis",
                        }
                    },
                    "listing_refs": [f"NASDAQ:{ticker}"],
                    "placements": profile_placements,
                    "opportunity_basis": self.RAW_MARKER + " opportunity basis",
                    "disposition": {
                        "state": "WATCHLIST",
                        "basis": self.RAW_MARKER + " disposition basis",
                    },
                }
                (self.data / "companies" / f"{issuer_id}.json").write_text(json.dumps(profile))
                if company_index < self.O1_PER_THEME:
                    handoff_link = profile_placements[0]["link_id"]
                    if not any(screen.get("chain_id") == chain_id for screen in self.screens):
                        self.screens.append({
                            "id": chain_id,
                            "chain_id": chain_id,
                            "as_of": "2026-08-29",
                            "buckets": {"pure_play": []},
                        })
                    next(screen for screen in self.screens
                         if screen["chain_id"] == chain_id)["buckets"]["pure_play"].append({
                             "issuer_id": issuer_id,
                             "listing_id": f"NASDAQ:{ticker}",
                             "ticker": ticker,
                             "link_id": handoff_link,
                             "thesis_1line": self.RAW_MARKER + " screen thesis",
                         })
                    profile["selection_basis"] = {
                        "screen_handoff": {
                            "screen_ref": chain_id,
                            "chain_id": chain_id,
                            "link_id": handoff_link,
                            "listing_id": f"NASDAQ:{ticker}",
                        },
                        "direct_exposure": {
                            "basis": self.RAW_MARKER + " selection basis",
                        },
                    }
                    (self.data / "companies" / f"{issuer_id}.json").write_text(
                        json.dumps(profile))
                if company_index < self.FINALS_PER_THEME:
                    self.stocks.append({
                        "issuer_id": issuer_id,
                        "listing_id": f"NASDAQ:{ticker}",
                        "ticker": ticker,
                        "chain_id": chain_id,
                        "link_id": profile_placements[0]["link_id"],
                        "status": "FINAL",
                        "as_of": "2026-08-29",
                    })
            pending_ticker = self.ticker(theme_index, self.PROFILES_PER_THEME - 1)
            self.requests["requests"].append({
                "id": f"REQ-SCALE-{theme_index:02d}",
                "campaign_id": "CAMP-20260829-01",
                "chain_id": chain_id,
                "ticker": pending_ticker,
                "status": "PENDING",
            })

        campaign = {
            "id": "CAMP-20260829-01",
            "title": "Ten-theme scale fixture",
            "as_of": "2026-01-15",
            "status": "ACTIVE",
            "selection_basis": {"as_of": "2026-01-15"},
            "themes": themes,
            "targets": {
                "theme_count": self.THEMES,
                "issuers_per_link": self.PLACEMENTS_PER_LINK,
                "completed_profiles_min": self.THEMES * self.PROFILES_PER_THEME,
                "profiles_per_theme_min": 10,
                "o1_min": 30,
                "o1_max": 60,
            },
            "completion": {
                "themes_selected": self.THEMES,
                "themes_complete": 0,
                "distinct_mapped_issuers": self.THEMES * self.PROFILES_PER_THEME,
                "completed_profiles": self.THEMES * self.PROFILES_PER_THEME,
                "opportunity_tiers": {
                    "O1": self.THEMES * self.O1_PER_THEME,
                    "O2": self.THEMES * (
                        self.PROFILES_PER_THEME - self.O1_PER_THEME),
                    "O3": 0,
                },
                "o1_complete": self.THEMES * self.O1_PER_THEME,
                "o1_final": self.THEMES * self.FINALS_PER_THEME,
                "per_theme": [],
            },
            "blockers": [],
        }
        (self.data / "campaigns" / "CAMP-20260829-01.json").write_text(
            json.dumps(campaign))


class CampaignIdentityFixture:
    """Small fixture that makes placement, listing, and handoff mistakes observable."""

    def __init__(self, root):
        self.data = Path(root) / "data"
        for folder in ("campaigns", "mappings", "companies"):
            (self.data / folder).mkdir(parents=True)
        self.chains = [{
            "id": "theme-a",
            "title": "Theme A",
            "heat_as_of": "2026-08-28",
            "links": [
                {"id": "L1", "name": "First", "position": 1},
                {"id": "L2", "name": "Second", "position": 2},
            ],
        }]
        self.screens = [{
            "id": "theme-a",
            "chain_id": "theme-a",
            "as_of": "2026-08-29",
            "buckets": {"pure_play": [
                {"issuer_id": "ISS-A", "listing_id": "XTKS:ZZZ",
                 "ticker": "ZZZ", "link_id": "L2"},
                {"issuer_id": "ISS-C", "listing_id": "XNYS:CCC",
                 "ticker": "CCC", "link_id": "L2"},
            ]},
        }]
        self.stocks = [
            # The exact O1 handoff. ZZZ is deliberately not the lexical first listing.
            {"issuer_id": "ISS-A", "listing_id": "XTKS:ZZZ",
             "ticker": "ZZZ", "chain_id": "theme-a",
             "link_id": "L2", "status": "FINAL", "as_of": "2026-08-30"},
            # Same issuer and chain, but no matching screen handoff.
            {"issuer_id": "ISS-A", "listing_id": "XNYS:AAA",
             "ticker": "AAA", "chain_id": "theme-a",
             "link_id": "L1", "status": "FINAL", "as_of": "2026-08-30"},
            # O1 profile with no screen handoff.
            {"issuer_id": "ISS-B", "listing_id": "XNYS:BBB",
             "ticker": "BBB", "chain_id": "theme-a",
             "link_id": "L1", "status": "FINAL", "as_of": "2026-08-30"},
            # Exact handoff and FINAL, but the issuer is O2.
            {"issuer_id": "ISS-C", "listing_id": "XNYS:CCC",
             "ticker": "CCC", "chain_id": "theme-a",
             "link_id": "L2", "status": "FINAL", "as_of": "2026-08-30"},
        ]
        self.requests = {"requests": [
            {"id": "REQ-1", "issuer_id": "ISS-D", "ticker": "DDD",
             "chain_id": "theme-a", "status": "PENDING"},
            {"id": "REQ-2", "issuer_id": "ISS-D", "ticker": "DDD",
             "chain_id": "theme-a", "status": "PENDING"},
        ]}
        self._write()

    def _write(self):
        placements = [
            {"chain_id": "theme-a", "link_id": "L1", "issuer_id": "ISS-A",
             "status": "ACTIVE"},
            {"chain_id": "theme-a", "link_id": "L2", "issuer_id": "ISS-A",
             "status": "ACTIVE"},
            {"chain_id": "theme-a", "link_id": "L1", "issuer_id": "ISS-B",
             "status": "ACTIVE"},
            {"chain_id": "theme-a", "link_id": "L2", "issuer_id": "ISS-C",
             "status": "ACTIVE"},
            {"chain_id": "theme-a", "link_id": "L1", "issuer_id": "ISS-D",
             "status": "ACTIVE"},
        ]
        mapping = {
            "id": "MAP-theme-a",
            "chain_id": "theme-a",
            "as_of": "2026-08-29",
            "status": "COMPLETE",
            "target_issuers_per_link": 10,
            "issuers": [
                {"issuer_id": issuer_id, "name": f"Issuer {issuer_id[-1]}"}
                for issuer_id in ("ISS-A", "ISS-B", "ISS-C", "ISS-D")
            ],
            "listings": [
                public_listing("ISS-A", "Issuer A", "XNYS:AAA", "AAA", "XNYS"),
                public_listing("ISS-A", "Issuer A", "XTKS:ZZZ", "ZZZ", "XTKS"),
                public_listing("ISS-B", "Issuer B", "XNYS:BBB", "BBB", "XNYS"),
                public_listing("ISS-C", "Issuer C", "XNYS:CCC", "CCC", "XNYS"),
                public_listing("ISS-D", "Issuer D", "XNYS:DDD", "DDD", "XNYS"),
            ],
            "placements": placements,
            "link_coverage": [
                {"link_id": "L1", "status": "OPEN", "distinct_issuer_count": 3},
                {"link_id": "L2", "status": "TARGET_MET", "distinct_issuer_count": 2},
            ],
        }
        (self.data / "mappings" / "theme-a.json").write_text(json.dumps(mapping))

        profiles = [
            {
                "issuer_id": "ISS-A", "issuer_name": "Issuer A",
                "status": "COMPLETE", "data_tier": "T1",
                "opportunity_tier": "O1", "opportunity_rank": 1,
                # Mapping has A on both links; the profile resolves only L2.
                "placements": [{"chain_id": "theme-a", "link_id": "L2"}],
                "listing_refs": ["XNYS:AAA", "XTKS:ZZZ"],
                "selection_basis": {"screen_handoff": {
                    "screen_ref": "theme-a", "chain_id": "theme-a",
                    "link_id": "L2", "listing_id": "XTKS:ZZZ",
                }},
                "as_of": "2026-08-27",
            },
            {
                "issuer_id": "ISS-B", "issuer_name": "Issuer B",
                "status": "COMPLETE", "data_tier": "T2",
                "opportunity_tier": "O1", "opportunity_rank": 2,
                "placements": [{"chain_id": "theme-a", "link_id": "L1"}],
                "listing_refs": ["XNYS:BBB"],
                "as_of": "2026-08-27",
            },
            {
                "issuer_id": "ISS-C", "issuer_name": "Issuer C",
                "status": "COMPLETE", "data_tier": "T2",
                "opportunity_tier": "O2",
                "placements": [{"chain_id": "theme-a", "link_id": "L2"}],
                "listing_refs": ["XNYS:CCC"],
                "as_of": "2026-08-27",
            },
            {
                "issuer_id": "ISS-D", "issuer_name": "Issuer D",
                "status": "BLOCKED", "data_tier": "T3",
                "opportunity_tier": "O3",
                "placements": [{"chain_id": "theme-a", "link_id": "L1"}],
                "listing_refs": ["XNYS:DDD"],
                "as_of": "2026-08-27",
            },
        ]
        for profile in profiles:
            (self.data / "companies" / f"{profile['issuer_id']}.json").write_text(
                json.dumps(profile))

        campaign = {
            "id": "CAMP-20260829-01",
            "title": "Identity fixture",
            "as_of": "2026-01-15",
            "selection_basis": {"as_of": "2026-01-15"},
            "status": "ACTIVE",
            "themes": [{
                "theme_id": "THEME-A", "chain_id": "theme-a",
                "title": "Theme A", "stage": "MAPPED", "as_of": "2026-01-15",
            }],
            "targets": {
                "theme_count": 10, "issuers_per_link": 10,
                "completed_profiles_min": 200, "profiles_per_theme_min": 10,
                "o1_min": 30, "o1_max": 60,
            },
            "completion": {
                "themes_selected": 1, "themes_complete": 0,
                "distinct_mapped_issuers": 4, "completed_profiles": 3,
                "opportunity_tiers": {"O1": 2, "O2": 1, "O3": 1},
                "o1_complete": 2,
                "o1_final": 1,
                "per_theme": [],
            },
            "blockers": [],
        }
        (self.data / "campaigns" / "CAMP-20260829-01.json").write_text(
            json.dumps(campaign))


class TestCampaignProjection(unittest.TestCase):
    def test_missing_optional_stores_is_truthful(self):
        with tempfile.TemporaryDirectory() as td:
            projection = build.build_campaign_ix(Path(td) / "data")
        self.assertFalse(projection["present"])
        self.assertEqual(projection["campaign_count"], 0)
        self.assertEqual(projection["themes"], [])
        self.assertEqual(projection["o1"], [])
        self.assertNotIn("counts", projection,
                         "without a campaign manifest there are no honest denominators")

    def test_missing_mapping_is_unknown_not_zero_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            (data / "campaigns").mkdir(parents=True)
            campaign = {
                "id": "CAMP-20260829-01",
                "title": "Missing mapping fixture",
                "as_of": "2026-01-15",
                "selection_basis": {"as_of": "2026-01-15"},
                "status": "ACTIVE",
                "themes": [{
                    "theme_id": "THEME-A", "chain_id": "theme-a",
                    "title": "Theme A", "stage": "CHAINED",
                    "as_of": "2026-01-15",
                }],
                "targets": {
                    "theme_count": 10, "issuers_per_link": 10,
                    "completed_profiles_min": 200,
                    "profiles_per_theme_min": 10,
                    "o1_min": 30, "o1_max": 60,
                },
                "completion": {
                    "themes_selected": 1, "distinct_mapped_issuers": 0,
                    "completed_profiles": 0,
                    "opportunity_tiers": {"O1": 0, "O2": 0, "O3": 0},
                },
                "blockers": [],
            }
            (data / "campaigns" / campaign["id"]).with_suffix(".json").write_text(
                json.dumps(campaign))
            projection = build.build_campaign_ix(data, chains=[{
                "id": "theme-a", "heat_as_of": "2026-08-29",
                "links": [{"id": "L1", "position": 1}],
            }])

        theme = projection["themes"][0]
        link = theme["links"][0]
        self.assertFalse(theme["mapping_present"])
        self.assertIsNone(theme["counts"]["mapped_issuers"])
        self.assertIsNone(link["mapped"])
        self.assertIsNone(link["target"])
        self.assertIsNone(link["status"])
        self.assertEqual(theme["counts"]["blockers"], 0)
        self.assertNotIn("NOT_STARTED", json.dumps(projection))
        self.assertEqual(theme["selection_as_of"], "2026-01-15")
        self.assertEqual(theme["coverage_as_of"], "2026-08-29")
        self.assertEqual(projection["selection_as_of"], "2026-01-15")
        self.assertEqual(projection["coverage_as_of"], "2026-08-29")

    def _identity_projection(self, *, with_completion=True):
        with tempfile.TemporaryDirectory() as td:
            fixture = CampaignIdentityFixture(td)
            if not with_completion:
                path = fixture.data / "campaigns" / "CAMP-20260829-01.json"
                campaign = json.loads(path.read_text())
                campaign.pop("completion")
                path.write_text(json.dumps(campaign))
            return build.build_campaign_ix(
                fixture.data, chains=fixture.chains, screens=fixture.screens,
                stocks=fixture.stocks, requests=fixture.requests)

    def test_multi_link_profiles_and_finals_keep_exact_placements(self):
        projection = self._identity_projection()
        theme = projection["themes"][0]
        links = {row["id"]: row for row in theme["links"]}

        self.assertEqual(theme["counts"]["profiles"], 3)
        self.assertEqual(links["L1"]["profiled"], 1,
                         "ISS-A must not profile L1 when its profile only resolves L2")
        self.assertEqual(links["L2"]["profiled"], 2)
        self.assertEqual(links["L1"]["finals"], 0,
                         "a chain-level final must not repeat across every issuer link")
        self.assertEqual(links["L2"]["finals"], 1)

    def test_dual_listing_final_uses_exact_screen_handoff_stock(self):
        projection = self._identity_projection()
        queue = {row["issuer_id"]: row for row in projection["o1"]}

        self.assertEqual(queue["ISS-A"]["stock_ticker"], "ZZZ")
        self.assertEqual(queue["ISS-A"]["listing_id"], "XTKS:ZZZ")
        self.assertEqual(queue["ISS-A"]["link_id"], "L2")
        self.assertTrue(queue["ISS-A"]["final"])

    def test_o2_and_unhanded_finals_do_not_count(self):
        projection = self._identity_projection(with_completion=False)
        queue = {row["issuer_id"]: row for row in projection["o1"]}

        self.assertEqual(projection["counts"]["o1"], 2)
        self.assertEqual(projection["counts"]["finals"], 1)
        self.assertIsNone(queue["ISS-B"]["stock_ticker"])
        self.assertFalse(queue["ISS-B"]["handoff_present"])
        self.assertFalse(queue["ISS-B"]["final"])
        self.assertNotIn("ISS-C", queue, "an O2 FINAL is not an O1 handoff")

    def test_pending_and_freshness_use_issuer_and_coverage_units(self):
        projection = self._identity_projection()

        self.assertEqual(projection["counts"]["mapped_issuers"], 4)
        self.assertEqual(projection["counts"]["profiles"], 3)
        self.assertEqual(projection["counts"]["pending"], 1)
        self.assertEqual(projection["selection_as_of"], "2026-01-15")
        self.assertEqual(projection["coverage_as_of"], "2026-08-30")

        theme = projection["themes"][0]
        self.assertEqual(theme["counts"]["o1"], 1)
        self.assertEqual(theme["counts"]["finals"], 1)
        self.assertEqual(theme["counts"]["pending"], 1)
        self.assertEqual(theme["counts"]["blockers"], 1,
                         "only the explicitly OPEN L1 coverage creates a deficit")

    def test_campaign_totals_recompute_mapped_issuer_denominator(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = CampaignIdentityFixture(td)
            path = fixture.data / "campaigns" / "CAMP-20260829-01.json"
            campaign = json.loads(path.read_text())
            campaign["completion"].update({
                "themes_selected": 9,
                "distinct_mapped_issuers": 37,
                "completed_profiles": 23,
                "opportunity_tiers": {"O1": 11, "O2": 12, "O3": 14},
            })
            path.write_text(json.dumps(campaign))
            projection = build.build_campaign_ix(
                fixture.data, chains=fixture.chains, screens=fixture.screens,
                stocks=fixture.stocks, requests=fixture.requests)

        self.assertEqual(projection["counts"]["themes"], 9)
        self.assertEqual(
            projection["counts"]["mapped_issuers"], 4,
            "a stale completion count must not override canonical mapped issuers",
        )
        self.assertEqual(projection["counts"]["profiles"], 23)
        self.assertEqual(projection["counts"]["o1"], 11)
        self.assertEqual(projection["counts"]["finals"], 1)

    def test_target_scale_is_compact_deterministic_and_complete(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = CampaignScaleFixture(td)
            first = build.build_campaign_ix(
                fixture.data,
                chains=fixture.chains,
                screens=fixture.screens,
                stocks=fixture.stocks,
                requests=fixture.requests,
            )
            second = build.build_campaign_ix(
                fixture.data,
                chains=list(reversed(fixture.chains)),
                screens=list(reversed(fixture.screens)),
                stocks=list(reversed(fixture.stocks)),
                requests={"requests": list(reversed(fixture.requests["requests"]))},
            )

        first_blob = json.dumps(first, separators=(",", ":"), sort_keys=True)
        second_blob = json.dumps(second, separators=(",", ":"), sort_keys=True)
        self.assertEqual(first_blob, second_blob)
        self.assertEqual(first["counts"]["themes"], 10)
        self.assertEqual(first["counts"]["links"], 120)
        self.assertEqual(first["counts"]["mapped_issuers"], 200)
        self.assertEqual(first["counts"]["profiles"], 200)
        self.assertEqual(first["counts"]["o1"], 60)
        self.assertEqual(first["counts"]["finals"], 30)
        self.assertEqual(first["counts"]["pending"], 10)
        self.assertEqual(len(first["o1"]), 60)
        self.assertEqual(len(first["themes"][0]["links"]), 12)
        self.assertLess(len(first_blob.encode()), PROJECTION_SCALE_BUDGET)
        self.assertLess(len(first_blob.encode()), build.CAMPAIGN_PROJECTION_MAX_BYTES)

    def test_raw_mapping_and_company_payloads_never_project(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = CampaignScaleFixture(td)
            projection = build.build_campaign_ix(
                fixture.data, chains=fixture.chains, screens=fixture.screens,
                stocks=fixture.stocks,
                requests=fixture.requests)
        blob = json.dumps(projection, separators=(",", ":"))
        self.assertNotIn(fixture.RAW_MARKER, blob)
        self.assertTrue({
            "evidence", "role", "searches", "business_summary", "metrics",
            "listing_refs", "exposure_summary", "crowdedness_caveats",
            "catalysts", "risks", "data_gaps", "disposition",
            "opportunity", "opportunity_basis", "selection_basis",
            "screen_handoff", "basis",
        }.isdisjoint(_all_keys(projection)))

    def test_scale_stays_inside_single_html_budget(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = CampaignScaleFixture(td)
            projection = build.build_campaign_ix(
                fixture.data, chains=fixture.chains, screens=fixture.screens,
                stocks=fixture.stocks,
                requests=fixture.requests)
        current, _ = build.extract_committed_payload()
        payload = dict(current or {})
        payload["campaign_ix"] = projection
        html = build.assemble_html(payload)
        self.assertIn(build.BLOB_MARKER, html)
        self.assertNotIn(fixture.RAW_MARKER, html)
        self.assertLess(len(html.encode()), HTML_SCALE_BUDGET,
                        f"page is {len(html.encode()):,} bytes against build.py's own "
                        f"{HTML_SCALE_BUDGET:,}-byte warn threshold")
        # The thing this test is actually about: a campaign at full scale must not be
        # what blows the page up, whatever else the payload is carrying.
        self.assertLess(len(json.dumps(projection, separators=(",", ":")).encode()),
                        build.CAMPAIGN_PROJECTION_MAX_BYTES)


class TestCampaignRendererContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "app" / "templates" / "app.js").read_text()
        cls.css = (ROOT / "app" / "templates" / "app.css").read_text()

    def test_campaign_has_nav_route_and_truthful_empty_state(self):
        self.assertIn('na("#/campaign", "Campaign", "campaign")', self.js)
        self.assertIn('p[0] === "campaign"', self.js)
        self.assertIn("No campaign data exists yet.", self.js)
        self.assertIn("Nothing has been inferred.", self.js)
        self.assertIn("mapping unavailable", self.js)
        self.assertIn("theme.mapping_present", self.js)
        self.assertIn("staleChip(theme.coverage_as_of)", self.js)
        self.assertIn("staleChip(ix.coverage_as_of)", self.js)
        self.assertIn("campaignSelectionStamp(theme.selection_as_of)", self.js)
        self.assertIn("campaignSelectionStamp(ix.selection_as_of)", self.js)
        self.assertNotIn("staleChip(theme.selection_as_of)", self.js)

    def test_data_and_opportunity_tiers_use_different_renderers(self):
        self.assertIn("function opportunityChip(", self.js)
        self.assertIn("tierChip(row.data_tier)", self.js)
        self.assertIn("opportunityChip(row.opportunity_tier)", self.js)
        self.assertIn("row.stock_ticker", self.js)
        self.assertIn(".chip.tier", self.css)
        self.assertIn(".chip.opportunity", self.css)
        self.assertIn(".chip.opp-O1", self.css)

    def test_dashboard_exposes_required_campaign_surfaces(self):
        for label in ("Theme coverage", "Per-link coverage", "complete profiles",
                      "O1 queue", "O1 FINAL", "pending data", "Blockers"):
            self.assertIn(label, self.js)
        self.assertIn("t.profiles_per_theme", self.js)


if __name__ == "__main__":
    unittest.main()
