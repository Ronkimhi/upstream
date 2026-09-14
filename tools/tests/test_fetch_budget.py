#!/usr/bin/env python3
"""The request-row loop's wall-clock budget, priority order, and self-chaining
(BUILD 2026-09-14).

data/requests.json on origin held 673 PENDING rows, requested in one 2h10m window
(pcs 267, web_doc 133, edgar_doc 109, prices 65, fundamentals 60, quality 39). The next
8 push-triggered runs all hit the 30-minute job timeout (.github/workflows/fetch.yml
timeout-minutes) walking that queue in plain file order -- pcs sources throttle and
web_doc alone carries a 45s-per-request ceiling, so one run can never be the whole
queue. A hard job timeout kills the runner outright before the commit step, so
everything those 8 runs fetched was thrown away. No run started again after that,
because a push made with GITHUB_TOKEN does not retrigger this workflow's own `push:`
trigger, and the weekday cron does not fire until 21:30Z.

Three fixes, three test classes:
  - TestOrderDueRequests: the pure priority sort (prices, fundamentals, quality,
    edgar_doc, insider, edgar_fts, web_doc, pcs; oldest requested_at first within a
    kind). Grouping strictly by kind is what gives "quality only after its own
    ticker's fundamentals" for free -- every fundamentals row, any ticker, sorts
    before every quality row, any ticker -- so this file also proves that with an
    adversarial ordering (a quality row requested, and listed, before its own
    ticker's fundamentals row).
  - TestDispatchDecision / TestDispatchNextRunUnit: the pure re-dispatch decision (a
    successor is queued only when PENDING rows remain AND this run actually
    transitioned at least one row's status -- never on "PENDING rows exist" alone,
    which would self-dispatch forever on a run that can make no progress at all) and
    the subprocess call it gates, with subprocess.run always faked so no test can
    ever shell out to a real `gh` binary.
  - TestRequestRowBudget / TestSelfDispatchInMain: main() end to end, a fake
    monotonic clock standing in for wall-clock time exactly as
    test_fetch_cron.py's price-refresh tests do. Proves a row the budget did not
    reach is left PENDING with its `attempts` untouched, and that main() only calls
    dispatch_next_run when the pure decision says to.

Run: python3 -m unittest discover -s tools/tests -q
"""
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent


