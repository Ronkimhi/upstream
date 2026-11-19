#!/usr/bin/env python3
"""Tests for tools/scorecard.py, the grader of every label against later returns.

Each test builds a small, structurally valid tree in a TemporaryDirectory and runs the
tool as a subprocess, the way fetch.yml runs it. Prices are chosen so every expected
figure can be checked by hand:

  label date 2026-01-10 (close that day is the start), SPY last close 2026-03-01
  SPY   100 -> 105 from 2026-01-11 (+5%)
  AAA   100 -> 110 (+10%)                    excess vs SPY +5%
  B.TO  50 -> 60 (+20%), file B-TO.json      excess vs SPY +15% (dotted ticker, dashed file)
  CCC   on a placement, no market file       excluded NO_SERIES, never filled
  universe = AAA and B.TO, mean +15%

So the 30-day horizon (matures 2026-02-09) is GRADED and the 90-day horizon (matures
2026-04-10) is IMMATURE: graded nothing, reported, never filled.
"""
import datetime
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "tools" / "scorecard.py"
LABEL = "2026-01-10"
RUN_DATE = "2026-03-02"


def run(root, *args, date=RUN_DATE):
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "--date", date, *args],
                          capture_output=True, text=True)


def write(root, rel, obj):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1))


def series(before, after, start="2026-01-01", end="2026-03-01"):
    d0, d1 = datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)
    rows, d = [], d0
    while d <= d1:
        rows.append([d.isoformat(), before if d.isoformat() <= LABEL else after])
        d += datetime.timedelta(days=1)
    return {"ticker": "x", "series": {"interval": "1d", "rows": rows}}


def heat(verdict, imp=70, crowd=30, cap=60, mc=False):
    return {"impact": {"score": imp}, "crowdedness": {"score": crowd}, "capture": {"score": cap},
            "verdict": verdict, "money_corner": mc, "as_of": LABEL}


def build(root):
    write(root, "data/market/SPY.json", series(100, 105))
    write(root, "data/market/AAA.json", series(100, 110))
    write(root, "data/market/B-TO.json", series(50, 60))
    write(root, "data/chains/alpha.json", {"id": "alpha", "heat_as_of": LABEL, "links": [
        {"id": "l1", "example_tickers": [], "heat": heat("UNDISCOVERED", mc=True)},
        {"id": "l2", "example_tickers": ["AAA"], "heat": heat("CROWDED", crowd=80)},
        {"id": "l3", "example_tickers": [], "heat": {"verdict": None}},
    ], "scenarios": [{"id": "S1", "probability_pct": 40, "status": "OPEN"}]})
    write(root, "data/mappings/alpha.json", {"chain_id": "alpha", "listings": [
        {"issuer_id": "A", "ticker": "AAA", "exchange": "NYSE", "market_ticker": "AAA"},
        {"issuer_id": "B", "ticker": "B.TO", "exchange": "TSX", "market_ticker": "B.TO"},
        {"issuer_id": "C", "ticker": "CCC", "exchange": "NYSE"},
    ], "placements": [
        {"link_id": "l1", "issuer_id": "A", "status": "ACTIVE"},
        {"link_id": "l1", "issuer_id": "B", "status": "ACTIVE"},
        {"link_id": "l1", "issuer_id": "C", "status": "ACTIVE"},
    ]})
    write(root, "data/companies/B.json", {"issuer_id": "B", "opportunity_tier": "O1",
                                          "created_at": LABEL + "T00:00:00Z", "changelog": [],
                                          "listings": [{"ticker": "B.TO", "exchange": "TSX",
                                                        "market_ticker": "B.TO"}]})
    write(root, "data/stocks/AAA__alpha.json", {"ticker": "AAA", "issuer_id": "A", "verdict": "WATCH",
                                                "clock": "COMPOUNDER", "created_at": LABEL + "T00:00:00Z",
                                                "changelog": []})
    write(root, "data/signals/SIG-20260101-01.json", {"id": "SIG-20260101-01", "chain_id": "alpha"})
    write(root, "data/impact/IMP-1.json", {"id": "IMP-1", "occurrence_id": "SIG-20260101-01",
                                           "impact_band": "PRIME", "impact_score": 80, "as_of": LABEL})
    write(root, "data/impact/IMP-2.json", {"id": "IMP-2", "occurrence_id": "CAND-20260101-01",
                                           "impact_band": "LEAKY", "impact_score": 40, "as_of": LABEL})


