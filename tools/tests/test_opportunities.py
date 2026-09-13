#!/usr/bin/env python3
"""Tests for tools/opportunities.py, exercised through its gate, tools/check_opportunities.py.

Three synthetic chains, each breaking one thing in exactly one place:

  * alpha has one link with BOTH an issuer basket and a scored price instrument, so the
    fund can outrank the stocks on its own link (method section 8's whole point).
  * beta has a money-corner link with a FINAL INVESTABLE dive (so best_object resolves
    all the way to VERDICT and a real zone), and a second link whose heat is the
    "pending" NULL-with-a-basis shape check_heat.py itself recognises, which must be
    unrankable rather than silently scored zero.
  * gamma has a link with a price instrument but no scored heat.instrument at all, the
    "unrated" fund case, sitting beside an issuer basket that scores normally.

Every test runs the gate via subprocess with `--root` (and `--page` where the point is
to feed it a deliberately wrong `top` block): the gate prints one diagnostic line per
top row (rank, chain/link, expression, size, best stage and ticker, zone_state) and one
line per unrankable or instrument-unrated link, which is what the assertions below read.
`--page` fixtures are built by calling `opportunities.rank()` directly for a real
baseline to mutate; the refusal itself is always observed through the gate's own exit
code and message, never inferred from the mutation alone.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import opportunities  # noqa: E402

CHECK = ROOT / "tools" / "check_opportunities.py"


def gate(root, *extra):
    return subprocess.run(
        [sys.executable, str(CHECK), "--root", str(root), *extra],
        capture_output=True, text=True)


def score(value):
    return {"score": value, "rationale": "fixture rationale",
            "evidence": [{"source_date": "2026-09-01", "url": "https://example.com/x"}]}


def null_score(basis="not yet scored"):
    return {"score": None, "basis": basis}


class Fixture:
    """The real directory layout, built with plain writes rather than the live agents."""

    def __init__(self, td):
        self.root = Path(td)
        for folder in ("chains", "stocks", "companies", "screens", "impact",
                       "mappings", "market", "digest", "campaigns"):
            (self.root / "data" / folder).mkdir(parents=True, exist_ok=True)
        self._build()

    def write(self, rel, obj):
        path = self.root / "data" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, indent=1) + "\n")
        return path

    def read(self, rel):
        return json.loads((self.root / "data" / rel).read_text())

    def rank(self):
        """A real baseline computation, used only to build `--page` fixtures to mutate.
        The tests themselves always assert against the gate's own subprocess output."""
        return opportunities.rank(opportunities.load_inputs(self.root))

    def _build(self):
        # alpha: one link, issuer basket AND a scored price instrument.
        self.write("chains/alpha.json", {
            "id": "alpha", "signal_id": "SIG-ALPHA-01", "title": "Alpha Chain",
            "clock": "COMPOUNDER",
            "links": [{
                "id": "alpha-link", "name": "Alpha Link", "position": 1,
                "investability": "PURE_PLAYS_EXIST",
                "bottleneck": {"criticality": "HIGH"},
                "example_tickers": ["ALPO"],
                "price_instruments": [{"ticker": "XFUT", "exchange": "NYSE Arca",
                                        "kind": "ETF", "holds": "freight futures"}],
                "heat": {
                    "impact": score(90), "crowdedness": score(85), "capture": score(70),
                    "verdict": "OVER_CROWDED", "money_corner": False,
                    "instrument": {"crowdedness": score(30), "capture": score(60),
                                   "verdict": "UNDISCOVERED"},
                },
            }],
        })

        # beta: a money-corner link with a real dive, and a NULL-heat link beside it.
        self.write("chains/beta.json", {
            "id": "beta", "signal_id": "SIG-BETA-01", "title": "Beta Chain",
            "clock": "COMPOUNDER",
            "links": [
                {
                    "id": "beta-link1", "name": "Beta Link One", "position": 1,
                    "investability": "PURE_PLAYS_EXIST",
                    "bottleneck": {"criticality": "CHOKE_POINT"},
                    "example_tickers": ["BETA1"],
                    "heat": {
                        "impact": score(80), "crowdedness": score(30), "capture": score(75),
                        "verdict": "UNDISCOVERED", "money_corner": True,
                    },
                },
                {
                    "id": "beta-link2", "name": "Beta Link Two", "position": 2,
                    "investability": "UNINVESTABLE",
                    "bottleneck": {"criticality": "LOW"},
                    "example_tickers": [],
                    "heat": {
                        "impact": null_score(), "crowdedness": null_score(),
                        "capture": null_score(), "verdict": None, "money_corner": None,
                    },
                },
            ],
        })

        # gamma: a price instrument with no scored heat.instrument (the unrated case).
        self.write("chains/gamma.json", {
            "id": "gamma", "signal_id": "SIG-GAMMA-01", "title": "Gamma Chain",
            "clock": "EVENT",
            "links": [{
                "id": "gamma-link", "name": "Gamma Link", "position": 1,
                "investability": "PARTIAL",
                "bottleneck": {"criticality": "MEDIUM"},
                "example_tickers": ["GAMO"],
                "price_instruments": [{"ticker": "GFUT", "exchange": "CME",
                                        "kind": "Future", "holds": "gamma spot exposure"}],
                "heat": {
                    "impact": score(70), "crowdedness": score(50), "capture": score(60),
                    "verdict": "EMERGING", "money_corner": False,
                },
            }],
        })

        self.write("stocks/BETA1__beta.json", {
            "ticker": "BETA1", "name": "Beta One Inc.", "issuer_id": "BETA-ONE",
            "listing_id": "XNYS-BETA1", "chain_id": "beta", "link_id": "beta-link1",
            "status": "FINAL", "verdict": "INVESTABLE", "clock": "COMPOUNDER",
            "created_at": "2026-09-01", "updated_at": "2026-09-08", "as_of": "2026-09-08",
            "review_by": "2026-12-01",
            "entry_zone": {"low": 10, "high": 20, "basis": "fixture"},
            "would_buy_zone": None, "no_entry_above": 22,
        })

        self.write("market/BETA1.json", {
            "ticker": "BETA1",
            "series": {"interval": "1w", "source": "test",
                       "rows": [["2026-08-25", 12.0], ["2026-09-01", 14.0],
                                ["2026-09-08", 15.0]],
                       "as_of": "2026-09-08"},
            "week52": {"low": 10.0, "high": 22.0},
            "quality": {"reverse_dcf": {"implied_fcf_cagr": 0.021}},
        })
        self.write("market/XFUT.json", {
            "ticker": "XFUT",
            "series": {"interval": "1d", "source": "test",
                       "rows": [["2026-09-01", 50.0], ["2026-09-08", 52.0]],
                       "as_of": "2026-09-08"},
            "quality": {"reverse_dcf": {"implied_fcf_cagr": None}},
        })

        for occ_id, sig_id, band, num in (
            ("IMP-ALPHA-01", "SIG-ALPHA-01", "LEAKY", 55.0),
            ("IMP-BETA-01", "SIG-BETA-01", "PRIME", 70.0),
            ("IMP-GAMMA-01", "SIG-GAMMA-01", "LEAKY", 40.0),
        ):
            self.write(f"impact/{occ_id}.json", {
                "id": occ_id, "occurrence_id": sig_id,
                "impact_score": num, "impact_band": band,
            })

        self.write("campaigns/CAMP-20260901-01.json",
                    {"id": "CAMP-20260901-01", "status": "ACTIVE"})
        # data/digest/ stays empty: an empty digest list is a real, common state.