def load_fetch():
    spec = importlib.util.spec_from_file_location(
        "fetch_budget_mod", ROOT / "tools" / "fetch" / "fetch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("requests", type(sys)("requests"))
    spec.loader.exec_module(mod)
    return mod


class TestOrderDueRequests(unittest.TestCase):
    """Pure: no disk, no network, no clock."""

    def setUp(self):
        self.mod = load_fetch()

    def test_all_eight_kinds_sort_into_the_declared_priority(self):
        kinds = ["pcs", "web_doc", "edgar_fts", "insider", "edgar_doc", "quality",
                 "fundamentals", "prices"]
        due = [{"id": f"R{i}", "kind": k, "requested_at": "2026-01-01T00:00:00Z"}
               for i, k in enumerate(kinds)]
        ordered = self.mod.order_due_requests(due)
        self.assertEqual(
            [r["kind"] for r in ordered],
            ["prices", "fundamentals", "quality", "edgar_doc", "insider",
             "edgar_fts", "web_doc", "pcs"])

    def test_oldest_requested_at_first_within_one_kind(self):
        due = [
            {"id": "NEW", "kind": "prices", "requested_at": "2026-09-10T00:00:00Z"},
            {"id": "OLD", "kind": "prices", "requested_at": "2026-08-28T20:20:00Z"},
            {"id": "MID", "kind": "prices", "requested_at": "2026-09-01T00:00:00Z"},
        ]
        ordered = self.mod.order_due_requests(due)
        self.assertEqual([r["id"] for r in ordered], ["OLD", "MID", "NEW"])

    def test_id_breaks_a_tie_on_requested_at(self):
        due = [
            {"id": "REQ-20260914-09", "kind": "prices", "requested_at": "2026-09-14T00:35:00Z"},
            {"id": "REQ-20260914-02", "kind": "prices", "requested_at": "2026-09-14T00:35:00Z"},
        ]
        ordered = self.mod.order_due_requests(due)
        self.assertEqual([r["id"] for r in ordered],
                         ["REQ-20260914-02", "REQ-20260914-09"])

    def test_fundamentals_group_entirely_precedes_quality_group_across_tickers(self):
        """The requirement's own wording: quality for a ticker only after THAT
        ticker's fundamentals. Deliberately adversarial -- AAA's quality row is
        requested earlier and listed first, BBB's fundamentals row is requested
        later -- to prove kind rank decides, not requested_at or file position."""
        due = [
            {"id": "Q-A", "kind": "quality", "ticker": "AAA", "requested_at": "2026-01-01T00:00:00Z"},
            {"id": "F-B", "kind": "fundamentals", "ticker": "BBB", "requested_at": "2026-01-02T00:00:00Z"},
            {"id": "Q-B", "kind": "quality", "ticker": "BBB", "requested_at": "2026-01-01T00:30:00Z"},
            {"id": "F-A", "kind": "fundamentals", "ticker": "AAA", "requested_at": "2026-01-02T00:30:00Z"},
        ]
        ordered = self.mod.order_due_requests(due)
        kind_seq = [r["kind"] for r in ordered]
        last_fundamentals = max(i for i, k in enumerate(kind_seq) if k == "fundamentals")
        first_quality = min(i for i, k in enumerate(kind_seq) if k == "quality")
        self.assertLess(last_fundamentals, first_quality,
                        "every fundamentals row, any ticker, must precede every "
                        "quality row, any ticker")

    def test_unknown_kind_sorts_last_without_crashing(self):
        due = [
            {"id": "M", "kind": "mystery-kind", "requested_at": "2026-01-01T00:00:00Z"},
            {"id": "P", "kind": "prices", "requested_at": "2026-01-02T00:00:00Z"},
            {"id": "W", "kind": "pcs", "requested_at": "2026-01-01T00:00:00Z"},
        ]
        ordered = self.mod.order_due_requests(due)
        self.assertEqual(ordered[-1]["id"], "M")

    def test_does_not_reorder_or_mutate_the_input_list(self):
        a = {"id": "1", "kind": "pcs", "requested_at": "2026-01-01T00:00:00Z"}
        b = {"id": "2", "kind": "prices", "requested_at": "2026-01-01T00:00:00Z"}
        due = [a, b]
        ordered = self.mod.order_due_requests(due)
        self.assertEqual(due, [a, b], "the input list's own order must not change")
        self.assertIsNot(ordered, due)
        self.assertEqual([r["id"] for r in ordered], ["2", "1"])


class TestDispatchDecision(unittest.TestCase):
    """Pure: dispatch_decision(pending_remaining, transitioned) -> (bool, reason)."""

    def setUp(self):
        self.mod = load_fetch()

    def test_no_pending_rows_never_dispatches(self):
        should, reason = self.mod.dispatch_decision(0, transitioned=5)
        self.assertFalse(should)
        self.assertIn("drained", reason)

    def test_pending_rows_but_no_progress_never_dispatches(self):
        """The loop-prevention case: a misconfigured budget or a total outage that
        lets zero rows complete must not queue an identical run forever."""
        should, reason = self.mod.dispatch_decision(pending_remaining=200, transitioned=0)
        self.assertFalse(should)
        self.assertIn("no progress", reason)

    def test_pending_rows_and_progress_dispatches(self):
        should, reason = self.mod.dispatch_decision(pending_remaining=200, transitioned=3)
        self.assertTrue(should)
        self.assertIn("200", reason)
        self.assertIn("3", reason)

    def test_zero_pending_and_zero_progress_never_dispatches(self):
        should, _reason = self.mod.dispatch_decision(0, 0)
        self.assertFalse(should)

    def test_a_single_transitioned_row_is_enough_progress(self):
        should, _reason = self.mod.dispatch_decision(pending_remaining=1, transitioned=1)
        self.assertTrue(should)


class TestDispatchNextRunUnit(unittest.TestCase):
    """dispatch_next_run's own subprocess call, always faked -- this file must never
    be able to shell out to a real `gh` binary."""

    def setUp(self):
        self.mod = load_fetch()

    def test_success_returns_true_and_shapes_the_command(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        self.mod.subprocess = types.SimpleNamespace(run=fake_run)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GITHUB_REPOSITORY", None)
            ok = self.mod.dispatch_next_run("because reasons")
        self.assertTrue(ok)
        self.assertEqual(calls[0], ["gh", "workflow", "run", "fetch.yml", "--ref", "main"])

    def test_passes_repo_when_env_is_set(self):
        calls = []
        self.mod.subprocess = types.SimpleNamespace(
            run=lambda cmd, **k: calls.append(cmd) or types.SimpleNamespace(
                returncode=0, stdout="", stderr=""))
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "ronki/upstream"}):
            self.mod.dispatch_next_run("x")
        self.assertIn("--repo", calls[0])
        self.assertIn("ronki/upstream", calls[0])

    def test_nonzero_returncode_returns_false(self):
        self.mod.subprocess = types.SimpleNamespace(
            run=lambda cmd, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="boom"))
        ok = self.mod.dispatch_next_run("x")
        self.assertFalse(ok)

    def test_a_raising_subprocess_call_never_raises_out(self):
        def boom(cmd, **k):
            raise FileNotFoundError("gh: command not found")
        self.mod.subprocess = types.SimpleNamespace(run=boom)
        ok = self.mod.dispatch_next_run("x")
        self.assertFalse(ok)


