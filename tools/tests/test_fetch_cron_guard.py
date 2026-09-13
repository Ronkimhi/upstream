#!/usr/bin/env python3
"""Each cron-only post-fetch step runs inside its own guard (BUILD item 1, 2026-09-13).

The weekday cron (30 21 * * 1-5) failed on all 10 scheduled runs from 2026-09-01 to
2026-09-11 -- fetching every time, committing NOTHING every time -- because a crash
anywhere in refresh_dive_tickers/eval_indicators propagated out of main() uncaught.
fetch.py's job step then exited non-zero, and .github/workflows/fetch.yml runs no step
after a failed one (the rebuild step is the only one marked continue-on-error; the commit
step is not), so every market file already jdumped to disk that run never reached git.
Two of those bugs were fixed individually (b35f4ce); this is the systemic fix: refresh,
indicators, shadow-sweep and the 30-day prune each run inside `_guarded_cron_step`, so a
THIRD bug in any of them costs one printed line and a health record, never the run.

What each test guards:
  - `_guarded_cron_step` itself: a raising callable is caught, named, and recorded;
    counts["errors"] is incremented; a clean callable leaves no trace.
  - `main()` end to end, is_cron forced True: each of the four steps, forced to raise
    ONE AT A TIME, must not stop the run -- data/requests.json and
    data/health/actions.json both still get written, and the OTHER three steps still ran
    (proven by a side effect, not merely by the absence of a crash).
  - all four raising AT ONCE still leaves a completed, self-describing run: four named
    entries in cron_step_errors, not a crash on the first one.

Run: python3 -m unittest discover -s tools/tests -q
"""
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent


def load_fetch():
    spec = importlib.util.spec_from_file_location(
        "fetch_cron_guard_mod", ROOT / "tools" / "fetch" / "fetch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("requests", type(sys)("requests"))
    spec.loader.exec_module(mod)
    return mod


class TestGuardedCronStepUnit(unittest.TestCase):
    def setUp(self):
        self.mod = load_fetch()

    def test_a_raising_step_is_caught_and_named(self):
        errors, counts = [], {"errors": 0}

        def boom():
            raise KeyError("ticker")
        self.mod._guarded_cron_step("refresh_market_tickers", boom, errors, counts)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["step"], "refresh_market_tickers")
        self.assertIn("ticker", errors[0]["error"])
        self.assertIn("KeyError", errors[0]["error"])
        self.assertIn("at", errors[0])
        self.assertEqual(counts["errors"], 1)

    def test_a_clean_step_leaves_no_trace(self):
        errors, counts = [], {"errors": 0}
        called = []
        self.mod._guarded_cron_step("ok_step", lambda: called.append(1), errors, counts)
        self.assertEqual(called, [1])
        self.assertEqual(errors, [])
        self.assertEqual(counts["errors"], 0)

    def test_never_raises_regardless_of_exception_type(self):
        """Not just RuntimeError -- the exact class of bug that caused the incident was
        a bare KeyError, which is easy to special-case by accident and wrong to."""
        errors, counts = [], {"errors": 0}
        for exc in (KeyError("x"), TypeError("y"), ValueError("z"), ZeroDivisionError()):
            def raiser(e=exc):
                raise e
            self.mod._guarded_cron_step("s", raiser, errors, counts)
        self.assertEqual(len(errors), 4)


