#!/usr/bin/env python3
"""The page must still open when the campaign is finished, not only tonight.

tools/tests/test_campaign_ui.py already asserts that TODAY's payload plus a full-scale
campaign projection fits app/build.py's own size threshold. That is a weaker claim than it
looks: it stresses one store against the page as it is now, and on 2026-08-30 the page was
2,636,580 bytes against a 2,000,000-byte threshold with a ten-theme campaign just frozen
and heading for 10 chains, ~200 profiles, 30-60 dives and several hundred tickers. Nothing
in the suite could answer "does this build still fit when all of that lands", so nothing
did, until it did not.

This file answers it, over a synthetic data/ tree at that scale whose objects are padded
to the byte sizes of the real ones (tools/tests/_page_scale_fixture.py carries the
measurements and their provenance). It fails if a future build starts inlining a store at
full fidelity again, and it fails if a projection is quietly removed.

It is deliberately NOT a test of how much is cut. It is a test of two things: the page
fits, and every cut is declared in the payload so app.js can print it. A build that got
under budget by silently dropping data would pass the first and fail the second.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _page_scale_fixture as fx  # noqa: E402


def _load_build():
    spec = importlib.util.spec_from_file_location("page_scale_build",
                                                  ROOT / "app" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build()
HTML_BUDGET = int(build.SIZE_WARN_MB * 1_000_000)

# The finished campaign, as CLAUDE.md's `run campaign init` contract locks it: 10 themes,
# 10 issuer maps, at least 200 complete profiles, 30-60 O1 dives. Tickers, signals,
# screens, appraisals and ambient candidates are set at the level those imply.
SCALE = dict(themes=10, links_per_chain=12, profiles=200, dives=60, tickers=400,
             signals=20, screens=10, impacts=60, candidates=200, occurrences=900,
             requests=2400)


class ScaleFixtureCase(unittest.TestCase):
    """One synthetic tree, built once: it writes ~500 files and is slow to make."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.data = Path(fx.write(cls._tmp.name, **SCALE))
        cls.payload = build.build_payload(cls.data, ROOT)
        cls.html = build.assemble_html(cls.payload)
        cls.sizes = build.store_sizes(cls.payload)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class TestFixtureIsRealisticallySized(ScaleFixtureCase):
    """A fixture smaller than reality proves a budget nobody has to meet.

    These are the numbers the whole file rests on, so they are asserted rather than
    trusted. If a real object grows past its fixture, update the fixture — never the
    other way round.
    """

    def _first(self, store):
        files = sorted((self.data / store).glob("[!_]*.json"))
        self.assertTrue(files, f"fixture wrote no {store}")
        return len(files[0].read_bytes())

    def test_objects_match_the_real_stores_they_stand_in_for(self):
        for store, measured in (("stocks", 55_440), ("chains", 39_955),
                                ("screens", 19_880), ("impact", 19_764),
                                ("signals", 9_270)):
            with self.subTest(store=store):
                self.assertGreaterEqual(
                    self._first(store), int(measured * 0.95),
                    f"the synthetic {store} object is smaller than the real one measured "
                    f"on 2026-08-30 ({measured:,} bytes); this test would then pass on a "
                    f"page that the real store does not fit in")

    def test_counts_are_the_campaign_the_manifest_locked(self):
        self.assertEqual(len(list((self.data / "chains").glob("[!_]*.json"))), 10)
        self.assertEqual(len(list((self.data / "stocks").glob("[!_]*.json"))), 60)
        self.assertEqual(len(list((self.data / "market").glob("[!_]*.json"))), 400)
        self.assertEqual(len(list((self.data / "companies").glob("*.json"))), 200)
        self.assertEqual(len(list((self.data / "mappings").glob("*.json"))), 10)


class TestProjectedCampaignScaleFitsThePage(ScaleFixtureCase):
    def test_page_stays_inside_the_single_file_budget(self):
        size = len(self.html.encode())
        biggest = sorted(self.sizes.items(), key=lambda kv: -kv[1])[:5]
        self.assertLess(
            size, HTML_BUDGET,
            "at the finished campaign's scale the page is "
            f"{size:,} bytes against app/build.py's own {HTML_BUDGET:,}-byte threshold. "
            "Heaviest stores: " + ", ".join(f"{k} {v:,}" for k, v in biggest) +
            ". Raise a projection's fidelity knob in app/build.py, never SIZE_WARN_MB")

    def test_no_store_silently_takes_over_the_page(self):
        overruns = build.store_overruns(self.payload)
        self.assertFalse(
            [o for o in overruns],
            "a store is over its STORE_SHARE_BYTES allowance at campaign scale: "
            + "; ".join(f"{s} {n:,} > {share:,}" for s, n, share in overruns))

    def test_the_biggest_raw_stores_are_projected_not_inlined(self):
        """The three that would each blow the page on their own."""
        raw = {}
        for store in ("stocks", "market", "impact"):
            folder = self.data / store
            raw[store] = sum(f.stat().st_size for f in folder.glob("[!_]*.json"))
        self.assertGreater(raw["stocks"], 3_000_000)
        self.assertGreater(raw["market"], 5_000_000)
        self.assertGreater(raw["impact"], 1_000_000)
        for store in ("stocks", "market", "impact"):
            with self.subTest(store=store):
                self.assertLess(self.sizes[store], raw[store] / 4)