class TestEnvInt(unittest.TestCase):
    def setUp(self):
        self.mod = load_fetch()

    def test_missing_env_uses_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("_FETCH_BUDGET_TEST_VAR", None)
            self.assertEqual(self.mod._env_int("_FETCH_BUDGET_TEST_VAR", 1200), 1200)

    def test_valid_env_overrides_default(self):
        with mock.patch.dict(os.environ, {"_FETCH_BUDGET_TEST_VAR": "42"}):
            self.assertEqual(self.mod._env_int("_FETCH_BUDGET_TEST_VAR", 7), 42)

    def test_garbage_env_falls_back_to_default(self):
        with mock.patch.dict(os.environ, {"_FETCH_BUDGET_TEST_VAR": "not-a-number"}):
            self.assertEqual(self.mod._env_int("_FETCH_BUDGET_TEST_VAR", 7), 7)

    def test_default_budget_leaves_room_under_the_job_ceiling(self):
        """.github/workflows/fetch.yml's job timeout is 30 minutes; the budget must
        leave real room for dependency install, the rebuild step, and commit/push
        retries on top of it."""
        self.assertLess(self.mod.FETCH_ROW_BUDGET_SECONDS, 1800)
        self.assertGreaterEqual(self.mod.FETCH_ROW_BUDGET_SECONDS, 600)


class _MainHarness(unittest.TestCase):
    """Shared setup for tests that run main() end to end: temp DATA dir, a stubbed
    feeds module (real feeds.py is orthogonal to this file and not on sys.path when
    fetch.py is loaded this way), argv/environ pinned to a plain push-style run.
    """

    def setUp(self):
        self.mod = load_fetch()
        self.td = tempfile.TemporaryDirectory()
        self.data = Path(self.td.name) / "data"
        (self.data).mkdir(parents=True, exist_ok=True)
        self.mod.DATA = self.data
        self._feeds_mod = sys.modules.get("feeds")
        stub = types.ModuleType("feeds")
        stub.run_feeds = lambda: {"ok": 0, "new": 0, "held": 0}
        sys.modules["feeds"] = stub
        self._env = mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push"}, clear=False)
        self._env.start()
        os.environ.pop("GITHUB_ACTIONS", None)
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

    def write_requests(self, rows):
        (self.data / "requests.json").write_text(json.dumps({"version": 1, "requests": rows}))

    def read_requests(self):
        return json.loads((self.data / "requests.json").read_text())["requests"]

    def fake_clock(self):
        clock = {"t": 0.0}
        self.mod.time = types.SimpleNamespace(
            monotonic=lambda: clock["t"], sleep=lambda s: None, time=lambda: clock["t"])
        return clock


