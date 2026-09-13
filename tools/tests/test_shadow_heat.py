#!/usr/bin/env python3
"""Exact tests for the OVER_CROWDED shadow-book writer.

Every test breaks or inspects a STRUCTURALLY VALID fixture in one specific place, the same
idiom tools/tests/test_themes.py uses: a minimal chain, a minimal mapping, minimal market
files, and an empty shadow book, all built fresh per test in a TemporaryDirectory so no
test depends on another's leftovers.

The fixture chain "alpha" carries four links: l1 is OVER_CROWDED with six example
tickers (one, FFF, has no market file), one INSTRUMENT-tagged futures ticker, and a
mapping placement that adds a seventh candidate, GGG; l2 is merely CROWDED, so it must
produce nothing; l3 is OVER_CROWDED but names no ticker at all, the no_tickers case; l4 is
OVER_CROWDED with its one candidate carried entirely by a mapping placement whose listing
has both a bare ticker and a market_ticker, proving the latter wins.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "tools" / "shadow_heat.py"

HEAT_AS_OF = "2026-08-30"
REQUIRED_SHADOW_KEYS = {"id", "ticker", "origin", "verdict_date", "spot", "review_at"}


def run(root, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True, text=True)


def heat_block(verdict, crowdedness_score):
    return {
        "impact": {"score": 70, "rationale": "x", "evidence": []},
        "crowdedness": {"score": crowdedness_score, "rationale": "x", "evidence": []},
        "capture": {"score": 60, "rationale": "x", "evidence": []},
        "verdict": verdict,
        "money_corner": False,
        "as_of": HEAT_AS_OF,
    }


class Fixture:
    def __init__(self, td):
        self.root = Path(td)
        (self.root / "data" / "chains").mkdir(parents=True)
        (self.root / "data" / "mappings").mkdir(parents=True)
        (self.root / "data" / "market").mkdir(parents=True)
        (self.root / "data" / "shadow").mkdir(parents=True)

        chain = {
            "id": "alpha",
            "heat_as_of": HEAT_AS_OF,
            "links": [
                {
                    "id": "l1",
                    "name": "Link One",
                    "example_tickers": ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
                    "price_instruments": [{"ticker": "XFUT", "expression": "INSTRUMENT"}],
                    "heat": heat_block("OVER_CROWDED", 91),
                },
                {
                    "id": "l2",
                    "name": "Link Two",
                    "example_tickers": ["ZZZ"],
                    "heat": heat_block("CROWDED", 65),
                },
                {
                    "id": "l3",
                    "name": "Link Three",
                    "example_tickers": [],
                    "heat": heat_block("OVER_CROWDED", 95),
                },
                {
                    "id": "l4",
                    "name": "Link Four",
                    "example_tickers": [],
                    "heat": heat_block("OVER_CROWDED", 88),
                },
            ],
        }
        self.write("data/chains/alpha.json", chain)

        mapping = {
            "id": "alpha",
            "chain_id": "alpha",
            "status": "ACTIVE",
            "listings": [
                {"listing_id": "X-GGG", "issuer_id": "G-ISSUER", "ticker": "GGG"},
                # A foreign-exchange listing where the bare ticker is not what any market
                # file is keyed by: tools/book.py's _placements() prefers market_ticker
                # for exactly this reason, and this tool must too.
                {"listing_id": "X-HHH", "issuer_id": "H-ISSUER", "ticker": "BA.",
                 "market_ticker": "hhh-suffixed"},
            ],
            "placements": [
                {"chain_id": "alpha", "link_id": "l1", "issuer_id": "G-ISSUER",
                 "status": "ACTIVE"},
                {"chain_id": "alpha", "link_id": "l4", "issuer_id": "H-ISSUER",
                 "status": "ACTIVE"},
            ],
        }
        self.write("data/mappings/alpha.json", mapping)

        self.write("data/market/HHH-SUFFIXED.json", {
            "ticker": "HHH-SUFFIXED",
            "series": {"interval": "1d", "source": "yfinance", "as_of": "2026-09-01",
                      "rows": [["2026-08-28", 5.0], ["2026-09-01", 5.5]]},
        })

        # AAA..EEE, GGG, XFUT all carry a close on 2026-08-28 (on or before heat_as_of)
        # and a later one on 2026-09-01 (after it), so the close-on-or-before rule has
        # something real to pick between. FFF gets no market file at all.
        for i, t in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE", "GGG", "XFUT"]):
            base = 10.0 * (i + 1)
            self.write(f"data/market/{t}.json", {
                "ticker": t,
                "series": {"interval": "1d", "source": "yfinance", "as_of": "2026-09-01",
                          "rows": [["2026-08-28", base], ["2026-09-01", base + 1]]},
            })

        self.write("data/shadow/book.json", {"rows": []})

        self.write("data/requests.json", {"version": 1, "requests": [
            {"id": "REQ-20260101-01", "kind": "prices", "ticker": "PRIOR", "query": None,
             "forms": None, "lookback_days": None, "requested_by": "seed",
             "requested_at": "2026-01-01T00:00:00+00:00", "by": "ron",
             "status": "FULFILLED", "note": None}]})

    def write(self, rel, obj):
        (self.root / rel).write_text(json.dumps(obj, indent=1) + "\n")

    def read(self, rel):
        return json.loads((self.root / rel).read_text())

    def raw(self, rel):
        return (self.root / rel).read_text()


class TestShadowHeat(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.fx = Fixture(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_rows_are_written_once_and_a_second_run_adds_nothing(self):
        r1 = run(self.fx.root, "alpha", "--date", HEAT_AS_OF)
        self.assertEqual(0, r1.returncode, r1.stdout + r1.stderr)
        self.assertIn("added 7", r1.stdout)
        rows1 = self.fx.read("data/shadow/book.json")["rows"]
        self.assertEqual(7, len(rows1))
        for row in rows1:
            self.assertTrue(REQUIRED_SHADOW_KEYS <= set(row),
                            f"{row.get('id')} is missing one of {REQUIRED_SHADOW_KEYS}")

        r2 = run(self.fx.root, "alpha", "--date", HEAT_AS_OF)
        self.assertEqual(0, r2.returncode, r2.stdout + r2.stderr)
        self.assertIn("added 0", r2.stdout)
        rows2 = self.fx.read("data/shadow/book.json")["rows"]
        self.assertEqual([row["id"] for row in rows1], [row["id"] for row in rows2],
                         "a re-run must not duplicate or reorder what is already on disk")

    def test_instrument_rows_are_never_capped_and_issuers_cap_at_five(self):
        r = run(self.fx.root, "alpha", "--date", HEAT_AS_OF)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        rows = self.fx.read("data/shadow/book.json")["rows"]
        l1_rows = [row for row in rows if row["link_id"] == "l1"]

        instrument_tickers = {row["ticker"] for row in l1_rows if row["expression"] == "INSTRUMENT"}
        self.assertEqual({"XFUT"}, instrument_tickers)

        issuer_tickers = [row["ticker"] for row in l1_rows if row["expression"] == "ISSUER"]
        self.assertEqual(5, len(issuer_tickers))
        self.assertEqual(["AAA", "BBB", "CCC", "DDD", "EEE"], issuer_tickers,
                         "example_tickers keep their given order and the cap falls after "
                         "the fifth of them, before the mapping-derived GGG is ever reached")
        self.assertNotIn("GGG", issuer_tickers)

    def test_spot_is_the_close_on_or_before_the_heat_date_not_the_latest(self):
        r = run(self.fx.root, "alpha", "--date", HEAT_AS_OF)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        rows = self.fx.read("data/shadow/book.json")["rows"]
        aaa = next(row for row in rows if row["ticker"] == "AAA")
        self.assertEqual(10.0, aaa["spot"]["value"],
                         "must be the 2026-08-28 close, not the 2026-09-01 close of 11.0")
        self.assertEqual("2026-08-28", aaa["spot"]["as_of"])
        self.assertEqual(HEAT_AS_OF, aaa["verdict_date"])
        self.assertIn("close on/before 2026-08-30", aaa["spot"]["source"])

    def test_a_deferred_ticker_is_printed_and_request_queues_exactly_one_prices_row(self):
        r = run(self.fx.root, "alpha", "--date", HEAT_AS_OF, "--request")
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("deferred 1 ticker(s): [FFF]", r.stdout)
        self.assertIn("requests queued 1", r.stdout)

        rows = self.fx.read("data/shadow/book.json")["rows"]
        self.assertFalse(any(row["ticker"] == "FFF" for row in rows),
                         "a ticker with no market file gets no row")

        reqs = self.fx.read("data/requests.json")["requests"]
        fff_rows = [r_ for r_ in reqs if r_.get("ticker") == "FFF"]
        self.assertEqual(1, len(fff_rows))
        self.assertEqual("prices", fff_rows[0]["kind"])
        self.assertEqual("PENDING", fff_rows[0]["status"])
        self.assertEqual("routine", fff_rows[0]["by"])

        # A second run the same day must not queue FFF again.
        r2 = run(self.fx.root, "alpha", "--date", HEAT_AS_OF, "--request")
        self.assertEqual(0, r2.returncode, r2.stdout + r2.stderr)
        self.assertIn("requests queued 0", r2.stdout)
        reqs2 = self.fx.read("data/requests.json")["requests"]
        self.assertEqual(1, len([r_ for r_ in reqs2 if r_.get("ticker") == "FFF"]))

    def test_a_crowded_link_gets_no_row_and_a_link_with_no_tickers_is_reported(self):
        r = run(self.fx.root, "alpha", "--date", HEAT_AS_OF)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        rows = self.fx.read("data/shadow/book.json")["rows"]
        self.assertFalse(any(row["link_id"] == "l2" for row in rows),
                         "a merely CROWDED link must never be shadowed")
        self.assertFalse(any(row["link_id"] == "l3" for row in rows))
        self.assertIn("no_tickers 1 link(s): [alpha/l3]", r.stdout)

    def test_indentation_and_existing_rows_are_preserved(self):
        prior_row = {"id": "SHD-PRIOR-1", "ticker": "QQQ", "origin": "DIVE_TOO_LATE",
                    "verdict_date": "2026-01-01",
                    "spot": {"value": 1.0, "source": "seed", "as_of": "2026-01-01"},
                    "review_at": "2026-04-01"}
        seed_text = json.dumps({"rows": [prior_row]}, indent=1) + "\n"
        (self.fx.root / "data" / "shadow" / "book.json").write_text(seed_text)
        # The row's own serialized text, exactly as it sits in the seeded file: everything
        # between the array's opening bracket and its close, before any sibling exists.
        row_text = seed_text[seed_text.index("[\n") + len("[\n"):seed_text.rindex("\n ]")]
        self.assertIn(row_text, seed_text)  # sanity on the slice itself

        r = run(self.fx.root, "alpha", "--date", HEAT_AS_OF)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

        after_text = self.fx.raw("data/shadow/book.json")
        self.assertIn(row_text, after_text,
                     "the pre-existing row's exact bytes must survive the append untouched")
        self.assertTrue(after_text.startswith('{\n "rows": [\n  {\n   "id": "SHD-PRIOR-1"'),
                        after_text[:60])
        rows = self.fx.read("data/shadow/book.json")["rows"]
        self.assertEqual("SHD-PRIOR-1", rows[0]["id"],
                         "the pre-existing row must stay first; new rows are appended")

    def test_dry_run_writes_nothing(self):
        book_path = self.fx.root / "data" / "shadow" / "book.json"
        req_path = self.fx.root / "data" / "requests.json"
        book_before = book_path.read_text()
        req_before = req_path.read_text()

        r = run(self.fx.root, "alpha", "--date", HEAT_AS_OF, "--request", "--dry-run")
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("added 7", r.stdout)
        self.assertIn("requests queued 1", r.stdout)

        self.assertEqual(book_before, book_path.read_text())
        self.assertEqual(req_before, req_path.read_text())

    def test_a_mapping_listing_prefers_market_ticker_over_the_bare_ticker(self):
        """H-ISSUER's listing carries ticker "BA." (a bare local exchange code, the kind
        that is one character from reading as Boeing's own NYSE ticker) and market_ticker
        "hhh-suffixed" (upper-cased to match the market file, HHH-SUFFIXED.json, the way
        every other issuer ticker in this file already arrives upper-cased). Only the
        market_ticker form may end up on disk or in the deferred list."""
        r = run(self.fx.root, "alpha", "--date", HEAT_AS_OF)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        rows = self.fx.read("data/shadow/book.json")["rows"]
        l4_rows = [row for row in rows if row["link_id"] == "l4"]
        self.assertEqual(["HHH-SUFFIXED"], [row["ticker"] for row in l4_rows])
        self.assertEqual("ISSUER", l4_rows[0]["expression"])
        self.assertNotIn("BA.", [row["ticker"] for row in rows])
        self.assertNotIn("BA.", r.stdout)


class TestRowProvenance(unittest.TestCase):
    """A row added after the call it grades says so (2026-09-13). The shadow book grades the
    machine's no from the call's own price, so a row written two weeks later was written with
    that fortnight's move in view, and a reader of its grade has to be able to see that."""

    def run_tool(self, date):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for folder in ("chains", "market", "shadow", "mappings"):
            (root / "data" / folder).mkdir(parents=True)
        chain = {"id": "beta", "heat_as_of": "2026-08-30", "links": [{
            "id": "l1", "name": "Link one", "example_tickers": ["AAA"],
            "heat": {"verdict": "OVER_CROWDED", "crowdedness": {"score": 84}}}]}
        (root / "data" / "chains" / "beta.json").write_text(json.dumps(chain))
        (root / "data" / "mappings" / "beta.json").write_text("{}")
        (root / "data" / "market" / "AAA.json").write_text(json.dumps(
            {"series": {"rows": [["2026-08-28", 10.0], ["2026-09-11", 17.0]], "source": "test"}}))
        (root / "data" / "shadow" / "book.json").write_text(json.dumps({"rows": []}, indent=1) + "\n")
        (root / "data" / "requests.json").write_text(json.dumps({"version": 1, "requests": []}, indent=1) + "\n")
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "shadow_heat.py"), "beta",
                                 "--root", str(root), "--date", date], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return json.loads((root / "data" / "shadow" / "book.json").read_text())["rows"][0]

    def test_a_row_written_after_the_call_says_so(self):
        row = self.run_tool("2026-09-13")
        self.assertEqual("2026-09-13", row["written_at"])
        self.assertIn("row written 2026-09-13, after the call", row["note"])
        self.assertEqual(10.0, row["spot"]["value"])

    def test_a_row_written_on_the_call_day_carries_no_hindsight_note(self):
        row = self.run_tool("2026-08-30")
        self.assertEqual("2026-08-30", row["written_at"])
        self.assertNotIn("after the call", row["note"])


