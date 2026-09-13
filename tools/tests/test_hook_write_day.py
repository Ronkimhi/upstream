#!/usr/bin/env python3
"""The Stop hooks ask WHEN a write happened, not only whether the session ever made one.

Both hooks covered here used to scan the whole transcript of a session and then demand a
ledger line dated TODAY. In a session spanning several days those are different questions, and
the gap cost real work twice: a loop that closed on Friday with its own dated ledger line and
same-day calibration still blocked every stop on Sunday, and the only way past the universe
hook was to write `run universe <slug>` into the ledger on a day no such run happened. A forged
line in an append-only record is worse than a missing one, so the rule became: the ledger line
is owed for the UTC day the write ACTUALLY happened.

Each test below pins one half of that. A hook that stops refusing an open loop is no longer a
gate, so every "passes" case here is paired with a "refuses" case on the same machinery.
"""
import contextlib
import datetime
import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS = ROOT / ".claude" / "hooks"


def load_hook(name):
    sys.path.insert(0, str(HOOKS))
    spec = importlib.util.spec_from_file_location(f"{name}_gate", HOOKS / f"{name}-gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_event(path, ts=None):
    """One transcript line: a Write tool_use, optionally stamped."""
    event = {
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use", "name": "Write", "input": {"file_path": path},
        }]},
    }
    if ts is not None:
        event["timestamp"] = ts
    return json.dumps(event)


class TestTranscriptWriteDay(unittest.TestCase):
    """write_targets, the shared helper all seven hooks can use."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(HOOKS))
        import write_targets
        cls.wt = write_targets
        cls.pat = re.compile(r"data/impact/(?!_)[^/]+\.json$")

    def test_day_comes_out_with_the_path(self):
        raw = "\n".join([
            write_event("data/impact/IMP-20260910-01.json", "2026-09-11T14:00:00.000Z"),
            write_event("data/impact/IMP-20260910-02.json", "2026-09-12T01:00:00.000Z"),
        ])
        pairs = list(self.wt.transcript_writes(raw, "data/impact/"))
        self.assertEqual(
            [(d.isoformat(), t) for d, t in pairs],
            [("2026-09-11", "data/impact/IMP-20260910-01.json"),
             ("2026-09-12", "data/impact/IMP-20260910-02.json")],
        )

    def test_latest_write_day_takes_the_most_recent(self):
        raw = "\n".join([
            write_event("data/impact/A.json", "2026-09-11T14:00:00.000Z"),
            write_event("data/impact/B.json", "2026-09-09T14:00:00.000Z"),
        ])
        self.assertEqual(
            self.wt.latest_write_day(raw, self.pat, hint="data/impact/"),
            datetime.date(2026, 9, 11),
        )

    def test_no_matching_write_is_none_not_a_day(self):
        raw = write_event("data/chains/some-chain.json", "2026-09-11T14:00:00.000Z")
        self.assertIsNone(self.wt.latest_write_day(raw, self.pat, hint="data/impact/"))

    def test_the_rank_log_is_not_an_appraisal(self):
        """The `(?!_)` guard: re-deriving the queue must not look like writing an appraisal,
        or the hook fires on its own remedy."""
        raw = write_event("data/impact/_rank-log.json", "2026-09-13T02:00:00.000Z")
        self.assertIsNone(self.wt.latest_write_day(raw, self.pat, hint="data/impact/"))

    def test_an_undateable_write_counts_as_the_unknown_day(self):
        """A transcript this module cannot date must keep the gate firing, never switch it
        off: an unreadable timestamp is the hook's blind spot, not a clean bill of health."""
        raw = write_event("data/impact/A.json")  # no timestamp at all
        today = datetime.date(2026, 9, 13)
        self.assertEqual(
            self.wt.latest_write_day(raw, self.pat, hint="data/impact/", unknown=today),
            today,
        )
        # and with no `unknown` supplied there is no day to hold, so nothing is claimed
        self.assertIsNone(self.wt.latest_write_day(raw, self.pat, hint="data/impact/"))

    def test_a_naive_timestamp_is_not_a_utc_day(self):
        self.assertIsNone(self.wt.line_utc_date("2026-09-11T23:58:00"))
        self.assertEqual(
            self.wt.line_utc_date("2026-09-11T23:58:00Z"), datetime.date(2026, 9, 11)
        )


