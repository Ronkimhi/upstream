"""Refusal tests for the Ember scenario gate."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TODAY = "2026-08-30"


def scenario(number, probability):
    return {"id": f"S{number}", "title": f"Case {number}", "narrative": "A distinct outcome.",
            "probability_pct": probability,
            "links_moved": [{"link_id": "input", "direction": "UP", "magnitude": "MEDIUM",
                               "why": "Demand rises."}],
            "leading_indicators": [{"name": "Orders"}, {"name": "Capacity"}],
            "invalidation_signs": ["Orders fail to rise"], "status": "OPEN"}


def chain():
    return {"id": "test-chain", "scenarios_as_of": TODAY,
            "links": [{"id": "input"}], "scenarios": [scenario(1, 35), scenario(2, 35), scenario(3, 30)],
            "scenario_health": {"examined": 3, "scored": 3, "pending": 0, "errors": 0}}


class TestScenarioGate(unittest.TestCase):
    def run_gate(self, mutate=None, ledger=True, calibration=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "chains").mkdir(parents=True)
            (root / "data" / "mappings").mkdir(parents=True)
            value = chain()
            if mutate:
                value = mutate(copy.deepcopy(value))
            (root / "data" / "chains" / "test-chain.json").write_text(json.dumps(value))
            (root / "data" / "mappings" / "test-chain.json").write_text("{}")
            (root / "data" / "chains" / "_ember-log.json").write_text(json.dumps(
                {"calibration": {"generated_at": f"{TODAY}T00:00:00Z"}} if calibration else {}))
            (root / "data" / "ledger.md").write_text(
                f"{TODAY} 00:00Z | RUN | run scenarios test-chain | by: ember | health: 3/3\n"
                if ledger else "")
            return subprocess.run([sys.executable, str(ROOT / "tools" / "check_scenarios.py"),
                                   "--root", str(root), "--date", TODAY],
                                  capture_output=True, text=True)

    def test_clean_campaign_scenarios_pass(self):
        self.assertEqual(self.run_gate().returncode, 0)

    def test_probability_and_link_fail(self):
        def mutate(value):
            value["scenarios"][0]["probability_pct"] = 10
            value["scenarios"][0]["links_moved"][0]["link_id"] = "missing"
            return value
        result = self.run_gate(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("outside 90-110", result.stdout)
        self.assertIn("no real link", result.stdout)

    def test_armed_check_requires_shape(self):
        def mutate(value):
            value["scenarios"][0]["leading_indicators"][0] = {"name": "Price", "armed": True}
            return value
        result = self.run_gate(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("armed indicator lacks check", result.stdout)

    def test_check_must_be_armed_and_supported(self):
        def mutate(value):
            value["scenarios"][0]["leading_indicators"][0] = {
                "name": "Price",
                "armed": False,
                "check": {"type": "ratio", "ticker": "vrt", "op": "GT", "level": "70"},
            }
            return value
        result = self.run_gate(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("check exists but armed is not true", result.stdout)
        self.assertIn("supported type, ticker, op, and finite numeric level", result.stdout)

    def test_supported_armed_price_check_passes(self):
        def mutate(value):
            value["scenarios"][0]["leading_indicators"][0] = {
                "name": "Price",
                "armed": True,
                "check": {"type": "price", "ticker": "VRT", "op": ">=", "level": 70.0},
            }
            return value
        self.assertEqual(self.run_gate(mutate).returncode, 0)

    def test_check_rejects_null_and_nonfinite_levels(self):
        for level in (None, "70", float("nan"), float("inf")):
            def mutate(value, level=level):
                value["scenarios"][0]["leading_indicators"][0] = {
                    "name": "Price", "armed": True,
                    "check": {"type": "price", "ticker": "VRT", "op": ">=", "level": level},
                }
                return value
            with self.subTest(level=level):
                result = self.run_gate(mutate)
                self.assertEqual(result.returncode, 1)
                self.assertIn("finite numeric level", result.stdout)

    def test_missing_postlude_evidence_fails(self):
        self.assertEqual(self.run_gate(ledger=False).returncode, 1)
        self.assertEqual(self.run_gate(calibration=False).returncode, 1)

    def test_health_components_must_reconcile(self):
        result = self.run_gate(lambda value: {**value, "scenario_health": {
            "examined": 3, "scored": 3, "pending": 1, "errors": 0}})
        self.assertEqual(result.returncode, 1)

    def test_balanced_but_false_health_fails(self):
        result = self.run_gate(lambda value: {**value, "scenario_health": {
            "examined": 3, "scored": 0, "pending": 3, "errors": 0}})
        self.assertEqual(result.returncode, 1)
        self.assertIn("exactly match content buckets", result.stdout)


if __name__ == "__main__":
    unittest.main()
