#!/usr/bin/env python3
"""app/build.py's companies, placements, fundamentals-headline and pipeline projections.

Every object in the Upstream app must be reachable — use case to value chain to stocks to
pipelines, no dead ends (2026-09-13). data/companies (193 profiles, 3.2 MB raw) and
data/mappings (12 files, 1.7 MB raw) never reached the page before that date, and neither
did a fundamentals headline or a company pipeline. These are deliberately COMPACT
projections, unlike the "carry every store whole" rule the rest of app/build.py follows
since 2026-09-03 — the room stays there for chains, stocks and impact by trimming hard
here, so every test in this file is really checking one thing twice: the field is present,
and the cut beside it is truthful (a count, never silent).
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _load_build():
    spec = importlib.util.spec_from_file_location("company_pipeline_build", ROOT / "app" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build()


def _company(issuer_id, **overrides):
    doc = {
        "issuer_id": issuer_id,
        "issuer_name": f"{issuer_id} Inc.",
        "as_of": "2026-09-10",
        "status": "COMPLETE",
        "data_tier": "T1",
        "opportunity_tier": "O2",
        "business_summary": "  A   summary   with   extra   whitespace.  ",
        "exposure_summary": {
            "narrative": "Single-link exposure.",
            "disclosed_revenue_exposure": {"pct": None, "tag": "NULL",
                                           "basis": "  Nothing disclosed.  "},
        },
        "listings": [{"listing_id": "NASDAQ-XYZ", "ticker": "XYZ", "exchange": "NASDAQ",
                      "market_ticker": "XYZ"}],
        "placements": [],
        "metrics": {
            "revenue": {"latest_fy": {"value": 100.0, "tag": "VERIFIED",
                                      "source_name": "SEC EDGAR", "source_date": "2026-08-01",
                                      "url": "https://example.invalid/facts",
                                      "basis": "x" * 900}},
            "valuation": {"market_cap": {"value": None, "tag": "NULL",
                                        "basis": "y" * 500}},
        },
        "catalysts": [{"claim": f"catalyst {i}  with  space", "source_date": "2026-09-0" + str(i % 9 + 1),
                       "url": "https://example.invalid/c" + str(i)} for i in range(7)],
        "risks": [{"claim": f"risk {i}", "source_date": "2026-09-01",
                   "url": "https://example.invalid/r" + str(i)} for i in range(6)],
        "data_gaps": ["  A gap with   extra space.  "],
        "notes": [{"by": "ron", "ts": "2026-09-01T00:00:00Z", "text": "should not reach the page"}],
        "changelog": [{"ts": "2026-09-01T00:00:00Z", "by": "ron", "change": "should not reach the page"}],
        "confidence_audit": {"verified": 1},
    }
    doc.update(overrides)
    return doc


class TestProjectCompanies(unittest.TestCase):
    def test_catalysts_and_risks_capped_with_a_truthful_total(self):
        rows = build.project_companies([_company("XYZ")], market={})
        row = rows[0]
        self.assertEqual(build.COMPANY_MAX_CATALYSTS, 3)  # the engineering brief's number
        self.assertEqual(build.COMPANY_MAX_RISKS, 3)
        self.assertEqual(len(row["catalysts"]), build.COMPANY_MAX_CATALYSTS)
        self.assertEqual(row["catalysts_total"], 7)
        self.assertEqual(len(row["risks"]), build.COMPANY_MAX_RISKS)
        self.assertEqual(row["risks_total"], 6)
        # Trimmed to short keys (2026-09-14: c/d/u, the same move metrics' v/t makes).
        self.assertEqual(set(row["catalysts"][0]), {"c", "d", "u"})
        self.assertNotIn("  ", row["catalysts"][0]["c"])  # whitespace collapsed

    def test_claim_text_cut_to_200_characters_with_the_full_length_never_shown(self):
        long_claim = "x" * 250
        doc = _company("XYZ", catalysts=[{"claim": long_claim, "source_date": "2026-09-01",
                                          "url": "https://example.invalid/c0"}])
        row = build.project_companies([doc], market={})[0]
        self.assertEqual(build.COMPANY_CLAIM_CHAR_LIMIT, 200)
        self.assertEqual(len(row["catalysts"][0]["c"]), 200)
        self.assertEqual(row["catalysts"][0]["c"], long_claim[:200])

    def test_prose_fields_whitespace_collapsed_and_short_ones_untouched(self):
        row = build.project_companies([_company("XYZ")], market={})[0]
        self.assertEqual(row["business_summary"], "A summary with extra whitespace.")
        self.assertEqual(row["exposure_summary"]["narrative"], "Single-link exposure.")
        self.assertEqual(row["exposure_summary"]["disclosed_revenue_exposure"]["basis"],
                         "Nothing disclosed.")

    def test_business_and_exposure_summary_cut_to_the_company_summary_limit(self):
        # 2026-09-14: business_summary and exposure_summary's narrative/basis are the two
        # proseiest fields left in the companies projection once catalysts/risks/metrics
        # are trimmed, so they take the rest of the cut needed to hold 328 real profiles
        # under 0.7 MB. Cut, never dropped: a reader who wants the whole sentence follows
        # the "Full evidence..." link to data/companies/<issuer_id>.json the company page
        # already prints.
        long_text = "A" * 300
        doc = _company("XYZ", business_summary=long_text,
                       exposure_summary={"narrative": long_text,
                                         "disclosed_revenue_exposure": {"pct": None, "tag": "NULL",
                                                                        "basis": long_text}})
        row = build.project_companies([doc], market={})[0]
        limit = build.COMPANY_SUMMARY_CHAR_LIMIT
        self.assertEqual(row["business_summary"], long_text[:limit])
        self.assertEqual(row["exposure_summary"]["narrative"], long_text[:limit])
        self.assertEqual(row["exposure_summary"]["disclosed_revenue_exposure"]["basis"],
                         long_text[:limit])

    def test_data_gaps_keeps_only_its_first_item_with_a_truthful_total(self):
        # 2026-09-14: at 328 real profiles even one 200-character item per company was
        # 130+ KB this store could not spare and stay inside 0.7 MB (see the engineering
        # brief this date's ledger line cites), so only the first gap (profile order, not
        # re-ranked) survives; the rest stays on data/companies/<issuer_id>.json, which
        # the company page names and counts via data_gaps_total.
        row = build.project_companies([_company("XYZ")], market={})[0]
        self.assertEqual(row["data_gaps"], ["A gap with extra space."])
        self.assertEqual(row["data_gaps_total"], 1)

    def test_data_gaps_total_counts_only_real_strings(self):
        doc = _company("XYZ", data_gaps=["a real gap", "", None, 42, "  ", "another gap"])
        row = build.project_companies([doc], market={})[0]
        self.assertEqual(row["data_gaps"], ["a real gap"])
        self.assertEqual(row["data_gaps_total"], 2)

    def test_data_gaps_first_item_also_cut_to_the_summary_limit(self):
        long_gap = "B" * 300
        doc = _company("XYZ", data_gaps=[long_gap, "a second gap"])
        row = build.project_companies([doc], market={})[0]
        limit = build.COMPANY_SUMMARY_CHAR_LIMIT
        self.assertEqual(row["data_gaps"], [long_gap[:limit]])
        self.assertEqual(row["data_gaps_total"], 2)

    def test_evidence_confidence_audit_and_changelog_never_carried(self):
        row = build.project_companies([_company("XYZ")], market={})[0]
        for field in ("notes", "changelog", "confidence_audit", "selection_review", "sources"):
            self.assertNotIn(field, row)

    def test_metrics_trimmed_to_value_and_tag_only(self):
        # 2026-09-14: source_name/source_date/url/official_source dropped too (not just
        # `basis`) — at 328 real profiles v/t/d/s/u/o alone was 738 KB, more than the
        # companies projection's entire 0.7 MB budget. The full citation trail for every
        # metric stays on data/companies/<issuer_id>.json, which the company page names.
        row = build.project_companies([_company("XYZ")], market={})[0]
        leaf = row["metrics"]["revenue"]["latest_fy"]
        self.assertEqual(leaf, {"v": 100.0, "t": "VERIFIED"})
        self.assertNotIn("basis", leaf)
        self.assertNotIn("value", leaf)  # the long key never rides beside the short one
        # A NULL leaf keeps its shape — value None, tag NULL — never dropped or fabricated.
        null_leaf = row["metrics"]["valuation"]["market_cap"]
        self.assertIsNone(null_leaf["v"])
        self.assertEqual(null_leaf["t"], "NULL")

    def test_listings_resolved_to_market_files(self):
        market = {"XYZ": {"series": {}}}
        row = build.project_companies([_company("XYZ")], market=market)[0]
        self.assertEqual(row["listings"][0]["market_file"], "XYZ")
        # listing_id/market_ticker (2026-09-14): inputs to the resolver above, confirmed
        # by search of app.js to be read by no template, so they do not ride into the
        # output alongside the fields companyListingRow() actually uses.
        self.assertEqual(set(row["listings"][0]), {"ticker", "exchange", "market_file"})

    def test_listing_with_no_fetch_resolves_to_none_not_a_guess(self):
        row = build.project_companies([_company("XYZ")], market={})[0]
        self.assertIsNone(row["listings"][0]["market_file"])

    def test_opportunity_tier_reads_both_shapes(self):
        as_dict = build.project_companies(
            [_company("A", opportunity={"tier": "O1"})], market={})[0]
        self.assertEqual(as_dict["opportunity_tier"], "O1")
        as_string = build.project_companies(
            [_company("B", opportunity_tier="O3")], market={})[0]
        self.assertEqual(as_string["opportunity_tier"], "O3")

    def test_malformed_rows_are_skipped_not_fatal(self):
        rows = build.project_companies([_company("OK"), {"no_issuer_id": True}, "garbage"],
                                       market={})
        self.assertEqual([r["issuer_id"] for r in rows], ["OK"])


def _mapping(chain_id, issuers, listings, placements, **overrides):
    doc = {"id": chain_id, "chain_id": chain_id, "status": "ACTIVE",
           "issuers": issuers, "listings": listings, "placements": placements}
    doc.update(overrides)
    return doc


class TestProjectPlacements(unittest.TestCase):
    def _basic_mapping(self, **placement_overrides):
        issuers = [{"issuer_id": "ACME", "name": "Acme Corp"}]
        listings = [{"listing_id": "NASDAQ-ACM", "issuer_id": "ACME", "ticker": "ACM",
                     "exchange": "NASDAQ", "market_ticker": "ACM"}]
        placement = {"chain_id": "test-chain", "link_id": "link-a", "issuer_id": "ACME",
                     "role": "  Makes   the   thing.  ", "status": "ACTIVE",
                     "evidence": [{"claim": "x", "source_name": "y", "source_date": "2026-01-01",
                                  "url": "https://example.invalid", "tag": "VERIFIED"}]}
        placement.update(placement_overrides)
        return self._doc(issuers, listings, [placement])

    def _doc(self, issuers, listings, placements, **overrides):
        return _mapping("test-chain", issuers, listings, placements, **overrides)

    def test_every_field_the_engineering_brief_names(self):
        rows = build.project_placements([self._basic_mapping()], market={"ACM": {}})
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["chain_id"], "test-chain")
        self.assertEqual(row["link_id"], "link-a")
        self.assertEqual(row["issuer_id"], "ACME")
        self.assertEqual(row["role"], "Makes the thing.")  # whitespace collapsed
        self.assertEqual(row["status"], "ACTIVE")
        self.assertEqual(row["market_file"], "ACM")
        self.assertIn(row["placement_audit_status"], ("PASS", "FAIL", "NONE"))
        # Placement evidence rows themselves never reach the page.
        self.assertNotIn("evidence", row)
        # mapping_status (the mapping's own status, distinct from this placement's own
        # `status` above) dropped 2026-09-14: confirmed by search of app.js that no
        # template reads it, so it was pure page weight (457 placements x its own key).
        self.assertNotIn("mapping_status", row)

    def test_unfetched_placement_market_file_is_none_never_guessed(self):
        row = build.project_placements([self._basic_mapping()], market={})[0]
        self.assertIsNone(row["market_file"])

    def test_bare_local_code_without_market_ticker_still_resolves(self):
        issuers = [{"issuer_id": "TSMC", "name": "Taiwan Semiconductor"}]
        listings = [{"listing_id": "TWSE-2330", "issuer_id": "TSMC", "ticker": "2330",
                     "exchange": "TWSE"}]  # no market_ticker, exactly the 2026-09-13 gap
        placement = {"chain_id": "ai-chain", "link_id": "fab", "issuer_id": "TSMC",
                     "status": "ACTIVE"}
        doc = _mapping("ai-chain", issuers, listings, [placement])
        row = build.project_placements([doc], market={"2330-TW": {}})[0]
        self.assertEqual(row["market_file"], "2330-TW")

    def test_placement_audit_status_pass_from_whole_census(self):
        doc = self._basic_mapping()
        doc["status"] = "COMPLETE"
        doc["audit"] = {"status": "PASS"}
        row = build.project_placements([doc], market={})[0]
        self.assertEqual(row["placement_audit_status"], "PASS")

    def test_placement_audit_status_fail_with_no_override(self):
        doc = self._basic_mapping()
        doc["audit"] = {"status": "FAIL"}
        row = build.project_placements([doc], market={})[0]
        self.assertEqual(row["placement_audit_status"], "FAIL")

    def test_placement_audit_status_none_when_unaudited(self):
        doc = self._basic_mapping()
        row = build.project_placements([doc], market={})[0]
        self.assertEqual(row["placement_audit_status"], "NONE")

    def test_placements_without_a_valid_listing_still_project(self):
        # An issuer named on a placement but with no VALIDATED public listing (the map
        # gate's own bar — validated_public_listings requires ticker+exchange+identity):
        # the placement still projects, with ticker and market_file honestly null. This is
        # what makes an un-profiled, unlisted issuer's role on the link visible at all.
        issuers = [{"issuer_id": "GHOST", "name": "Ghost Co"}]
        placement = {"chain_id": "test-chain", "link_id": "link-a", "issuer_id": "GHOST",
                     "status": "ACTIVE"}
        doc = _mapping("test-chain", issuers, [], [placement])
        row = build.project_placements([doc], market={})[0]
        self.assertEqual(row["issuer_id"], "GHOST")
        self.assertIsNone(row["ticker"])
        self.assertIsNone(row["market_file"])

    def test_malformed_placement_rows_skipped_not_fatal(self):
        doc = self._basic_mapping()
        doc["placements"].append({"chain_id": "test-chain"})  # no link_id/issuer_id
        doc["placements"].append("garbage")
        rows = build.project_placements([doc], market={"ACM": {}})
        self.assertEqual(len(rows), 1)


class TestFundamentalsHeadline(unittest.TestCase):
    def test_named_fields_take_the_latest_row(self):
        f = {"source": "sec-companyfacts", "as_of": "2026-09-01",
             "revenue_fy": [["2024-12-31", 100], ["2025-12-31", 120]],
             "cash": [["2025-06-30", 5]], "total_debt": [["2025-06-30", 2]]}
        headline = build.fundamentals_headline(f)
        self.assertEqual(headline["fields"]["revenue_fy"], {"date": "2025-12-31", "value": 120})
        self.assertEqual(headline["fields"]["cash"], {"date": "2025-06-30", "value": 5})
        self.assertEqual(headline["official_source"], True)

    def test_vendor_source_flagged_not_official(self):
        f = {"source": "yfinance-statements", "as_of": "2026-09-01",
             "revenue_fy": [["2025-12-31", 50]]}
        self.assertEqual(build.fundamentals_headline(f)["official_source"], False)

    def test_unnamed_rpo_field_picked_up_generically(self):
        # The field the engineering brief says "another agent is adding to fundamentals
        # now" — picked up by name pattern, not hardcoded, so it needs no edit here.
        f = {"source": "sec-companyfacts", "as_of": "2026-09-01",
             "remaining_performance_obligation_fy": [["2025-12-31", 77]]}
        headline = build.fundamentals_headline(f)
        self.assertEqual(headline["fields"]["remaining_performance_obligation_fy"],
                         {"date": "2025-12-31", "value": 77})

    def test_vendor_scalar_shape_also_read(self):
        f = {"source": "yfinance-statements", "as_of": "2026-09-01",
             "shares_fy": {"value": 900, "as_of": "2026-08-01"}}
        headline = build.fundamentals_headline(f)
        self.assertEqual(headline["fields"]["shares_fy"], {"date": "2026-08-01", "value": 900})

    def test_empty_series_never_fabricates_a_point(self):
        f = {"source": "sec-companyfacts", "as_of": "2026-09-01", "revenue_fy": []}
        headline = build.fundamentals_headline(f)
        self.assertIsNone(headline)  # nothing else present either

    def test_none_fundamentals_returns_none(self):
        self.assertIsNone(build.fundamentals_headline(None))

    def test_market_projection_carries_headline_but_never_the_full_block(self):
        market = {"XYZ": {"ticker": "XYZ", "series": {"rows": []},
                          "fundamentals": {"source": "sec-companyfacts", "as_of": "2026-09-01",
                                          "revenue_fy": [["2025-12-31", 10]]}}}
        row = build.project_market(market)["XYZ"]
        self.assertNotIn("fundamentals", row)
        self.assertEqual(row["fundamentals_headline"]["fields"]["revenue_fy"]["value"], 10)


class TestTrimQuality(unittest.TestCase):
    """2026-09-14, the page-diet pass: market's `quality` block (511 tickers, ~0.85 MB
    before this trim) carried several sub-fields no template reads — confirmed by search
    of app.js. project_market applies _trim_quality to every ticker; these tests hold the
    kept/dropped split so a future field addition to tools/acis/quality.py does not
    silently re-inflate the page."""

    def _market_with_quality(self, quality):
        return {"XYZ": {"series": {"rows": []}, "quality": quality}}

    def test_kept_fields_survive_unchanged(self):
        q = {
            "as_of": "2026-09-13", "state": "SCORED", "formulas": "financetoolkit.models",
            "piotroski": {"score": 5, "state": "MIDDLING", "criteria": {"roa": True}},
            "beneish": {"score": -2.1, "state": "CLEAN"},
            "altman": {"score": 3.2, "state": "SAFE"},
            "reverse_dcf": {"implied_fcf_cagr": 0.05, "state": "SOLVED",
                            "implied_by_horizon": {"5": 0.05}, "assumptions": {"discount_rate": 0.09}},
            "health": {"statement_fields_found": 10, "statement_fields_needed": 12},
        }
        row = build.project_market(self._market_with_quality(q))["XYZ"]
        out = row["quality"]
        self.assertEqual(out["piotroski"]["criteria"], {"roa": True})
        self.assertEqual(out["reverse_dcf"]["implied_by_horizon"], {"5": 0.05})
        self.assertEqual(out["reverse_dcf"]["assumptions"], {"discount_rate": 0.09})
        self.assertEqual(out["health"]["statement_fields_found"], 10)
        self.assertEqual(out["formulas"], "financetoolkit.models")

    def test_confirmed_unused_fields_dropped(self):
        q = {
            "piotroski": {"score": 5, "proxies": {"shares": "note"},
                          "inputs_found": 9, "inputs_needed": 9},
            "beneish": {"score": -2.1, "inputs_found": 12, "inputs_needed": 12,
                       "common_periods": ["2025-12-31"]},
            "altman": {"score": 3.2, "inputs_found": 8, "inputs_needed": 8},
            "reverse_dcf": {"implied_fcf_cagr": 0.05, "base_fcf": 100.0,
                            "enterprise_value": 900.0, "horizon_spread": 0.01,
                            "horizon_note": "fixed boilerplate, identical for every ticker"},
            "derived": {"fcf": 100.0, "market_cap": 900.0},
        }
        row = build.project_market(self._market_with_quality(q))["XYZ"]
        out = row["quality"]
        self.assertNotIn("derived", out)
        for group in ("piotroski", "beneish", "altman"):
            for field in ("proxies", "inputs_found", "inputs_needed", "common_periods"):
                self.assertNotIn(field, out[group])
        for field in ("base_fcf", "enterprise_value", "horizon_spread", "horizon_note"):
            self.assertNotIn(field, out["reverse_dcf"])
        # The scores themselves survive — only the bookkeeping behind them is cut.
        self.assertEqual(out["piotroski"]["score"], 5)
        self.assertEqual(out["reverse_dcf"]["implied_fcf_cagr"], 0.05)

    def test_non_dict_quality_passed_through_unchanged(self):
        self.assertIsNone(build._trim_quality(None))
        self.assertEqual(build._trim_quality("PENDING_DATA"), "PENDING_DATA")

    def test_health_periods_available_dropped_statement_fields_kept(self):
        # 2026-09-14, the page-diet pass: qualityCard (app.js) reads only
        # health.statement_fields_found/needed, never the per-ticker filed-year list.
        q = {"health": {"statement_fields_found": 10, "statement_fields_needed": 12,
                        "periods_available": ["2021-12-31", "2022-12-31"]}}
        out = build.project_market(self._market_with_quality(q))["XYZ"]["quality"]
        self.assertEqual(out["health"], {"statement_fields_found": 10,
                                         "statement_fields_needed": 12})

    def test_top_level_unrendered_scalars_dropped_as_of_kept(self):
        # 2026-09-14: qualityCard binds market[T].quality to its local `q` and reads only
        # `.as_of` off the top level (plus the named sub-groups elsewhere in this class).
        q = {"as_of": "2026-09-13", "source": "yfinance-statements",
             "fundamentals_as_of": "2026-09-01", "currency_note": None,
             "official_source": False, "source_tag": "INFERRED", "state": "SCORED",
             "shares_used": {"value": 1000000.0}, "price_used": {"value": 12.5}}
        out = build.project_market(self._market_with_quality(q))["XYZ"]["quality"]
        self.assertEqual(out["as_of"], "2026-09-13")
        for field in build.QUALITY_TOP_UNRENDERED:
            self.assertNotIn(field, out)

    def test_formulas_dropped_only_when_it_matches_the_shared_note(self):
        # Byte-identical across every ticker that carries it — confirmed 2026-09-14 —
        # so a match drops in favor of the one copy in payload["method"]["quality"]
        # (QUALITY_SHARED_FORMULAS_NOTE). A ticker whose note genuinely differs (a
        # different quality provider) is never silently swapped for the shared one.
        shared = build.project_market(
            self._market_with_quality({"formulas": build.QUALITY_SHARED_FORMULAS_NOTE})
        )["XYZ"]["quality"]
        self.assertNotIn("formulas", shared)
        distinct = build.project_market(
            self._market_with_quality({"formulas": "a different methodology note"})
        )["XYZ"]["quality"]
        self.assertEqual(distinct["formulas"], "a different methodology note")


class TestTrimPcs(unittest.TestCase):
    """2026-09-14, the page-diet pass: market's `pcs` block (189,409 bytes for 595
    tickers) carried down to the one boolean app.js actually reads —
    `m.pcs.axis_a.machine_admissible`, used only to count PCS-armed tickers in the
    cortex overview. Confirmed by exhaustive search: axis_a's score/state/
    non_null_fields/flags/sub_scores, all of axis_b, gate and health feed Ember's heat
    scoring on the agent side and are never drawn."""

    def test_pcs_trimmed_to_machine_admissible_only(self):
        pcs = {"axis_a": {"score": 88.2, "state": "DARK", "machine_admissible": True,
                          "sub_scores": {"trends": 30, "wsb": 25}},
               "axis_b": {"band": "SATURATED", "fields": {"analyst_count": 26}},
               "gate": {"state": "ADVISORY", "reason": "fixtures unreadable"},
               "health": {"attempted": 4, "fetched": 2, "null": 2}}
        market = {"XYZ": {"series": {"rows": []}, "pcs": pcs}}
        out = build.project_market(market)["XYZ"]["pcs"]
        self.assertEqual(out, {"axis_a": {"machine_admissible": True}})

    def test_missing_axis_a_reads_as_none_never_a_guess(self):
        market = {"XYZ": {"series": {"rows": []}, "pcs": {"gate": {"state": "ADVISORY"}}}}
        out = build.project_market(market)["XYZ"]["pcs"]
        self.assertEqual(out, {"axis_a": {"machine_admissible": None}})

    def test_non_dict_pcs_passed_through(self):
        self.assertIsNone(build._trim_pcs(None))
        self.assertEqual(build._trim_pcs("PENDING_DATA"), "PENDING_DATA")


class TestTrimScreenFundamentals(unittest.TestCase):
    """2026-09-14, the page-diet pass: a screen row's own `fundamentals` snapshot (619 KB
    of the 1.27 MB screens store) carried a citation trail (source/as_of/tag per leaf) and
    several whole fields that stockCard() (app.js) never reads — confirmed by search of
    app.js, which pulls every figure through its own fval() helper (`.value` only)."""

    def _screen_with_row(self, fundamentals):
        return {"chain_id": "c1", "id": "s1",
                "buckets": {"pure_play": [{"ticker": "ACM", "fundamentals": fundamentals}]}}

    def test_value_fields_kept_stripped_to_value_only(self):
        f = {"revenue_fy": {"value": 100.0, "source": "data/market/ACM.json fundamentals.revenue_fy",
                            "as_of": "2026-08-31", "tag": "VERIFIED"},
             "net_income_fy": {"value": 10.0, "source": "x", "as_of": "2026-08-31", "tag": "VERIFIED"},
             "revenue_cagr_3y": {"value": 0.2, "source": "x", "as_of": "2026-08-31", "tag": "INFERRED"},
             "market_implied_fcf_cagr": {"value": 0.3, "source": "x", "as_of": "2026-08-31", "tag": "VERIFIED"},
             "piotroski": 6, "piotroski_state": "MIDDLING",
             "beneish_state": "CLEAN", "beneish_score": -2.4,
             "quality_basis": "both computed from complete inputs", "latest_fy": "2025-12-31"}
        out = build.project_screens([self._screen_with_row(f)])[0]
        row = out["buckets"]["pure_play"][0]
        fo = row["fundamentals"]
        self.assertEqual(fo["revenue_fy"], {"value": 100.0})
        self.assertEqual(fo["net_income_fy"], {"value": 10.0})
        self.assertEqual(fo["revenue_cagr_3y"], {"value": 0.2})
        self.assertEqual(fo["market_implied_fcf_cagr"], {"value": 0.3})
        self.assertEqual(fo["piotroski"], 6)
        self.assertEqual(fo["beneish_state"], "CLEAN")
        for dropped in ("piotroski_state", "beneish_score", "quality_basis", "latest_fy"):
            self.assertNotIn(dropped, fo)

    def test_pending_data_string_passed_through_unchanged(self):
        out = build.project_screens([self._screen_with_row("PENDING_DATA")])[0]
        self.assertEqual(out["buckets"]["pure_play"][0]["fundamentals"], "PENDING_DATA")

    def test_missing_fundamentals_key_does_not_appear(self):
        screen = {"chain_id": "c1", "id": "s1",
                  "buckets": {"pure_play": [{"ticker": "ACM"}]}}
        out = build.project_screens([screen])[0]
        self.assertNotIn("fundamentals", out["buckets"]["pure_play"][0])


class TestProjectPipelines(unittest.TestCase):
    def test_missing_directory_is_simply_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(build.project_pipelines(Path(tmp)), [])

    def test_pipeline_projects_and_trims_excerpts(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "pipelines").mkdir()
            doc = {
                "issuer_id": "ACME", "ticker": "ACM", "as_of": "2026-09-10",
                "status": "PARTIAL",
                "items": [{"type": "BACKLOG", "name": "Widget backlog", "stage": "signed",
                          "value": 900000, "currency": "USD", "date": "2026-08-01",
                          "claim": "Backlog reached $900k.",
                          "source_kind": "FILING", "source_ref": "8-K",
                          "source_url": "https://example.invalid/8k",
                          "source_excerpt": "  Backlog   reached   $900,000   this   quarter.  "}],
                "searched": ["site:sec.gov ACME backlog"],
            }
            (data / "pipelines" / "ACME.json").write_text(json.dumps(doc))
            rows = build.project_pipelines(data)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["issuer_id"], "ACME")
            self.assertEqual(rows[0]["items"][0]["source_excerpt"],
                             "Backlog reached $900,000 this quarter.")
            self.assertEqual(rows[0]["items"][0]["value"], 900000)  # untouched, not trimmed
            self.assertEqual(rows[0]["searched"], ["site:sec.gov ACME backlog"])

    def test_malformed_pipeline_docs_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "pipelines").mkdir()
            (data / "pipelines" / "bad.json").write_text(json.dumps({"no_issuer_id": True}))
            self.assertEqual(build.project_pipelines(data), [])


class TestProjectRequestsTrimming(unittest.TestCase):
    def test_only_non_fulfilled_rows_kept_counts_describe_the_whole_store(self):
        requests = {"requests": [
            {"id": "R1", "kind": "prices", "ticker": "AAA", "status": "FULFILLED",
             "requested_at": "2026-09-01T00:00:00Z"},
            {"id": "R2", "kind": "prices", "ticker": "AAA", "status": "PENDING",
             "requested_at": "2026-09-02T00:00:00Z"},
            {"id": "R3", "kind": "quality", "ticker": "AAA", "status": "FAILED",
             "requested_at": "2026-09-03T00:00:00Z", "note": "control probe failed"},
        ]}
        out = build.project_requests(requests)
        self.assertEqual(out["total"], 3)
        self.assertEqual({r["id"] for r in out["requests"]}, {"R2", "R3"})
        self.assertEqual(out["by_kind_status"]["prices"]["FULFILLED"], 1)
        self.assertEqual(out["by_kind_status"]["prices"]["PENDING"], 1)
        self.assertEqual(out["by_kind_status"]["quality"]["FAILED"], 1)
        self.assertEqual(out["latest_by_ticker"]["AAA"]["prices"]["id"], "R2")
        self.assertEqual(out["latest_by_ticker"]["AAA"]["quality"]["status"], "FAILED")
        self.assertEqual(out["settled"], 1)  # only the FULFILLED row is settled

    def test_latest_by_ticker_drops_fulfilled_entries_and_fulfilled_at(self):
        # 2026-09-14, the page-diet pass: the company page's reqLatest block (app.js)
        # filters to status != "FULFILLED" before rendering anything and never reads
        # `fulfilled_at` even for the rows it keeps — confirmed by exhaustive search.
        # 437,070 of 500,804 real bytes were exactly this: FULFILLED (ticker,kind)
        # entries shipped only to be filtered out client-side.
        requests = {"requests": [
            {"id": "R1", "kind": "prices", "ticker": "AAA", "status": "FULFILLED",
             "requested_at": "2026-09-01T00:00:00Z", "fulfilled_at": "2026-09-01T01:00:00Z"},
            {"id": "R2", "kind": "quality", "ticker": "AAA", "status": "PENDING",
             "requested_at": "2026-09-02T00:00:00Z", "note": "queued"},
        ]}
        out = build.project_requests(requests)
        self.assertNotIn("prices", out["latest_by_ticker"]["AAA"])
        self.assertNotIn("fulfilled_at", out["latest_by_ticker"]["AAA"]["quality"])
        self.assertEqual(out["latest_by_ticker"]["AAA"]["quality"]["note"], "queued")

    def test_ticker_with_only_fulfilled_entries_absent_not_an_empty_dict(self):
        requests = {"requests": [
            {"id": "R1", "kind": "prices", "ticker": "AAA", "status": "FULFILLED",
             "requested_at": "2026-09-01T00:00:00Z"},
        ]}
        out = build.project_requests(requests)
        self.assertNotIn("AAA", out["latest_by_ticker"])

    def test_kept_rows_drop_fetch_workflow_bookkeeping_fields(self):
        # pipelineRequestRow (app.js) reads only kind/status/note/id/requested_at (plus
        # ticker/url) — confirmed by exhaustive search. Everything in
        # REQUEST_ROW_UNRENDERED is the fetch workflow's own retry/verification state.
        requests = {"requests": [
            {"id": "R1", "kind": "edgar_doc", "ticker": "AAA", "status": "PENDING",
             "requested_at": "2026-09-02T00:00:00Z", "requested_by": "ron",
             "last_attempt_at": "2026-09-03T00:00:00Z", "query": "10-K",
             "forms": ["10-K"], "lookback_days": 90, "attempts": 2, "cik": "0000320193",
             "wrote": "data/edgar/docs/AAA.json", "by": "routine"},
        ]}
        row = build.project_requests(requests)["requests"][0]
        self.assertEqual(row["id"], "R1")
        self.assertEqual(row["kind"], "edgar_doc")
        for field in build.REQUEST_ROW_UNRENDERED:
            self.assertNotIn(field, row)


class TestProjectLinkTrimming(unittest.TestCase):
    """2026-09-14, the page-diet pass: a chain link's price-test fields
    (instrument_search/scarce_price/price_instruments, method §4) and heat.instrument
    have no render path — confirmed by exhaustive search of app.js. All three stay
    required on data/chains/<slug>.json, which tools/check_chain.py validates directly."""

    def _link(self, **overrides):
        link = {"id": "l1", "name": "Link One", "position": 1,
                "instrument_search": {"searched_at": "2026-09-14", "result": "FOUND"},
                "scarce_price": {"name": "Benchmark", "unit": "USD"},
                "price_instruments": [{"ticker": "ETF1", "holds": "x"}],
                "heat": {"verdict": "CROWDED",
                        "instrument": {"as_of": "2026-09-13",
                                      "crowdedness": {"score": 68, "rationale": "r"}}}}
        link.update(overrides)
        return link

    def test_price_test_fields_dropped(self):
        row = build._project_link(self._link())
        for field in build.LINK_UNRENDERED:
            self.assertNotIn(field, row)

    def test_heat_instrument_dropped_verdict_kept(self):
        row = build._project_link(self._link())
        self.assertNotIn("instrument", row["heat"])
        self.assertEqual(row["heat"]["verdict"], "CROWDED")

    def test_explainer_draws_on_and_lang_dropped_rest_kept(self):
        link = self._link(explainer={"what": "It moves goods.", "players": "Yards.",
                                     "why": "Bottleneck.", "bottleneck": "Yes.",
                                     "hands_to": "Next link.", "as_of": "2026-09-01",
                                     "by": "atlas", "draws_on": ["SIG-1"], "lang": "he"})
        row = build._project_link(link)
        out = row["explainer"]
        self.assertEqual(out["what"], "It moves goods.")
        self.assertEqual(out["by"], "atlas")
        self.assertNotIn("draws_on", out)
        self.assertNotIn("lang", out)


class TestProjectChainTrimming(unittest.TestCase):
    def test_map_limitation_dropped_explainer_trimmed(self):
        # Never rendered by any template — confirmed 2026-09-14 — while staying required
        # on disk, where tools/check_chain.py enforces it and chainPath() names it.
        chain = {"id": "c1", "title": "Chain One", "links": [], "scenarios": [],
                 "map_limitation": "Cannot see private tier-3 suppliers.",
                 "explainer": {"shape": "Linear.", "thesis": "x", "draws_on": ["SIG-1"],
                              "lang": "he"}}
        doc = build._project_chain(chain)
        self.assertNotIn("map_limitation", doc)
        self.assertNotIn("draws_on", doc["explainer"])
        self.assertEqual(doc["explainer"]["shape"], "Linear.")


class TestProjectScenarioTrimming(unittest.TestCase):
    def test_leading_indicator_check_and_check_source_dropped(self):
        # `.armed`, `.check_basis`, `.where_to_watch`, `.tripped_at` and indText()'s
        # `.signal`/`.indicator` are the only fields a scenario's indicator renders
        # through (app.js) — confirmed 2026-09-14 by exhaustive search.
        scenario = {"id": "S1", "title": "Scenario", "evidence": [],
                   "leading_indicators": [
                       {"signal": "ETR above 52w high", "armed": True,
                        "check_basis": "Price crosses the 52-week high.",
                        "check": {"type": "PRICE", "ticker": "ETR", "op": "ABOVE",
                                 "level": "52w_high"},
                        "check_source": "data/market/ETR.json week52"},
                   ]}
        row = build._project_scenario(scenario)
        ind = row["leading_indicators"][0]
        self.assertEqual(ind["signal"], "ETR above 52w high")
        self.assertTrue(ind["armed"])
        self.assertNotIn("check", ind)
        self.assertNotIn("check_source", ind)


class TestProjectScreensTrimming(unittest.TestCase):
    """2026-09-14, the page-diet pass: a screen document's search-log bookkeeping
    (queries_run/superseded_rows) and a row's identity/audit bookkeeping
    (audit_scope/audit_scope_basis/secondary_links/secondary_link_basis/mapping_ref/
    profile_ref/listing_id/market_ticker) have no render path — confirmed by exhaustive
    search of app.js. Both stay required on data/screens/<chain>.json for
    tools/check_screen.py."""

    def test_screen_doc_search_log_fields_dropped(self):
        screen = {"chain_id": "c1", "id": "s1", "buckets": {},
                 "queries_run": ["site:sec.gov widget maker"],
                 "superseded_rows": [{"ticker": "OLD"}]}
        out = build.project_screens([screen])[0]
        for field in build.SCREEN_UNRENDERED:
            self.assertNotIn(field, out)

    def test_screen_row_identity_audit_fields_dropped(self):
        row = {"ticker": "ACM", "chain_id": "c1", "link_id": "l1", "issuer_id": "ACME",
              "audit_scope": "PLACEMENT", "audit_scope_basis": "current PASS entry",
              "secondary_links": ["l2"], "secondary_link_basis": "shared supplier",
              "mapping_ref": "data/mappings/c1.json", "profile_ref": "data/companies/ACME.json",
              "listing_id": "NASDAQ-ACM", "market_ticker": "ACM"}
        screen = {"chain_id": "c1", "id": "s1", "buckets": {"pure_play": [row]}}
        out = build.project_screens([screen])[0]["buckets"]["pure_play"][0]
        self.assertEqual(out["ticker"], "ACM")
        self.assertEqual(out["issuer_id"], "ACME")
        for field in build.SCREEN_ROW_UNRENDERED:
            self.assertNotIn(field, out)

    def test_crowdedness_trimmed_to_state_and_pcs_axis_a(self):
        # stockCard() (app.js) reads only `.state` and `.pcs_axis_a` off a row's own PCS
        # cross-check — confirmed 2026-09-14 by exhaustive search.
        row = {"ticker": "ACM", "crowdedness": {
            "state": "DARK", "pcs_axis_a": 88.2, "basis": "x" * 300,
            "caveat": "y" * 200, "source": "data/market/ACM.json pcs",
            "sub_scores": {"trends": 30}, "analyst_count": 26}}
        screen = {"chain_id": "c1", "id": "s1", "buckets": {"pure_play": [row]}}
        out = build.project_screens([screen])[0]["buckets"]["pure_play"][0]
        self.assertEqual(out["crowdedness"], {"state": "DARK", "pcs_axis_a": 88.2})


class TestProjectCandidatesTrimming(unittest.TestCase):
    def test_campaign_record_dropped_rest_kept(self):
        # A full copy of the candidate's `run campaign init` evaluation, never read by
        # app.js — confirmed 2026-09-14. The campaign manifest is the permanent record.
        candidates = {"as_of": "2026-09-14", "candidates": [
            {"id": "CAND-1", "title": "x", "changelog": [{"ts": "2026-09-01", "by": "nell",
             "change": "created"}],
             "campaign_record": {"dimensions": {"occurrence_strength": {"score": 85}}}},
        ]}
        out = build.project_candidates(candidates)["candidates"][0]
        self.assertEqual(out["id"], "CAND-1")
        self.assertEqual(out["changelog_total"], 1)
        self.assertNotIn("campaign_record", out)


class TestProjectBook(unittest.TestCase):
    """2026-09-14, the page-diet pass: data/book.json's 33-field ranked rows trimmed to
    the 4 fields bookRowsForTicker (app.js) actually reads — confirmed by exhaustive
    search: ticker/chain_id are the lookup keys, price/piotroski the only facts the link
    modal's "additional names" list draws from a matched row."""

    def test_rows_trimmed_methodology_block_kept(self):
        book = {"id": "book-1", "ranking": {"method": "x"}, "denominator": 229,
               "rows": [{"ticker": "ACM", "chain_id": "c1", "price": 45.2,
                        "piotroski": 6, "rank": 3, "heat_verdict": "CROWDED",
                        "investability": "HIGH", "criticality": "CHOKE_POINT"}]}
        out = build.project_book(book)
        self.assertEqual(out["ranking"], {"method": "x"})
        self.assertEqual(out["denominator"], 229)
        self.assertEqual(out["rows"], [{"ticker": "ACM", "chain_id": "c1",
                                        "price": 45.2, "piotroski": 6}])

    def test_non_dict_book_passed_through(self):
        self.assertIsNone(build.project_book(None))