class TestCronStepsGuardedInMain(unittest.TestCase):
    """End-to-end: main() itself, is_cron forced True, one real step broken at a time."""

    def setUp(self):
        self.mod = load_fetch()
        self.td = tempfile.TemporaryDirectory()
        self.data = Path(self.td.name) / "data"
        self.mod.DATA = self.data
        # Stub feeds: real feeds.py lives beside fetch.py and is not on sys.path when
        # loaded this way, and feeds is orthogonal to what this file is testing.
        self._feeds_mod = sys.modules.get("feeds")
        stub = types.ModuleType("feeds")
        stub.run_feeds = lambda: {"ok": 0, "new": 0, "held": 0}
        sys.modules["feeds"] = stub
        self._env = mock.patch.dict("os.environ", {"GITHUB_EVENT_NAME": "schedule"})
        self._env.start()
        self._argv = mock.patch.object(sys, "argv", ["fetch.py"])
        self._argv.start()

    def tearDown(self):
        self._argv.stop()
        self._env.stop()
        if self._feeds_mod is None:
            sys.modules.pop("feeds", None)
        else:
            sys.modules["feeds"] = self._feeds_mod
        self.td.cleanup()

    def _run_main(self):
        rc = self.mod.main()
        reqs_path = self.data / "requests.json"
        health_path = self.data / "health" / "actions.json"
        return rc, reqs_path, health_path

    def _latest_run(self, health_path):
        health = json.loads(health_path.read_text())
        return health["fetch"]["runs"][-1]

    def test_a_crash_in_refresh_does_not_stop_the_run(self):
        shadow_called = []
        self.mod.refresh_market_tickers = lambda counts: (_ for _ in ()).throw(
            KeyError("ticker"))
        self.mod.shadow_sweep = lambda: shadow_called.append(1)
        rc, reqs_path, health_path = self._run_main()
        self.assertEqual(rc, 0)
        self.assertTrue(reqs_path.exists(), "requests.json must still be written")
        self.assertTrue(health_path.exists(), "health/actions.json must still be written")
        self.assertEqual(shadow_called, [1], "the NEXT step must still have run")
        run = self._latest_run(health_path)
        steps = {e["step"] for e in run["cron_step_errors"]}
        self.assertIn("refresh_market_tickers", steps)

    def test_a_crash_in_eval_indicators_does_not_stop_the_run(self):
        prune_called = []
        self.mod.eval_indicators = lambda trips: (_ for _ in ()).throw(
            KeyError("indicator"))
        self.mod.prune_fulfilled_requests = lambda reqs: prune_called.append(1)
        rc, reqs_path, health_path = self._run_main()
        self.assertEqual(rc, 0)
        self.assertTrue(reqs_path.exists())
        self.assertEqual(prune_called, [1], "the step AFTER the crash must still have run")
        run = self._latest_run(health_path)
        self.assertIn("eval_indicators", {e["step"] for e in run["cron_step_errors"]})

    def test_a_crash_in_shadow_sweep_does_not_stop_the_run(self):
        self.mod.shadow_sweep = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        rc, reqs_path, health_path = self._run_main()
        self.assertEqual(rc, 0)
        self.assertTrue(reqs_path.exists())
        run = self._latest_run(health_path)
        self.assertIn("shadow_sweep", {e["step"] for e in run["cron_step_errors"]})

    def test_a_crash_in_prune_does_not_stop_the_run(self):
        self.mod.prune_fulfilled_requests = lambda reqs: (_ for _ in ()).throw(
            RuntimeError("boom"))
        rc, reqs_path, health_path = self._run_main()
        self.assertEqual(rc, 0)
        self.assertTrue(reqs_path.exists())
        run = self._latest_run(health_path)
        self.assertIn("prune_fulfilled_requests", {e["step"] for e in run["cron_step_errors"]})

    def test_all_four_crashing_at_once_still_completes_and_names_all_four(self):
        self.mod.refresh_market_tickers = lambda counts: (_ for _ in ()).throw(KeyError("a"))
        self.mod.eval_indicators = lambda trips: (_ for _ in ()).throw(KeyError("b"))
        self.mod.shadow_sweep = lambda: (_ for _ in ()).throw(RuntimeError("c"))
        self.mod.prune_fulfilled_requests = lambda reqs: (_ for _ in ()).throw(RuntimeError("d"))
        rc, reqs_path, health_path = self._run_main()
        self.assertEqual(rc, 0)
        self.assertTrue(reqs_path.exists())
        self.assertTrue(health_path.exists())
        run = self._latest_run(health_path)
        self.assertEqual({e["step"] for e in run["cron_step_errors"]},
                         {"refresh_market_tickers", "eval_indicators", "shadow_sweep",
                          "prune_fulfilled_requests"})

    def test_a_clean_cron_run_records_no_step_errors(self):
        self.mod.refresh_market_tickers = lambda counts: None
        self.mod.eval_indicators = lambda trips: None
        self.mod.shadow_sweep = lambda: None
        self.mod.prune_fulfilled_requests = lambda reqs: None
        rc, reqs_path, health_path = self._run_main()
        self.assertEqual(rc, 0)
        run = self._latest_run(health_path)
        self.assertEqual(run["cron_step_errors"], [])


if __name__ == "__main__":
    unittest.main()