def calls(root):
    return json.loads((Path(root) / "data/scorecard/calls.json").read_text())["calls"]


def card(root):
    return json.loads((Path(root) / "data/scorecard/scorecard.json").read_text())


def call_for(root, ref):
    return [c for c in calls(root) if c["object_ref"] == ref]


class TestScorecard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_backfills_and_grades_by_hand(self):
        build(self.root)
        r = run(self.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cs = calls(self.root)
        self.assertTrue(all(c["provenance"] == "BACKFILL" for c in cs))
        # 2 labelled links + 1 tier + 1 dive + 1 tied impact; l3 unlabeled, IMP-2 untied
        self.assertEqual(sorted(c["origin"] for c in cs), ["DIVE", "HEAT", "HEAT", "IMPACT", "TIER"])
        c = card(self.root)
        self.assertEqual(c["origins"]["IMPACT"]["untied"], {"OCCURRENCE_NOT_A_SIGNAL": 1})
        self.assertEqual(c["origins"]["HEAT"]["unlabeled"], 1)
        und = c["origins"]["HEAT"]["labels"]["UNDISCOVERED"]["h30"]
        self.assertEqual(und["status"], "GRADED")
        # call level = mean(+5%, +15%) vs SPY; vs universe (+15%): mean(-5%, +5%)
        self.assertEqual(und["call_level"]["vs_spy"]["median_pct"], 10.0)
        self.assertEqual(und["call_level"]["vs_universe"]["median_pct"], 0.0)
        self.assertEqual(und["ticker_level"]["vs_spy"]["n"], 2)
        dive = c["origins"]["DIVE"]["labels"]["WATCH"]["h30"]
        self.assertEqual(dive["call_level"]["vs_spy"]["median_pct"], 5.0)
        self.assertEqual(dive["call_level"]["vs_spy"]["hit_rate_pct"], 100.0)
        self.assertEqual(c["scenarios"]["unscorable"], 1)
        self.assertIn("examined", r.stdout)
        self.assertIn("graded", r.stdout)
        self.assertIn("interim", r.stdout)
        self.assertIn("excluded", r.stdout)

    def test_changed_label_appends_and_never_edits(self):
        build(self.root)
        self.assertEqual(run(self.root).returncode, 0)
        before = calls(self.root)
        # an unchanged re-run adds nothing
        self.assertEqual(run(self.root, date="2026-03-03").returncode, 0)
        self.assertEqual(calls(self.root), before)
        # Ember re-scores l1: UNDISCOVERED becomes EMERGING with new scores
        chain = json.loads((self.root / "data/chains/alpha.json").read_text())
        chain["links"][0]["heat"] = heat("EMERGING", crowd=55)
        write(self.root, "data/chains/alpha.json", chain)
        r = run(self.root, date="2026-03-04")
        self.assertEqual(r.returncode, 0, r.stdout)
        after = calls(self.root)
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(after[:len(before)], before)          # old calls byte-for-byte
        new = after[-1]
        self.assertEqual((new["label"], new["provenance"], new["label_date"]),
                         ("EMERGING", "OBSERVED", "2026-03-04"))
        l1 = call_for(self.root, "data/chains/alpha.json#l1")
        self.assertEqual([c["label"] for c in l1], ["UNDISCOVERED", "EMERGING"])
        graded = {x["id"]: x for x in card(self.root)["calls"]}
        self.assertEqual(graded[l1[0]["id"]]["superseded_by"], l1[1]["id"])
        # the superseded label is still graded
        self.assertEqual(graded[l1[0]["id"]]["horizons"]["h30"]["status"], "GRADED")

    def test_missing_series_is_excluded_with_reason_never_filled(self):
        build(self.root)
        self.assertEqual(run(self.root).returncode, 0)
        l1 = call_for(self.root, "data/chains/alpha.json#l1")[0]
        self.assertEqual(l1["start_prices"]["CCC"], {"excluded": "NO_SERIES"})
        h = card(self.root)["origins"]["HEAT"]["labels"]["UNDISCOVERED"]["h30"]
        self.assertEqual(h["excluded_by_reason"], {"NO_SERIES": 1})
        self.assertEqual(h["n_tickers"], 2)      # AAA and B.TO only, CCC never priced

    def test_immature_horizon_is_never_graded(self):
        build(self.root)
        self.assertEqual(run(self.root).returncode, 0)
        c = card(self.root)
        lab = c["origins"]["HEAT"]["labels"]["UNDISCOVERED"]
        self.assertEqual(lab["h90"]["status"], "NOT_MATURED")
        self.assertEqual(lab["h90"]["n_graded_calls"], 0)
        self.assertIsNone(lab["h90"]["call_level"]["vs_spy"]["median_pct"])
        self.assertEqual(lab["to_date"]["status"], "INTERIM")
        one = c["calls"][0]["horizons"]
        self.assertEqual(one["h90"]["status"], "IMMATURE")
        self.assertEqual(one["h90"]["matures_on"], "2026-04-10")
        self.assertNotIn("x_spy_pct", one["h90"])

    def test_dotted_ticker_resolves_to_dashed_file(self):
        build(self.root)
        self.assertEqual(run(self.root).returncode, 0)
        tier = call_for(self.root, "data/companies/B.json")[0]
        self.assertEqual(tier["tickers"][0]["stem"], "B-TO")
        self.assertEqual(tier["start_prices"]["B.TO"], {"date": LABEL, "close": 50.0})
        h = card(self.root)["origins"]["TIER"]["labels"]["O1"]["h30"]
        self.assertEqual(h["call_level"]["vs_spy"]["median_pct"], 15.0)

    def test_empty_tree_grades_nothing_and_says_so(self):
        r = run(self.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("NOTHING GRADED", r.stdout)
        self.assertNotIn("OK", r.stdout)
        self.assertFalse((self.root / "data/scorecard").exists())

    def test_refuses_an_edited_call_and_writes_nothing(self):
        build(self.root)
        self.assertEqual(run(self.root).returncode, 0)
        path = self.root / "data/scorecard/calls.json"
        doc = json.loads(path.read_text())
        doc["calls"][0]["label"] = "QUIET"        # rewrite history after the fact
        path.write_text(json.dumps(doc, indent=1))
        frozen = path.read_text()
        card_before = (self.root / "data/scorecard/scorecard.json").read_text()
        result = run(self.root, date="2026-03-05")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("REFUSED", result.stdout)
        self.assertEqual(path.read_text(), frozen)
        self.assertEqual((self.root / "data/scorecard/scorecard.json").read_text(), card_before)

    def test_refuses_a_corrupt_store(self):
        build(self.root)
        path = self.root / "data/scorecard/calls.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        result = run(self.root)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(path.read_text(), "{not json")

    def test_check_fails_when_a_committed_call_is_dropped(self):
        build(self.root)
        self.assertEqual(run(self.root).returncode, 0)
        git = ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-qm", "seed"], check=True)
        ok = subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root), "--check"],
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout)
        path = self.root / "data/scorecard/calls.json"
        doc = json.loads(path.read_text())
        doc["calls"].pop(0)                       # drop a graded call, ids still valid
        path.write_text(json.dumps(doc, indent=1))
        result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("FAIL", result.stdout)
        # and the writer refuses to append over the same violation
        writer = run(self.root, date="2026-03-05")
        self.assertEqual(writer.returncode, 2, writer.stdout)


if __name__ == "__main__":
    unittest.main()