class TestProjectAgentLogs(unittest.TestCase):
    """2026-09-14, the page-diet pass: the rank/map/scout calibration logs each carry a
    block or four no template reads — confirmed by exhaustive search of app.js."""

    def test_rank_log_drops_queue_and_changelog(self):
        rank_log = {"as_of": "2026-09-14", "calibration": {"denominators": {"a": 1}},
                   "queue": ["SIG-1", "SIG-2"], "changelog": [{"ts": "x"}]}
        out = build.project_rank(rank_log)
        self.assertEqual(out["calibration"], {"denominators": {"a": 1}})
        self.assertNotIn("queue", out)
        self.assertNotIn("changelog", out)

    def test_map_log_drops_dead_blocks_keeps_notes_and_archetypes(self):
        # mapCard (app.js) filters .notes for ESCALATION-tagged rows and reads
        # .calibration/.archetypes in full — confirmed 2026-09-14.
        map_log = {"calibration": {"per_chain": {}}, "archetypes": [{"status": "HARDENED"}],
                  "notes": [{"text": "ESCALATION: x"}], "changelog": [{"ts": "x"}],
                  "spot_tests": [{"x": 1}], "repairs": [{"x": 1}], "confidence_audit": {}}
        out = build.project_map_log(map_log)
        self.assertEqual(out["notes"], [{"text": "ESCALATION: x"}])
        self.assertEqual(out["archetypes"], [{"status": "HARDENED"}])
        for field in ("changelog", "spot_tests", "repairs", "confidence_audit"):
            self.assertNotIn(field, out)

    def test_scout_log_drops_dead_blocks_keeps_notes_and_proposed_rules(self):
        scout_log = {"calibration": {"denominators": {}}, "proposed_rules": [{"status": "HARDENED"}],
                    "notes": [{"text": "ESCALATION: y"}], "changelog": [{"ts": "x"}],
                    "spot_tests": [{"x": 1}], "repairs": [{"x": 1}], "confidence_audit": {}}
        out = build.project_scout_log(scout_log)
        self.assertEqual(out["notes"], [{"text": "ESCALATION: y"}])
        self.assertEqual(out["proposed_rules"], [{"status": "HARDENED"}])
        for field in ("changelog", "spot_tests", "repairs", "confidence_audit"):
            self.assertNotIn(field, out)