class TestInstrumentRecordShape(unittest.TestCase):
    """Atlas's price_instruments records carry no `expression` key, which method section 4 never
    defines, and the tool used to skip every instrument without one, so BWET, the first real
    instrument, got no shadow row (2026-09-13)."""

    def test_an_instrument_without_an_expression_key_still_gets_a_row(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for folder in ("chains", "market", "shadow", "mappings"):
            (root / "data" / folder).mkdir(parents=True)
        chain = {"id": "gamma", "heat_as_of": "2026-08-30", "links": [{
            "id": "l1", "name": "Link one", "example_tickers": ["AAA"],
            "price_instruments": [{"ticker": "XFUT", "exchange": "NYSE Arca", "kind": "ETF",
                                   "holds": "freight futures"}],
            "heat": {"verdict": "OVER_CROWDED", "crowdedness": {"score": 84}}}]}
        (root / "data" / "chains" / "gamma.json").write_text(json.dumps(chain))
        (root / "data" / "mappings" / "gamma.json").write_text("{}")
        for ticker in ("AAA", "XFUT"):
            (root / "data" / "market" / f"{ticker}.json").write_text(json.dumps(
                {"series": {"rows": [["2026-08-28", 10.0]], "source": "test"}}))
        (root / "data" / "shadow" / "book.json").write_text(json.dumps({"rows": []}, indent=1) + "\n")
        (root / "data" / "requests.json").write_text(json.dumps({"version": 1, "requests": []}, indent=1) + "\n")
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "shadow_heat.py"), "gamma",
                                 "--root", str(root), "--date", "2026-09-13"], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        rows = json.loads((root / "data" / "shadow" / "book.json").read_text())["rows"]
        self.assertEqual({("XFUT", "INSTRUMENT"), ("AAA", "ISSUER")},
                         {(row["ticker"], row["expression"]) for row in rows})


if __name__ == "__main__":
    unittest.main()