def _page_path(td, top):
    path = Path(td) / "page.json"
    path.write_text(json.dumps({"top": top}))
    return path


def _row(top, chain_id):
    return next(r for r in top if r["chain_id"] == chain_id)


class TestGateOnACleanFixture(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.fx = Fixture(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_instrument_expression_outranks_issuers_on_the_same_link(self):
        # issuers: 90 * 70 * (100-85) / 1e4 = 9.5; instrument: 90 * 60 * (100-30) / 1e4 = 37.8
        r = gate(self.fx.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("alpha/alpha-link: INSTRUMENT size 37.8, best FUND XFUT", r.stdout)
        self.assertNotIn("alpha/alpha-link: ISSUERS", r.stdout)

    def test_zone_state_uses_the_latest_close(self):
        r = gate(self.fx.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("beta/beta-link1: ISSUERS size 42.0, best VERDICT BETA1, "
                       "zone_state IN_ZONE, pct_above None", r.stdout)

        market = self.fx.read("market/BETA1.json")
        market["series"]["rows"][-1] = ["2026-09-08", 25.0]
        self.fx.write("market/BETA1.json", market)

        r2 = gate(self.fx.root)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("beta/beta-link1: ISSUERS size 42.0, best VERDICT BETA1, "
                       "zone_state ABOVE, pct_above 25.0", r2.stdout)

    def test_every_link_is_ranked_or_counted_unrankable(self):
        r = gate(self.fx.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("4 links across 3 chains; 3 ranked, 1 unrankable, 2 instruments "
                       "(1 rated)", r.stdout)
        self.assertIn("accounted: 4 link(s) = 3 ranked + 1 unrankable; 2 instrument "
                       "link(s), 1 unrated", r.stdout)
        # beta-link2's NULL heat is the one link that cannot rank...
        self.assertIn("unrankable: beta/beta-link2 (NULL heat)", r.stdout)
        # ...and gamma's link, with no scored heat.instrument, is unrated rather than
        # dropped, while its ISSUERS expression still ranks (it appears in top[3]).
        self.assertIn("instrument unrated: gamma/gamma-link (GFUT)", r.stdout)
        self.assertIn("gamma/gamma-link: ISSUERS size 21.0", r.stdout)

    def test_the_gate_always_prints_its_denominator(self):
        r = gate(self.fx.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        first_line = r.stdout.splitlines()[0]
        # printed first, before any FAIL line: the denominator is never conditional.
        for phrase in ("links across", "ranked,", "unrankable,", "instruments", "top:"):
            self.assertIn(phrase, first_line)

    def test_a_clean_fixture_passes(self):
        r = gate(self.fx.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("check_opportunities: OK", r.stdout)


class TestScopeEmpty(unittest.TestCase):
    def test_an_absent_store_is_not_reported_as_a_pass(self):
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "empty"
            (empty / "data").mkdir(parents=True)
            r = gate(empty)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SCOPE EMPTY", r.stdout)
        self.assertNotIn("check_opportunities: OK", r.stdout)


class TestGateRefusesAPage(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.fx = Fixture(self.td.name)
        self.fresh = self.fx.rank()

    def tearDown(self):
        self.td.cleanup()

    def test_the_gate_refuses_a_copied_number_that_disagrees_with_its_source(self):
        page_top = json.loads(json.dumps(self.fresh["top"]))  # deep copy
        row = _row(page_top, "beta")
        self.assertEqual(row["expression"], "ISSUERS")
        row["impact"] = 999  # the chain file on disk still says 80
        page_path = _page_path(self.td.name, page_top)

        r = gate(self.fx.root, "--page", str(page_path))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("differs", r.stdout)
        self.assertIn("impact", r.stdout)

    def test_the_gate_refuses_a_size_computed_from_a_null_score(self):
        page_top = json.loads(json.dumps(self.fresh["top"]))
        row = _row(page_top, "beta")
        row["crowdedness"] = None  # size stays populated: a fabricated NULL-defaulted size
        page_path = _page_path(self.td.name, page_top)

        r = gate(self.fx.root, "--page", str(page_path))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("defaulted NULL", r.stdout)

    def test_the_gate_refuses_a_line_carrying_a_number_the_row_does_not_have(self):
        page_top = json.loads(json.dumps(self.fresh["top"]))
        row = _row(page_top, "beta")
        row["lines"]["why"] = "Moves hard (99 of 100), a number this row never carried."
        page_path = _page_path(self.td.name, page_top)

        r = gate(self.fx.root, "--page", str(page_path))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("lines.why", r.stdout)
        self.assertIn("99", r.stdout)


if __name__ == "__main__":
    unittest.main()