class TestLedgerCap(unittest.TestCase):
    def test_ledger_capped_to_newest_200_with_a_truthful_total(self):
        # Tightened from 400 to 200 on 2026-09-14 (the page-diet pass): room-making room
        # for companies/placements/pipelines under the 12.5 MB page target.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            for folder in ("signals", "chains", "screens", "stocks", "impact", "digest",
                           "companies", "mappings", "pipelines"):
                (data / folder).mkdir(parents=True)
            (data / "market").mkdir()
            (data / "shadow").mkdir()
            (data / "health").mkdir()
            (data / "radar").mkdir()
            (data / "calendar").mkdir()
            (data / "feeds").mkdir()
            (data / "themes").mkdir()
            (data / "campaigns").mkdir()
            (data / "requests.json").write_text(json.dumps({"requests": []}))
            (data / "ledger.md").write_text("\n".join(
                f"2026-09-{(i % 28) + 1:02d} 12:00Z | RUN | line {i}" for i in range(500)))
            payload = build.build_payload(data, root)
            self.assertEqual(len(payload["ledger"]), 200)
            self.assertEqual(payload["ledger_total"], 500)
            self.assertEqual(payload["ledger"][-1], "2026-09-24 12:00Z | RUN | line 499")


def _minimal_data_tree(root: Path) -> Path:
    """The same bare-minimum data/ tree TestLedgerCap builds, factored out so the
    2026-09-14 page-diet pass's other build_payload-level tests (digests, theme
    occurrence rows) do not each retype it."""
    data = root / "data"
    for folder in ("signals", "chains", "screens", "stocks", "impact", "digest",
                   "companies", "mappings", "pipelines"):
        (data / folder).mkdir(parents=True)
    (data / "market").mkdir()
    (data / "shadow").mkdir()
    (data / "health").mkdir()
    (data / "radar").mkdir()
    (data / "calendar").mkdir()
    (data / "feeds").mkdir()
    (data / "themes").mkdir()
    (data / "campaigns").mkdir()
    (data / "requests.json").write_text(json.dumps({"requests": []}))
    (data / "ledger.md").write_text("")
    return data