class HookCase(unittest.TestCase):
    """Drive a hook over a temp repo root with a controlled 'today'."""

    def _decide(self, hook, root, transcript_text, today):
        transcript_path = root / "transcript.jsonl"
        transcript_path.write_text(transcript_text)
        payload = json.dumps({"transcript_path": str(transcript_path)})
        out = io.StringIO()
        patches = [
            mock.patch.object(hook, "ROOT", root),
            mock.patch.object(sys, "stdin", io.StringIO(payload)),
            contextlib.redirect_stdout(out),
        ]
        if hasattr(hook, "utc_today"):
            patches.append(mock.patch.object(hook, "utc_today", return_value=today))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            hook.main()
        return json.loads(out.getvalue())["decision"]


class TestImpactGateWriteDay(HookCase):
    """A closed appraisal loop from an earlier day does not block a later stop."""

    @classmethod
    def setUpClass(cls):
        cls.hook = load_hook("impact")

    def _tree(self, root, *, ledger_lines, calibration_as_of):
        (root / "data" / "impact").mkdir(parents=True, exist_ok=True)
        (root / "data" / "ledger.md").write_text("".join(ledger_lines))
        (root / "data" / "impact" / "_rank-log.json").write_text(
            json.dumps({"calibration": {"as_of": calibration_as_of}})
        )

    IMPACT_LINE = (
        "2026-09-11 14:05Z | IMPACT | run impact SIG-20260910-01 | by: ron | "
        "wrote: data/impact/IMP-20260910-01.json | result: ranked: 1 | band: LEAKY | "
        "health: n/a | artifact: skipped(x) | model: claude-opus-5\n"
    )

    def test_friday_loop_closed_does_not_block_a_sunday_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root, ledger_lines=[self.IMPACT_LINE], calibration_as_of="2026-09-11")
            raw = write_event(
                "data/impact/IMP-20260910-01.json", "2026-09-11T14:00:00.000Z"
            )
            self.assertEqual(
                self._decide(self.hook, root, raw, datetime.date(2026, 9, 13)),
                "approve",
                "an appraisal written Friday, with Friday's ledger line and calibration on "
                "disk, is a closed loop and must not block a Sunday stop",
            )

    def test_a_calibration_re_derived_later_is_fresher_not_wrong(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root, ledger_lines=[self.IMPACT_LINE], calibration_as_of="2026-09-13")
            raw = write_event(
                "data/impact/IMP-20260910-01.json", "2026-09-11T14:00:00.000Z"
            )
            self.assertEqual(
                self._decide(self.hook, root, raw, datetime.date(2026, 9, 13)), "approve"
            )

    def test_an_appraisal_written_today_with_no_ledger_line_still_blocks(self):
        """The gate still gates. Narrowing the scan to the write's own day must not make an
        actually-open loop pass."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root, ledger_lines=[self.IMPACT_LINE], calibration_as_of="2026-09-13")
            raw = write_event(
                "data/impact/IMP-20260913-09.json", "2026-09-13T02:00:00.000Z"
            )
            self.assertEqual(
                self._decide(self.hook, root, raw, datetime.date(2026, 9, 13)), "block"
            )

    def test_an_appraisal_written_today_with_a_stale_calibration_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            today_line = self.IMPACT_LINE.replace("2026-09-11", "2026-09-13")
            self._tree(
                root,
                ledger_lines=[today_line],
                calibration_as_of="2026-09-11",
            )
            raw = write_event(
                "data/impact/IMP-20260913-09.json", "2026-09-13T02:00:00.000Z"
            )
            self.assertEqual(
                self._decide(self.hook, root, raw, datetime.date(2026, 9, 13)), "block"
            )

    def test_a_write_at_2358z_is_owed_yesterdays_line(self):
        """The UTC-midnight edge the old rule had backwards: a write stamped 23:58Z carries a
        23:58Z ledger line, which is already yesterday's date four minutes later."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            line = (
                "2026-09-12 23:58Z | IMPACT | run impact SIG-20260912-01 | by: ron | "
                "wrote: data/impact/IMP-20260912-01.json | result: ranked: 1 | "
                "band: PRIME | health: n/a | artifact: skipped(x) | model: claude-opus-5\n"
            )
            self._tree(root, ledger_lines=[line], calibration_as_of="2026-09-12")
            raw = write_event(
                "data/impact/IMP-20260912-01.json", "2026-09-12T23:58:00.000Z"
            )
            self.assertEqual(
                self._decide(self.hook, root, raw, datetime.date(2026, 9, 13)), "approve"
            )