class TestEveryCutIsDeclaredInThePayload(ScaleFixtureCase):
    """Getting under budget by dropping data silently is the failure this guards.

    Each assertion below is a denominator the page can print. tools/check_render.py holds
    the other half — that app.js actually prints them.
    """

    def test_dive_fidelity_is_stated_and_adds_up(self):
        note = self.payload["carried"]["stocks"]
        self.assertEqual(note["total"], SCALE["dives"])
        self.assertEqual(note["carried"] + note["summary"] + note["index_only"],
                         note["total"])
        self.assertGreaterEqual(note["carried"], 1,
                                "at least the most recent dive must be readable in full")
        for row in self.payload["stocks"]:
            self.assertIn("detail_inlined", row,
                          "every dive row must say which fidelity it is")
            for field in ("ticker", "chain_id", "verdict", "clock", "review_by"):
                self.assertIn(field, row,
                              f"{field} must survive at every fidelity — the lists, the "
                              "cortex and the dive hero all read it")
            if not row["detail_inlined"]:
                self.assertNotIn("expectations_gap", row)

    def test_market_series_states_what_it_kept_and_what_the_file_holds(self):
        header_only, sampled = 0, 0
        for ticker, doc in self.payload["market"].items():
            series = doc.get("series")
            self.assertIsNotNone(series, f"{ticker} lost its series header entirely")
            self.assertIn("row_count", series,
                          "the page must carry the file's real point count, not only its own")
            self.assertIn("sampling", series)
            self.assertIn("inlined_rows", series)
            self.assertEqual(series["inlined_rows"], len(series.get("rows") or []))
            if series["sampling"] == "HEADER_ONLY":
                header_only += 1
                self.assertEqual(series["inlined_rows"], 0)
            else:
                sampled += 1
                self.assertLessEqual(series["inlined_rows"],
                                     build.MARKET_SERIES_POINTS)
                self.assertLess(series["inlined_rows"], series["row_count"],
                                "a sampled series must be smaller than the file's")
                # First and last point kept, so the span the chart draws is the real one.
                self.assertEqual(series["rows"][0][0], "2023-01-01")
        self.assertGreater(sampled, 0, "no ticker got a chart at all")
        self.assertGreater(header_only, 200,
                           "tickers with no dive must not be carrying price series")

    def test_impact_carries_source_counts_not_source_text(self):
        blob = json.dumps(self.payload["impact"], separators=(",", ":"))
        self.assertNotIn("source_excerpt", blob)
        self.assertNotIn("\"evidence\"", blob)
        full = [a for a in self.payload["impact"] if a["legs_inlined"]]
        chip = [a for a in self.payload["impact"] if not a["legs_inlined"]]
        self.assertTrue(full and chip,
                        "both impact fidelities must be exercised at this scale")
        for appraisal in self.payload["impact"]:
            for leg in ("money_at_stake", "public_reach", "capture_odds", "timing_fit"):
                self.assertIn("evidence_count", appraisal[leg],
                              "a leg with its evidence dropped and no count reads as an "
                              "unsourced assertion")
                self.assertGreater(appraisal[leg]["evidence_count"], 0)
            self.assertIsNotNone(appraisal["id"],
                                 "the page must be able to name the file it did not carry")
        for appraisal in full:
            self.assertTrue(appraisal["public_reach"].get("rationale"))
        for appraisal in chip:
            self.assertNotIn("rationale", appraisal["public_reach"])

    def test_append_only_history_carries_its_full_count(self):
        seen = 0
        for store in ("signals", "chains", "screens"):
            for doc in self.payload[store]:
                if "changelog" in doc:
                    seen += 1
                    self.assertIn("changelog_total", doc)
                    self.assertLessEqual(len(doc["changelog"]),
                                         build.HISTORY_ROWS_INLINED)
                    self.assertLessEqual(len(doc["changelog"]), doc["changelog_total"])
        self.assertGreater(seen, 0, "nothing carried a changelog — the scan found nothing")

    def test_stores_the_page_only_summarizes_carry_their_denominator(self):
        self.assertEqual(self.payload["requests"]["total"], SCALE["requests"])
        self.assertGreater(self.payload["requests"]["settled"], 0)
        self.assertTrue(all(r["status"] in ("PENDING", "FAILED")
                            for r in self.payload["requests"]["requests"]))
        self.assertEqual(self.payload["candidates"]["total"], SCALE["candidates"])
        self.assertNotIn("campaign_record",
                         json.dumps(self.payload["candidates"], separators=(",", ":")))
        self.assertEqual(self.payload["themes"]["total"], SCALE["occurrences"])
        self.assertLessEqual(len(self.payload["themes"]["rows"]),
                             build.OCCURRENCE_ROWS_INLINED)
        self.assertLess(len(self.payload["themes"]["rows"]),
                        self.payload["themes"]["total"])

    def test_link_citations_survive_as_counts(self):
        counted = 0
        for chain in self.payload["chains"]:
            for link in chain["links"]:
                self.assertNotIn("evidence", link,
                                 "the link citation array reaches no template; carrying "
                                 "it is 46 KB of invisible page")
                self.assertIn("evidence_count", link)
                counted += link["evidence_count"]
        self.assertGreater(counted, 0)


if __name__ == "__main__":
    unittest.main()