class TestDigestsKeepsOnlyNewest(unittest.TestCase):
    def test_only_the_newest_week_is_carried(self):
        # `(D.digests || [])[0]` — the only way app.js ever reads a digest, three call
        # sites, confirmed 2026-09-14 by exhaustive search; no view lists past weeks, and
        # `run digest` never prunes data/digest/, so the unread tail only grows.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = _minimal_data_tree(root)
            for week in ("2026-34", "2026-35", "2026-36"):
                (data / "digest" / f"{week}.json").write_text(
                    json.dumps({"week": week, "verdicts": {"named": []}}))
            payload = build.build_payload(data, root)
            self.assertEqual(len(payload["digests"]), 1)
            self.assertEqual(payload["digests"][0]["week"], "2026-36")


class TestThemeOccurrenceRowsTrimming(unittest.TestCase):
    def test_id_and_theme_by_dropped_rest_of_the_row_kept(self):
        # thOccRow (app.js) reads t/s/u/d/o/r/f/b/th — never the row's own `id` or
        # `theme_by` — confirmed 2026-09-14 by exhaustive search of the whole occurrence
        # log section. The permanent row on disk keeps both.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = _minimal_data_tree(root)
            (data / "themes" / "themes.json").write_text(json.dumps({
                "as_of": "2026-09-14", "themes": [], "calibration": {}}))
            (data / "themes" / "occurrences.json").write_text(json.dumps({"occurrences": [
                {"id": "OCC-1", "title": "x", "source": "y", "url": "https://example.invalid",
                 "ts": "2026-09-14", "origin": "feed", "origin_ref": "f1", "family": "POLICY",
                 "theme_id": "th-1", "theme_basis": "matched 'tariff'", "theme_by": "nell"},
            ]}))
            payload = build.build_payload(data, root)
            row = payload["themes"]["rows"][0]
            self.assertEqual(row["t"], "x")
            self.assertEqual(row["th"], "th-1")
            self.assertEqual(row["b"], "matched 'tariff'")
            self.assertNotIn("i", row)
            self.assertNotIn("by", row)

    def test_rows_are_capped_at_the_newest_1500_and_total_stays_the_corpus(self):
        # 2026-09-18: the projection's own comment promised a trim the code never did, so
        # `rows` grew one entry per occurrence forever against an append-only store. A
        # single ingest catch-up took the real page over the 12.5 MB target below. The cap
        # is the newest 1500 by (ts, id) descending; `total` must keep reporting the whole
        # corpus, because app.js decides whether to say "on this page" by comparing the
        # two, and a `total` that shrank to len(rows) would make a truncated page claim to
        # be the whole log.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = _minimal_data_tree(root)
            (data / "themes" / "themes.json").write_text(json.dumps({
                "as_of": "2026-09-18", "themes": [], "calibration": {}}))
            (data / "themes" / "occurrences.json").write_text(json.dumps({"occurrences": [
                {"id": f"OCC-{i:05d}", "title": f"item {i}", "source": "wire",
                 "url": "https://example.invalid", "ts": f"2026-09-{(i % 28) + 1:02d}",
                 "origin": "feed", "family": "POLICY"}
                for i in range(1600)]}))
            payload = build.build_payload(data, root)
            th = payload["themes"]
            self.assertEqual(th["total"], 1600)
            self.assertEqual(len(th["rows"]), 1500)
            # Newest first, so the row that survives at the head is the latest date.
            self.assertEqual(th["rows"][0]["d"], "2026-09-28")
            self.assertEqual(payload["method"]["page"]["occurrence_rows"],
                             "newest 1500 of 1600")

    def test_a_log_under_the_cap_is_carried_whole_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = _minimal_data_tree(root)
            (data / "themes" / "themes.json").write_text(json.dumps({
                "as_of": "2026-09-18", "themes": [], "calibration": {}}))
            (data / "themes" / "occurrences.json").write_text(json.dumps({"occurrences": [
                {"id": f"OCC-{i:05d}", "title": f"item {i}", "source": "wire",
                 "ts": "2026-09-18", "origin": "feed", "family": "POLICY"}
                for i in range(10)]}))
            payload = build.build_payload(data, root)
            self.assertEqual(payload["themes"]["total"], 10)
            self.assertEqual(len(payload["themes"]["rows"]), 10)
            self.assertEqual(payload["method"]["page"]["occurrence_rows"], "all")