class TestUniverseGateWriteDay(HookCase):
    """Same rule on the census hook, which was the one that could only be satisfied by a
    forged ledger line."""

    @classmethod
    def setUpClass(cls):
        cls.hook = load_hook("universe")

    def _tree(self, root, *, ledger_lines, map_log_generated_at):
        (root / "data" / "mappings").mkdir(parents=True, exist_ok=True)
        (root / "data" / "chains").mkdir(parents=True, exist_ok=True)
        (root / "data" / "ledger.md").write_text("".join(ledger_lines))
        (root / "data" / "mappings" / "h5n1-panzootic.json").write_text(json.dumps({}))
        (root / "data" / "chains" / "_map-log.json").write_text(
            json.dumps({"calibration": {"generated_at": map_log_generated_at}})
        )

    UNIVERSE_LINE = (
        "2026-09-11 03:19Z | RUN | run universe h5n1-panzootic | by: ron | "
        "wrote: data/mappings/h5n1-panzootic.json | result: censused 4 links | "
        "health: check_map OK | artifact: skipped(x) | model: claude-opus-5\n"
    )

    def test_friday_census_closed_does_not_block_a_sunday_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(
                root,
                ledger_lines=[self.UNIVERSE_LINE],
                map_log_generated_at="2026-09-11T03:20:00Z",
            )
            raw = write_event(
                "data/mappings/h5n1-panzootic.json", "2026-09-11T03:19:00.000Z"
            )
            self.assertEqual(
                self._decide(self.hook, root, raw, datetime.date(2026, 9, 13)),
                "approve",
                "a census written Friday with Friday's ledger line and calibration is a "
                "closed loop; demanding a Sunday-dated `run universe` line can only be "
                "satisfied by forging one",
            )

    def test_a_census_written_today_with_no_ledger_line_still_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(
                root,
                ledger_lines=[self.UNIVERSE_LINE],
                map_log_generated_at="2026-09-13T02:00:00Z",
            )
            raw = write_event(
                "data/mappings/h5n1-panzootic.json", "2026-09-13T02:00:00.000Z"
            )
            self.assertEqual(
                self._decide(self.hook, root, raw, datetime.date(2026, 9, 13)), "block"
            )

    def test_a_census_written_today_with_a_stale_map_log_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            today_line = self.UNIVERSE_LINE.replace("2026-09-11", "2026-09-13")
            self._tree(
                root,
                ledger_lines=[today_line],
                map_log_generated_at="2026-09-11T03:20:00Z",
            )
            raw = write_event(
                "data/mappings/h5n1-panzootic.json", "2026-09-13T02:00:00.000Z"
            )
            self.assertEqual(
                self._decide(self.hook, root, raw, datetime.date(2026, 9, 13)), "block"
            )

    def test_two_maps_each_answer_for_their_own_day(self):
        """Per-slug, not one global day: an old closed census must not be dragged forward by
        a new one on a different chain, and a new open one must not hide behind the old."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(
                root,
                ledger_lines=[self.UNIVERSE_LINE],
                map_log_generated_at="2026-09-13T02:00:00Z",
            )
            (root / "data" / "mappings" / "other-chain.json").write_text(json.dumps({}))
            raw = "\n".join([
                write_event(
                    "data/mappings/h5n1-panzootic.json", "2026-09-11T03:19:00.000Z"
                ),
                write_event("data/mappings/other-chain.json", "2026-09-13T02:00:00.000Z"),
            ])
            decision = self._decide(self.hook, root, raw, datetime.date(2026, 9, 13))
            self.assertEqual(
                decision, "block",
                "other-chain was censused today and owes a line dated today; h5n1's closed "
                "Friday loop does not cover it",
            )


if __name__ == "__main__":
    unittest.main()