class TestRequestRowBudget(_MainHarness):
    def test_rows_past_the_budget_remain_pending_with_attempts_unchanged(self):
        clock = self.fake_clock()
        self.mod.FETCH_ROW_BUDGET_SECONDS = 100
        attempted = []

        def slow_prices(t):
            clock["t"] += 60  # each call burns more than half the budget
            attempted.append(t)
            return [f"data/market/{t}.json"]
        self.mod.do_prices = slow_prices
        self.write_requests([
            {"id": "REQ-20260914-01", "kind": "prices", "ticker": "AAA",
             "requested_at": "2026-09-14T00:00:00Z", "status": "PENDING"},
            {"id": "REQ-20260914-02", "kind": "prices", "ticker": "BBB",
             "requested_at": "2026-09-14T00:01:00Z", "status": "PENDING"},
            {"id": "REQ-20260914-03", "kind": "prices", "ticker": "CCC",
             "requested_at": "2026-09-14T00:02:00Z", "status": "PENDING"},
        ])
        self.mod.main()
        by_id = {r["id"]: r for r in self.read_requests()}
        self.assertEqual(attempted, ["AAA", "BBB"], "budget must stop before CCC")
        self.assertEqual(by_id["REQ-20260914-01"]["status"], "FULFILLED")
        self.assertEqual(by_id["REQ-20260914-02"]["status"], "FULFILLED")
        self.assertEqual(by_id["REQ-20260914-03"]["status"], "PENDING",
                         "a row the run never reached must stay exactly PENDING")
        self.assertNotIn("attempts", by_id["REQ-20260914-03"],
                         "attempts must not increment for a row the budget skipped")

    def test_a_row_already_in_progress_is_never_cut_off_mid_row(self):
        """The check happens BEFORE a row starts, never mid-row: a row that was
        admitted always completes and is recorded FULFILLED or FAILED, never left
        half-done."""
        clock = self.fake_clock()
        self.mod.FETCH_ROW_BUDGET_SECONDS = 10

        def slow_prices(t):
            clock["t"] += 1000  # wildly over budget, but this row already started
            return ["ok"]
        self.mod.do_prices = slow_prices
        self.write_requests([
            {"id": "REQ-1", "kind": "prices", "ticker": "AAA",
             "requested_at": "2026-09-14T00:00:00Z", "status": "PENDING"},
        ])
        self.mod.main()
        by_id = {r["id"]: r for r in self.read_requests()}
        self.assertEqual(by_id["REQ-1"]["status"], "FULFILLED")

    def test_nothing_deferred_when_it_all_fits_comfortably(self):
        self.mod.do_prices = lambda t: ["ok"]
        self.write_requests([
            {"id": f"REQ-{i}", "kind": "prices", "ticker": f"T{i}",
             "requested_at": "2026-09-14T00:00:00Z", "status": "PENDING"}
            for i in range(3)
        ])
        self.mod.main()
        statuses = {r["status"] for r in self.read_requests()}
        self.assertEqual(statuses, {"FULFILLED"})

    def test_quality_never_processed_before_its_own_tickers_fundamentals_this_run(self):
        calls = []
        self.mod.do_fundamentals = lambda t: calls.append(("fundamentals", t)) or ["f"]
        self.mod.do_quality = lambda t: calls.append(("quality", t)) or ["q"]
        # Quality for AAA is requested BEFORE fundamentals for AAA -- an ordering no
        # compliant session ever writes (the pull-data skill queues fundamentals
        # first), but exactly the adversarial case that proves kind rank decides.
        self.write_requests([
            {"id": "REQ-1", "kind": "quality", "ticker": "AAA",
             "requested_at": "2026-09-14T00:00:00Z", "status": "PENDING"},
            {"id": "REQ-2", "kind": "fundamentals", "ticker": "AAA",
             "requested_at": "2026-09-14T00:05:00Z", "status": "PENDING"},
        ])
        self.mod.main()
        self.assertEqual([c[0] for c in calls], ["fundamentals", "quality"])

    def test_deferred_by_budget_is_recorded_on_the_health_run(self):
        clock = self.fake_clock()
        self.mod.FETCH_ROW_BUDGET_SECONDS = 1
        self.mod.do_prices = lambda t: (clock.__setitem__("t", clock["t"] + 5) or ["ok"])
        self.write_requests([
            {"id": "REQ-1", "kind": "prices", "ticker": "AAA",
             "requested_at": "2026-09-14T00:00:00Z", "status": "PENDING"},
            {"id": "REQ-2", "kind": "prices", "ticker": "BBB",
             "requested_at": "2026-09-14T00:01:00Z", "status": "PENDING"},
        ])
        self.mod.main()
        health = json.loads((self.data / "health" / "actions.json").read_text())
        run = health["fetch"]["runs"][-1]
        self.assertGreater(run["deferred_by_budget"], 0)
        self.assertEqual(run["pending_remaining"], 1)