class TestRealPageSize(unittest.TestCase):
    """The page-diet engineering brief's own target (2026-09-14): with companies,
    placements, fundamentals headlines and pipelines added, the real page — this
    worktree's actual data/, not a synthetic fixture — builds AT OR BELOW 12.5 MB, a
    number this test hard-codes rather than derives from build.page_byte_limit()
    (PAGE_MAX_MB=16 less its refuse margin, 15.5 MB). That gate is the platform's own
    refuse point and stays exactly as it is; 12.5 MB is a stricter, separate target this
    test exists to hold, so a build that fits under the platform's ceiling but is
    quietly getting heavier again does not pass silently until it hits 15.5 MB.

    History, so the next session does not repeat it: this test asserted a hand-picked
    15,000,000 on 2026-09-13, was loosened the same day to bind to build.page_byte_limit()
    instead ("bind to the gate, not to a number invented before the day's other landings
    were known") after the real build reached 15,377,296 bytes, and then measured
    15.94 MB and FAILED even that loosened gate once the a1-app company/placement/
    pipeline work and six more chains landed together. The fix this time is the other
    direction: hold a real ceiling below the platform's own, and make the build small
    enough to clear it — daily-vs-weekly market series (project_market), a hard-trimmed
    companies projection (project_companies, 0.7 MB), a 200-line ledger cap
    (LEDGER_CARRIED_LINES), and the confirmed-dead fields this pass found along the way
    (board.blocked[].gaps duplicating companies[].data_gaps; quality's
    inputs_found/inputs_needed/common_periods/proxies/derived/base_fcf/enterprise_value/
    horizon_spread/horizon_note; placements[].mapping_status; listings[].listing_id and
    .market_ticker). Never raise this number to make a build pass; shrink the build."""

    PAGE_TARGET_BYTES = 12_500_000

    def test_real_build_is_at_or_below_the_page_diet_target(self):
        if not (build.DATA / "companies").is_dir():
            self.skipTest("no data/companies on this tree")
        payload = build.build_payload()
        html = build.assemble_html(payload)
        size = len(html.encode())
        self.assertLessEqual(size, self.PAGE_TARGET_BYTES,
                             f"real page is {size:,} bytes, over the page-diet target "
                             f"({self.PAGE_TARGET_BYTES:,}); shrink a projection, never "
                             "raise this number")
        # The target is meaningfully under the platform's own refuse point, never past it.
        self.assertLess(self.PAGE_TARGET_BYTES, build.page_byte_limit())


if __name__ == "__main__":
    unittest.main()
