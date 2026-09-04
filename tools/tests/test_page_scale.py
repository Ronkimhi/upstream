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

What it measured on 2026-08-30, after the fixture's chain size was corrected from a stale
39,955 bytes (a chain from before heat and scenarios existed) to the real 200,005:

    every chain and every dive at navigation-only fidelity   1,823,106 bytes
    10 chains at full analytical fidelity                    1,258,003
    60 dives at full analytical fidelity                     3,581,934
    everything at full fidelity                             ~6,290,000
    the budget                                               2,000,000
    (2026-09-04: the budget is gone. Ron: "I don't care about the digital size of the
    pages, the megabytes. I just want all the data." Every store is carried whole and
    the only ceiling is the platform's 16 MB, which build.page_byte_limit() refuses.)

The floor — the page with no written analysis in it at all — is 91% of the budget. So at
the campaign the manifest locked, this one file holds the navigation for ten themes, two
hundred profiles and sixty dives plus ONE chain and ONE dive of actual writing, and the
elastic budgets in app/build.py spend it in campaign theme-rank order. That is not a
tuning problem; 6.3 MB does not fit in 2 MB by any projection that keeps the prose. It
does fit inside the artifact platform's own 16 MB cap, which is the decision this test
exists to put in front of a human: a higher SIZE_WARN_MB, a split page, or on-demand
loading. Until one of those is chosen, the page degrades visibly and says so.
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
HTML_BUDGET = build.page_byte_limit()

# The finished campaign, as CLAUDE.md's `run campaign init` contract locks it: 10 themes,
# 10 issuer maps, at least 200 complete profiles, 30-60 O1 dives. Tickers, signals,
# screens, appraisals and ambient candidates are set at the level those imply.
SCALE = dict(themes=10, links_per_chain=13, profiles=200, dives=60, tickers=400,
             signals=20, screens=10, impacts=60, candidates=200, occurrences=900,
             requests=2400, scenarios_per_chain=5)


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
        # Re-measured 2026-08-30 (second pass). The first pass put a chain at 39,955
        # bytes — data/chains/ai-infrastructure.json as it was BEFORE heat blocks and
        # scenarios existed — and that one stale number is why this file's forward
        # projection said "1.9 MB, 91 KB under budget" while the real page was already
        # 2,112,067 bytes. A chain is now the heaviest object in the repo: the mean over
        # the nine data/chains/*.json carrying full heat AND scenarios is 200,005 bytes
        # (range 135,806 .. 239,104). See tools/tests/_page_scale_fixture.py.
        for store, measured in (("stocks", 59_665), ("chains", 200_005),
                                ("market", 25_909),
                                ("screens", 19_880), ("impact", 21_257),
                                ("signals", 10_205)):
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
    def test_page_stays_under_the_platform_cap(self):
        size = len(self.html.encode())
        biggest = sorted(self.sizes.items(), key=lambda kv: -kv[1])[:5]
        self.assertLess(
            size, HTML_BUDGET,
            "at the finished campaign's scale, carried whole, the page is "
            f"{size:,} bytes against the platform cap less margin ({HTML_BUDGET:,}). "
            "Heaviest stores: " + ", ".join(f"{k} {v:,}" for k, v in biggest) +
            ". A store has outgrown one file; that is a split-page decision for Ron, "
            "never a trim")


def _decode_series(head: dict) -> list:
    """The python twin of app.js decodeSeries(), so the test can prove the round trip."""
    from datetime import date, timedelta
    c = head.get("rows_c") or {}
    rows = []
    if c.get("start"):
        start = date.fromisoformat(c["start"])
        for off, v in zip(c.get("d") or [], c.get("c") or []):
            d = (start + timedelta(days=off)).isoformat()
            rows.append([d] + (v if isinstance(v, list) else [v]))
    rows.extend(c.get("raw") or [])
    return rows


class TestEveryStoreIsCarriedWhole(ScaleFixtureCase):
    """Ron, 2026-09-04: all the data. Nothing on disk that a template can render may be
    cut, and every denominator the page prints must equal what it carries."""

    def test_dives_and_chains_are_all_whole(self):
        for store in ("stocks", "chains"):
            note = self.payload["carried"][store]
            self.assertEqual(note["carried"], note["total"], store)
            self.assertEqual(note["summary"], 0)
            self.assertEqual(note["index_only"], 0)
        self.assertEqual(self.payload["carried"]["stocks"]["total"], SCALE["dives"])
        self.assertEqual(self.payload["carried"]["chains"]["total"], SCALE["themes"])
        for row in self.payload["stocks"]:
            self.assertTrue(row.get("detail_inlined"))
            self.assertIn("expectations_gap", row)
            self.assertIn("red_team", row)
        for chain in self.payload["chains"]:
            self.assertEqual(chain.get("chain_fidelity"), "FULL")
            self.assertIn("notes", chain)
            for link in chain["links"]:
                self.assertIn("evidence", link)
                self.assertEqual(len(link["evidence"]), link["evidence_count"])
                self.assertIn("capture_inputs", link)
                for leg in ("impact", "crowdedness", "capture"):
                    block = (link.get("heat") or {}).get(leg)
                    if not isinstance(block, dict):
                        continue
                    self.assertTrue(block.get("rationale"))
                    self.assertEqual(len(block["evidence"]), block["evidence_total"])
                    self.assertTrue(all("source_excerpt" in e for e in block["evidence"]
                                        if isinstance(e, dict) and e.get("source_excerpt") is not None))
            for scen in chain["scenarios"]:
                self.assertIn("evidence", scen)
                self.assertEqual(len(scen["evidence"]), scen["evidence_count"])
                for moved in scen["links_moved"]:
                    self.assertIn("why", moved)

    def test_every_market_series_round_trips(self):
        for ticker, doc in self.payload["market"].items():
            series = doc.get("series")
            self.assertIsNotNone(series, f"{ticker} lost its series")
            self.assertEqual(series["sampling"], "COMPLETE")
            self.assertEqual(series["inlined_rows"], series["row_count"])
            on_disk = json.loads((self.data / "market" / f"{ticker}.json").read_text())
            rows = (on_disk.get("series") or {}).get("rows") or []
            self.assertEqual(_decode_series(series), rows,
                             f"{ticker}: the compact series does not decode to the file")
            self.assertIn("quality", doc)
            for gone in ("fundamentals", "insider", "prints", "legs"):
                self.assertNotIn(gone, doc, f"{gone} reaches no template")

    def test_impact_carries_legs_and_excerpts(self):
        for appraisal in self.payload["impact"]:
            self.assertTrue(appraisal["legs_inlined"])
            for leg in ("money_at_stake", "public_reach", "capture_odds", "timing_fit"):
                block = appraisal[leg]
                self.assertEqual(len(block.get("evidence") or []), block["evidence_count"])
                self.assertTrue(block.get("rationale") or block.get("basis"))

    def test_append_only_history_is_whole(self):
        seen = 0
        for store in ("signals", "chains", "screens", "stocks", "impact"):
            for doc in self.payload[store]:
                if "changelog_total" in doc:
                    seen += 1
                    self.assertEqual(len(doc["changelog"]), doc["changelog_total"])
        self.assertGreater(seen, 0)

    def test_side_stores_are_whole(self):
        req = self.payload["requests"]
        self.assertEqual(len(req["requests"]), req["total"])
        self.assertEqual(req["total"], SCALE["requests"])
        cands = self.payload["candidates"]
        self.assertEqual(len(cands["candidates"]), cands["total"])
        self.assertEqual(cands["total"], SCALE["candidates"])
        themes = self.payload["themes"]
        self.assertEqual(len(themes["rows"]), themes["total"])
        self.assertEqual(themes["total"], SCALE["occurrences"])
        feeds = self.payload["feeds"]
        self.assertEqual(len(feeds["items"]), feeds["total"])


if __name__ == "__main__":
    unittest.main()
