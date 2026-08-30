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
HTML_SCALE_BUDGET = 1_500_000


def _load_build():
    spec = importlib.util.spec_from_file_location("campaign_build", ROOT / "app" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build()


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
            listings = [{
                "listing_id": f"NASDAQ:{self.ticker(theme_index, company_index)}",
                "issuer_id": self.issuer_id(theme_index, company_index),
                "ticker": self.ticker(theme_index, company_index),
                "exchange": "NASDAQ",
            } for company_index in range(self.PROFILES_PER_THEME)]
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
                    "status": "COMPLETE",
                    "as_of": "2026-08-29",
                    "business_summary": self.RAW_MARKER + ("z" * 2000),
                    "metrics": {
                        "revenue": {"value": company_index, "source": self.RAW_MARKER}
                    },
                    "listing_refs": [f"NASDAQ:{ticker}"],
                    "placements": profile_placements,
                    "disposition": {
                        "state": "WATCHLIST",
                        "basis": "direct capture and expectations gap",
                    },
                }
                (self.data / "companies" / f"{issuer_id}.json").write_text(json.dumps(profile))
                if company_index < self.FINALS_PER_THEME:
                    self.stocks.append({
                        "ticker": ticker,
                        "chain_id": chain_id,
                        "status": "FINAL",
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
            "as_of": "2026-08-29",
            "status": "ACTIVE",
            "themes": themes,
            "targets": {
                "theme_count": self.THEMES,
                "issuers_per_link": self.PLACEMENTS_PER_LINK,
                "completed_profiles_min": self.THEMES * self.PROFILES_PER_THEME,
                "profiles_per_theme_min": 10,
                "o1_min": 30,
                "o1_max": 60,
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

    def test_target_scale_is_compact_deterministic_and_complete(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = CampaignScaleFixture(td)
            first = build.build_campaign_ix(
                fixture.data,
                chains=fixture.chains,
                stocks=fixture.stocks,
                requests=fixture.requests,
            )
            second = build.build_campaign_ix(
                fixture.data,
                chains=list(reversed(fixture.chains)),
                stocks=list(reversed(fixture.stocks)),
                requests={"requests": list(reversed(fixture.requests["requests"]))},
            )

        first_blob = json.dumps(first, separators=(",", ":"), sort_keys=True)
        second_blob = json.dumps(second, separators=(",", ":"), sort_keys=True)
        self.assertEqual(first_blob, second_blob)
        self.assertEqual(first["counts"]["themes"], 10)
        self.assertEqual(first["counts"]["links"], 120)
        self.assertEqual(first["counts"]["placements"], 1200)
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
                fixture.data, chains=fixture.chains, stocks=fixture.stocks,
                requests=fixture.requests)
        blob = json.dumps(projection, separators=(",", ":"))
        self.assertNotIn(fixture.RAW_MARKER, blob)
        self.assertTrue({
            "evidence", "role", "searches", "business_summary", "metrics",
            "listing_refs",
        }.isdisjoint(_all_keys(projection)))

    def test_scale_stays_inside_single_html_budget(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = CampaignScaleFixture(td)
            projection = build.build_campaign_ix(
                fixture.data, chains=fixture.chains, stocks=fixture.stocks,
                requests=fixture.requests)
        current, _ = build.extract_committed_payload()
        payload = dict(current or {})
        payload["campaign_ix"] = projection
        html = build.assemble_html(payload)
        self.assertIn(build.BLOB_MARKER, html)
        self.assertNotIn(fixture.RAW_MARKER, html)
        self.assertLess(len(html.encode()), HTML_SCALE_BUDGET)


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

    def test_data_and_opportunity_tiers_use_different_renderers(self):
        self.assertIn("function opportunityChip(", self.js)
        self.assertIn("tierChip(row.data_tier)", self.js)
        self.assertIn("opportunityChip(row.opportunity_tier)", self.js)
        self.assertIn(".chip.tier", self.css)
        self.assertIn(".chip.opportunity", self.css)
        self.assertIn(".chip.opp-O1", self.css)

    def test_dashboard_exposes_required_campaign_surfaces(self):
        for label in ("Theme coverage", "Per-link coverage", "complete profiles",
                      "O1 queue", "FINAL verdicts", "pending data", "Blockers"):
            self.assertIn(label, self.js)


if __name__ == "__main__":
    unittest.main()
