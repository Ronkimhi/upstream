"""Refusal tests for the Ember heat gate."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from heat_score import band_for, money_corner  # noqa: E402

TODAY = "2026-08-30"


def score(value):
    return {"score": value, "rationale": "Written rationale.",
            "evidence": [{"source_date": TODAY, "url": "https://example.com/evidence"}]}


def chain():
    return {
        "id": "test-chain", "heat_as_of": TODAY,
        "links": [{"id": "input", "heat": {"impact": score(70), "crowdedness": score(30),
                                            "capture": score(70), "verdict": "UNDISCOVERED",
                                            "money_corner": True}},
                  {"id": "output", "heat": {"impact": score(30), "crowdedness": score(90),
                                             "capture": score(40), "verdict": "OVER_CROWDED",
                                             "money_corner": False, "repricing_check": {"legs": []}}},
        ],
        "heat_health": {"examined": 2, "scored": 2, "pending": 0, "errors": 0},
    }


class TestHeatGate(unittest.TestCase):
    def run_gate(self, mutate=None, ledger=True, calibration=True, ledger_text=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("chains", "mappings", "market"):
                (root / "data" / name).mkdir(parents=True, exist_ok=True)
            value = chain()
            if mutate:
                value = mutate(copy.deepcopy(value))
            (root / "data" / "chains" / "test-chain.json").write_text(json.dumps(value))
            (root / "data" / "mappings" / "test-chain.json").write_text("{}")
            (root / "data" / "chains" / "_ember-log.json").write_text(json.dumps(
                {"calibration": {"generated_at": f"{TODAY}T00:00:00Z"}} if calibration else {}))
            (root / "data" / "ledger.md").write_text(
                ledger_text if ledger_text is not None else
                (f"{TODAY} 00:00Z | RUN | run heat test-chain | by: ember | health: 2/2\n" if ledger else ""))
            return subprocess.run([sys.executable, str(ROOT / "tools" / "check_heat.py"),
                                   "--root", str(root), "--date", TODAY],
                                  capture_output=True, text=True)

    def test_clean_campaign_heat_passes(self):
        self.assertEqual(self.run_gate().returncode, 0)

    def test_undated_evidence_fails(self):
        def mutate(value):
            value["links"][0]["heat"]["impact"]["evidence"][0].pop("source_date")
            return value
        result = self.run_gate(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("dated URL evidence", result.stdout)

    def test_computed_fields_fail_when_hand_written(self):
        result = self.run_gate(lambda c: c["links"][0]["heat"].update({"verdict": "QUIET"}) or c)
        self.assertEqual(result.returncode, 1)
        self.assertIn("verdict disagrees", result.stdout)

    def test_crowdedness_sixty_is_crowded_everywhere(self):
        self.assertEqual(band_for(70, 60), "CROWDED")
        self.assertTrue(money_corner(70, 40, 70))
        def mutate(value):
            heat = value["links"][0]["heat"]
            heat["crowdedness"] = score(60)
            heat["verdict"], heat["money_corner"], heat["repricing_check"] = "CROWDED", False, {"legs": []}
            return value
        self.assertEqual(self.run_gate(mutate).returncode, 0)

    def test_crowded_requires_repricing_check(self):
        def mutate(value):
            value["links"][1]["heat"].pop("repricing_check")
            return value
        result = self.run_gate(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs repricing_check", result.stdout)

    def test_missing_postlude_evidence_fails(self):
        self.assertEqual(self.run_gate(ledger=False).returncode, 1)
        self.assertEqual(self.run_gate(calibration=False).returncode, 1)

    def test_a_line_that_only_mentions_run_heat_is_not_the_run_line(self):
        text = (f"{TODAY} 00:00Z | RUN | request data ZZZZ | by: ron | "
                f"result: next: run heat test-chain | health: n/a\n")
        result = self.run_gate(ledger_text=text)
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("no same-day RUN/AMEND ledger line for run heat", result.stdout)

    def test_null_heat_cannot_retain_promoted_opportunity(self):
        def mutate(value):
            for key in ("impact", "crowdedness", "capture"):
                value["links"][0]["heat"][key] = {"score": None, "basis": "No dated source exists."}
            value["heat_health"] = {"examined": 2, "scored": 1, "pending": 1, "errors": 0}
            return value  # retains UNDISCOVERED / true on purpose
        result = self.run_gate(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("must not retain verdict or money_corner", result.stdout)

    def test_all_null_with_basis_is_honest_pending_result(self):
        def mutate(value):
            for key in ("impact", "crowdedness", "capture"):
                value["links"][0]["heat"][key] = {"score": None, "basis": "No dated source exists."}
            value["links"][0]["heat"]["verdict"] = None
            value["links"][0]["heat"]["money_corner"] = None
            value["heat_health"] = {"examined": 2, "scored": 1, "pending": 1, "errors": 0}
            return value
        self.assertEqual(self.run_gate(mutate).returncode, 0)

    def test_health_components_must_reconcile(self):
        result = self.run_gate(lambda value: {**value, "heat_health": {
            "examined": 2, "scored": 2, "pending": 1, "errors": 0}})
        self.assertEqual(result.returncode, 1)

    def test_balanced_but_false_health_fails(self):
        result = self.run_gate(lambda value: {**value, "heat_health": {
            "examined": 2, "scored": 0, "pending": 2, "errors": 0}})
        self.assertEqual(result.returncode, 1)
        self.assertIn("exactly match content buckets", result.stdout)

    def test_calibrator_uses_the_same_content_buckets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "chains").mkdir(parents=True)
            value = chain()
            for key in ("impact", "crowdedness", "capture"):
                value["links"][0]["heat"][key] = {"score": None, "basis": "No dated source exists."}
            value["links"][0]["heat"]["verdict"] = None
            value["links"][0]["heat"]["money_corner"] = None
            value["heat_health"] = {"examined": 2, "scored": 1, "pending": 1, "errors": 0}
            (root / "data" / "chains" / "test-chain.json").write_text(json.dumps(value))
            result = subprocess.run([sys.executable, str(ROOT / "tools" / "ember_calibrate.py"),
                                     "--root", str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            logged = json.loads((root / "data" / "chains" / "_ember-log.json").read_text())
            self.assertEqual(logged["per_chain"]["test-chain"]["heat"],
                             {"examined": 2, "scored": 1, "pending": 1, "errors": 0})
            self.assertTrue(logged["per_chain"]["test-chain"]["stored_heat_health_matches"])


def instrument_score(value):
    return score(value)


class TestInstrumentHeatAndShadowRows(unittest.TestCase):
    """The price-instrument expression and the graded no (method sections 3 and 8, 2026-09-13)."""

    def run_gate(self, mutate=None, market=("FRO",), shadow_rows=None, requests=None, ledger=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("chains", "mappings", "market", "shadow"):
                (root / "data" / name).mkdir(parents=True, exist_ok=True)
            value = chain()
            crowded = value["links"][1]
            crowded["example_tickers"] = ["FRO"]
            if mutate:
                value = mutate(copy.deepcopy(value))
            (root / "data" / "chains" / "test-chain.json").write_text(json.dumps(value))
            (root / "data" / "mappings" / "test-chain.json").write_text("{}")
            for ticker in market:
                (root / "data" / "market" / f"{ticker}.json").write_text(json.dumps(
                    {"ticker": ticker, "series": {"rows": [["2026-08-28", 10.0]], "as_of": TODAY, "source": "test"}}))
            (root / "data" / "shadow" / "book.json").write_text(json.dumps({"rows": shadow_rows or []}))
            (root / "data" / "requests.json").write_text(json.dumps({"version": 1, "requests": requests or []}))
            (root / "data" / "chains" / "_ember-log.json").write_text(json.dumps(
                {"calibration": {"generated_at": f"{TODAY}T00:00:00Z"}}))
            (root / "data" / "ledger.md").write_text(
                f"{TODAY} 00:00Z | RUN | run heat test-chain | by: ember | health: 2/2\n" if ledger else "")
            return subprocess.run([sys.executable, str(ROOT / "tools" / "check_heat.py"),
                                   "--root", str(root), "--date", TODAY],
                                  capture_output=True, text=True)

    @staticmethod
    def shadow_row(ticker="FRO"):
        return {"id": f"SHD-HEAT-test-chain-output-{ticker}-{TODAY}", "ticker": ticker,
                "origin": "HEAT_OVER_CROWDED", "expression": "ISSUER", "chain_id": "test-chain",
                "link_id": "output", "verdict_date": TODAY, "spot": {"value": 10.0}, "review_at": "2026-11-28"}

    @staticmethod
    def with_instrument(value, crowd=30, capture=60, verdict="UNDISCOVERED"):
        link = value["links"][0]
        link["price_instruments"] = [{"ticker": "XFUT", "exchange": "NYSE Arca", "kind": "ETF", "holds": "futures"}]
        link["heat"]["instrument"] = {"as_of": TODAY, "crowdedness": instrument_score(crowd),
                                      "capture": instrument_score(capture), "verdict": verdict,
                                      "ticker_refs": []}
        value["heat_health"]["instruments"] = {"examined": 1, "scored": 1, "pending": 0, "errors": 0}
        return value

    def test_an_over_crowded_link_with_a_priceable_ticker_and_no_shadow_row_fails(self):
        result = self.run_gate()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("OVER_CROWDED with no shadow row", result.stdout)

    def test_an_over_crowded_link_with_its_shadow_row_passes(self):
        result = self.run_gate(shadow_rows=[self.shadow_row()])
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn("1 OVER_CROWDED link(s), 1 with rows", result.stdout)

    def test_an_over_crowded_link_with_no_market_file_needs_a_pending_prices_request(self):
        refused = self.run_gate(market=())
        self.assertEqual(1, refused.returncode, refused.stdout)
        self.assertIn("no priceable ticker and no PENDING prices request", refused.stdout)
        queued = self.run_gate(market=(), requests=[{"id": "REQ-20260830-01", "kind": "prices",
                                                     "ticker": "FRO", "status": "PENDING"}])
        self.assertEqual(0, queued.returncode, queued.stdout)
        self.assertIn("1 pending a price", queued.stdout)

    def test_an_over_crowded_link_with_no_ticker_at_all_is_reported_not_refused(self):
        result = self.run_gate(mutate=lambda v: (v["links"][1].pop("example_tickers"), v)[1])
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn("1 with no ticker", result.stdout)

    def test_price_instruments_without_instrument_heat_fails(self):
        def mutate(value):
            value["links"][0]["price_instruments"] = [{"ticker": "XFUT", "kind": "ETF"}]
            return value
        result = self.run_gate(mutate, shadow_rows=[self.shadow_row()])
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("price_instruments present but heat.instrument missing", result.stdout)

    def test_a_scored_instrument_expression_passes_and_is_counted(self):
        result = self.run_gate(self.with_instrument, shadow_rows=[self.shadow_row()])
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn("instruments: 1 examined, 1 scored", result.stdout)

    def test_an_instrument_verdict_is_computed_never_written(self):
        result = self.run_gate(lambda v: self.with_instrument(v, crowd=85, verdict="UNDISCOVERED"),
                               shadow_rows=[self.shadow_row()])
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("instrument: verdict disagrees with scores (computed OVER_CROWDED)", result.stdout)

    def test_instrument_null_with_basis_is_pending(self):
        def mutate(value):
            value = self.with_instrument(value)
            inst = value["links"][0]["heat"]["instrument"]
            inst["crowdedness"] = {"score": None, "basis": "no market file for XFUT yet"}
            inst["capture"] = {"score": None, "basis": "no market file for XFUT yet"}
            inst["verdict"] = None
            value["heat_health"]["instruments"] = {"examined": 1, "scored": 0, "pending": 1, "errors": 0}
            return value
        result = self.run_gate(mutate, shadow_rows=[self.shadow_row()])
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn("instruments: 1 examined, 0 scored, 1 pending", result.stdout)

    def test_heat_health_instruments_must_match(self):
        def mutate(value):
            value = self.with_instrument(value)
            value["heat_health"]["instruments"] = {"examined": 1, "scored": 0, "pending": 1, "errors": 0}
            return value
        result = self.run_gate(mutate, shadow_rows=[self.shadow_row()])
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("heat_health.instruments must exactly match", result.stdout)

    def test_instrument_heat_on_a_link_without_instruments_fails(self):
        def mutate(value):
            value["links"][0]["heat"]["instrument"] = {"as_of": TODAY, "crowdedness": instrument_score(30),
                                                       "capture": instrument_score(60), "verdict": "UNDISCOVERED"}
            return value
        result = self.run_gate(mutate, shadow_rows=[self.shadow_row()])
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("heat.instrument on a link with no price_instruments", result.stdout)

    def test_an_instrument_scored_today_binds_the_postlude_when_heat_as_of_is_older(self):
        old = "2026-08-01"

        def mutate(value):
            value = self.with_instrument(value)
            value["heat_as_of"] = old
            return value
        old_row = {**self.shadow_row(), "id": f"SHD-HEAT-test-chain-output-FRO-{old}", "verdict_date": old}
        refused = self.run_gate(mutate, shadow_rows=[old_row], ledger=False)
        self.assertEqual(1, refused.returncode, refused.stdout)
        self.assertIn("no same-day RUN/AMEND ledger line for run heat", refused.stdout)
        passed = self.run_gate(mutate, shadow_rows=[old_row])
        self.assertEqual(0, passed.returncode, passed.stdout)
        self.assertIn("instruments: 1 examined, 1 scored", passed.stdout)

    def test_an_older_instrument_block_does_not_touch_the_chain_today(self):
        def mutate(value):
            value = self.with_instrument(value)
            value["heat_as_of"] = "2026-08-01"
            value["links"][0]["heat"]["instrument"]["as_of"] = "2026-08-01"
            return value
        result = self.run_gate(mutate, ledger=False)
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn("NOT RUN TODAY", result.stdout)

    def test_a_heat_run_today_must_rescore_the_instrument(self):
        def mutate(value):
            value = self.with_instrument(value)
            value["links"][0]["heat"]["instrument"]["as_of"] = "2026-08-01"
            return value
        result = self.run_gate(mutate, shadow_rows=[self.shadow_row()])
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("a heat run today must re-score the instrument", result.stdout)


if __name__ == "__main__":
    unittest.main()
