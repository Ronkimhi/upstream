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
    def run_gate(self, mutate=None, ledger=True, calibration=True):
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
                f"{TODAY} 00:00Z | RUN | run heat test-chain | by: ember | health: 2/2\n"
                if ledger else "")
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


if __name__ == "__main__":
    unittest.main()