class TestSelfDispatchInMain(_MainHarness):
    def setUp(self):
        super().setUp()
        self.dispatch_calls = []
        self.mod.dispatch_next_run = lambda reason: (
            self.dispatch_calls.append(reason) or True)

    def test_dispatches_when_pending_remain_and_progress_was_made(self):
        clock = self.fake_clock()
        self.mod.FETCH_ROW_BUDGET_SECONDS = 50

        def prices(t):
            clock["t"] += 60
            return ["ok"]
        self.mod.do_prices = prices
        os.environ["GITHUB_ACTIONS"] = "true"
        self.write_requests([
            {"id": "REQ-1", "kind": "prices", "ticker": "AAA",
             "requested_at": "2026-09-14T00:00:00Z", "status": "PENDING"},
            {"id": "REQ-2", "kind": "prices", "ticker": "BBB",
             "requested_at": "2026-09-14T00:01:00Z", "status": "PENDING"},
        ])
        self.mod.main()
        self.assertEqual(len(self.dispatch_calls), 1)
        statuses = {r["id"]: r["status"] for r in self.read_requests()}
        self.assertEqual(statuses["REQ-1"], "FULFILLED")
        self.assertEqual(statuses["REQ-2"], "PENDING")

    def test_no_dispatch_when_the_queue_is_fully_drained(self):
        self.mod.do_prices = lambda t: ["ok"]
        os.environ["GITHUB_ACTIONS"] = "true"
        self.write_requests([
            {"id": "REQ-1", "kind": "prices", "ticker": "AAA",
             "requested_at": "2026-09-14T00:00:00Z", "status": "PENDING"},
        ])
        self.mod.main()
        self.assertEqual(self.dispatch_calls, [])

    def test_no_dispatch_when_the_budget_allows_zero_progress(self):
        self.fake_clock()
        self.mod.FETCH_ROW_BUDGET_SECONDS = 0
        self.mod.do_prices = lambda t: ["ok"]
        os.environ["GITHUB_ACTIONS"] = "true"
        self.write_requests([
            {"id": "REQ-1", "kind": "prices", "ticker": "AAA",
             "requested_at": "2026-09-14T00:00:00Z", "status": "PENDING"},
        ])
        self.mod.main()
        self.assertEqual(self.dispatch_calls, [])
        statuses = {r["id"]: r["status"] for r in self.read_requests()}
        self.assertEqual(statuses["REQ-1"], "PENDING")

    def test_no_dispatch_call_when_not_running_in_github_actions(self):
        """dispatch_decision would say yes; the GITHUB_ACTIONS gate in main() must
        still keep the real call from firing outside a runner (a session testing
        this file locally must never be able to queue a real workflow run)."""
        clock = self.fake_clock()
        self.mod.FETCH_ROW_BUDGET_SECONDS = 50

        def prices(t):
            clock["t"] += 60
            return ["ok"]
        self.mod.do_prices = prices
        self.assertNotIn("GITHUB_ACTIONS", os.environ)
        self.write_requests([
            {"id": "REQ-1", "kind": "prices", "ticker": "AAA",
             "requested_at": "2026-09-14T00:00:00Z", "status": "PENDING"},
            {"id": "REQ-2", "kind": "prices", "ticker": "BBB",
             "requested_at": "2026-09-14T00:01:00Z", "status": "PENDING"},
        ])
        self.mod.main()
        self.assertEqual(self.dispatch_calls, [])

    def test_dispatch_reason_is_always_recorded_on_the_health_run(self):
        """Rule 21: a check reports what it examined, not just what it found -- the
        reason is recorded whether or not a dispatch happened."""
        self.mod.do_prices = lambda t: ["ok"]
        os.environ["GITHUB_ACTIONS"] = "true"
        self.write_requests([
            {"id": "REQ-1", "kind": "prices", "ticker": "AAA",
             "requested_at": "2026-09-14T00:00:00Z", "status": "PENDING"},
        ])
        self.mod.main()
        health = json.loads((self.data / "health" / "actions.json").read_text())
        run = health["fetch"]["runs"][-1]
        self.assertIsNotNone(run["dispatch_reason"])
        self.assertIn("drained", run["dispatch_reason"])
        self.assertFalse(run["dispatched_next_run"])


if __name__ == "__main__":
    unittest.main()
