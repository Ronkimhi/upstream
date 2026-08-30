"""Fail-open unreadable-state and fail-closed missing-evidence tests for Ember's Stop hook."""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
HOOK_PATH = ROOT / ".claude" / "hooks" / "ember-gate.py"
spec = importlib.util.spec_from_file_location("ember_gate", HOOK_PATH)
ember = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ember)


# The first four lines of the real data/ledger.md, verbatim in shape. Lines 3 and 4 are prose
# and both contain "|", which is the whole point: the hook filtered on `"|" in line` and then
# parsed field one as a timestamp, so it raised ValueError on line 3 of the real ledger before
# it ever reached a run line. Every heat and scenario session got a traceback instead of a gate.
LEDGER_HEADERS = (
    "# Upstream run ledger\n"
    "\n"
    "Append-only. One line per session/routine run. Line types: RUN | AMEND | RADAR | "
    "RADAR-DEGRADED | DIGEST | NOTE.\n"
    "Format: `date | TYPE | command | by: who | wrote: paths | result: summary | "
    "health: n/n | artifact: republished|skipped(reason)`\n"
    "\n"
)


class TestEmberStopHook(unittest.TestCase):
    def decision(self, *, ledger=True, calibration=True, malformed=False, headers=False):
        with tempfile.TemporaryDirectory() as tmp:
            root, today = Path(tmp), ember.datetime.datetime.now(ember.datetime.timezone.utc).date().isoformat()
            (root / "data" / "chains").mkdir(parents=True)
            (root / "data" / "chains" / "test-chain.json").write_text(
                json.dumps({"heat_as_of": today}))
            transcript = root / "transcript.jsonl"
            transcript.write_text(json.dumps({"message": {"content": [{
                "type": "tool_use", "name": "Write",
                "input": {"file_path": "data/chains/test-chain.json"}}]}}))
            if ledger or headers:
                (root / "data" / "ledger.md").write_text(
                    (LEDGER_HEADERS if headers else "")
                    + (f"{today}T00:00:00Z | RUN | run heat test-chain | by: ember\n"
                       if ledger else ""))
            if calibration:
                payload = "{bad" if malformed else json.dumps(
                    {"calibration": {"generated_at": f"{today}T00:00:00Z"}})
                (root / "data" / "chains" / "_ember-log.json").write_text(payload)
            out = io.StringIO()
            with mock.patch.object(ember, "ROOT", root), mock.patch.object(
                sys, "stdin", io.StringIO(json.dumps({"transcript_path": str(transcript)}))
            ), contextlib.redirect_stdout(out):
                ember.main()
            return json.loads(out.getvalue())["decision"]

    def test_missing_required_evidence_blocks(self):
        self.assertEqual(self.decision(ledger=False), "block")
        self.assertEqual(self.decision(calibration=False), "block")

    def test_unreadable_calibration_fails_open(self):
        self.assertEqual(self.decision(malformed=True), "approve")

    def test_prose_ledger_headers_do_not_stop_the_hook_from_blocking(self):
        """The behaviour the hook exists for, with the real ledger's prose headers present.

        A ledger whose only run line is missing must BLOCK. Before the fix the header lines
        raised ValueError out of `utc_date`, so the hook died on the real repo's ledger and
        enforced nothing at all.
        """
        self.assertEqual(self.decision(ledger=False, headers=True), "block")

    def test_prose_ledger_headers_do_not_suppress_a_real_run_line(self):
        """The complement: headers present AND the run line present must still approve."""
        self.assertEqual(self.decision(ledger=True, headers=True), "approve")


if __name__ == "__main__":
    unittest.main()
