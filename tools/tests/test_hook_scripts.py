#!/usr/bin/env python3
"""Regression tests for harness findings 2026-W37-F1 (chain-gate.py) and 2026-W37-F2
(radar-gate.py).

Every other Stop-hook test in this repo (test_pressure.py, test_campaign_hooks.py) loads
the hook via `importlib.util.spec_from_file_location` + `exec_module`. That never sets the
loaded module's `__name__` to `"__main__"`, so `if __name__ == "__main__": raise
SystemExit(main())` never runs there, and any bug reachable only through that guard is
invisible to those tests. Claude Code itself never imports a hook: it always runs it as a
subprocess, `python3 <hook>.py < payload`. This test does the same, to close that gap.

Both chain-gate.py and radar-gate.py got a private `_in_repo()` helper pasted in after their
`if __name__ == "__main__":` guard, by the same 2026-08-30 commit (799793a): `main()` calls
`_in_repo()` whenever the session's own transcript shows the write each gate watches for, so
every such Stop evaluation raised `NameError: name '_in_repo' is not defined` at exit code 1
instead of ever reaching the ledger/calibration check the gate exists to run.
TestChainGateScript covers the first file (fixed as 2026-W37-F1); TestRadarGateScript covers
the second (2026-W37-F2).
"""
import datetime
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS = ROOT / ".claude" / "hooks"


class TestChainGateScript(unittest.TestCase):
    """chain-gate.py invoked as a real subprocess, isolated from the live repo state.

    Commit 799793a (2026-08-30, "two Stop hooks demanded a record of work that never
    happened") added a module-level `_in_repo()` helper to chain-gate.py but appended it
    AFTER the file's existing `if __name__ == "__main__":` guard. `main()` calls
    `_in_repo()` whenever the session's transcript shows a `data/chains/` write, so every
    such Stop evaluation since that commit raised `NameError: name '_in_repo' is not
    defined` at exit code 1 instead of ever reaching the ledger/calibration check the gate
    exists to run.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        # chain-gate.py resolves its own root as Path(__file__).resolve().parent.parent.parent
        # (no env var override), so isolation means copying it plus its one import into a
        # throwaway tree the same three levels deep. Run from there, the subprocess can never
        # read or write this repo's real data/ledger.md or data/chains/_map-log.json.
        hooks_dir = self.root / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        shutil.copy(HOOKS / "chain-gate.py", hooks_dir / "chain-gate.py")
        shutil.copy(HOOKS / "write_targets.py", hooks_dir / "write_targets.py")
        self.hook_path = hooks_dir / "chain-gate.py"

    def _run(self, payload: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.hook_path)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
        )

    def _transcript_with_chain_write(self) -> Path:
        # A synthetic Stop-hook transcript: one JSONL line whose tool_use writes a real
        # (non-underscore) file under data/chains/, the shape main() must resolve through
        # write_targets.from_tool_use before it ever reaches _in_repo().
        target = str(Path("data") / "chains" / "ai-infrastructure.json")
        event = {
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": target},
                }]
            }
        }
        path = self.root / "transcript.jsonl"
        path.write_text(json.dumps(event) + "\n")
        return path

    def test_chain_write_script_invocation_does_not_crash(self):
        transcript = self._transcript_with_chain_write()

        # A complete postlude (ledger line + calibration, both dated today) so a correctly
        # working hook reaches "approve" rather than "block" -- proving the fix actually
        # restores the gate's logic, not merely that the process exits without a traceback.
        today = datetime.date.today().isoformat()
        ledger = self.root / "data" / "ledger.md"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(f"{today} 00:00Z | RUN | run chain ai-infrastructure | by: test\n")
        log = self.root / "data" / "chains" / "_map-log.json"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({"calibration": {"generated_at": f"{today}T00:00:00Z"}}))

        result = self._run({"transcript_path": str(transcript)})

        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)
        self.assertIn("decision", decision)
        self.assertEqual(decision["decision"], "approve")


class TestRadarGateScript(unittest.TestCase):
    """radar-gate.py invoked as a real subprocess, isolated from the live repo state.

    Commit 799793a (2026-08-30, "two Stop hooks demanded a record of work that never
    happened") added a module-level `_in_repo()` helper to radar-gate.py but appended it
    AFTER the file's existing `if __name__ == "__main__":` guard, the identical defect it
    introduced in chain-gate.py at the same time. `main()` calls `_in_repo()` whenever the
    transcript shows a `data/signals/` write, so every such Stop evaluation since that
    commit raised `NameError: name '_in_repo' is not defined` at exit code 1 instead of
    ever reaching the ledger/calibration check the gate exists to run.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        # radar-gate.py resolves its own root as Path(__file__).resolve().parent.parent.parent
        # (no env var override), so isolation means copying it plus its one import into a
        # throwaway tree the same three levels deep. Run from there, the subprocess can never
        # read or write this repo's real data/ledger.md or data/radar/scout-log.json.
        hooks_dir = self.root / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        shutil.copy(HOOKS / "radar-gate.py", hooks_dir / "radar-gate.py")
        shutil.copy(HOOKS / "write_targets.py", hooks_dir / "write_targets.py")
        self.hook_path = hooks_dir / "radar-gate.py"

    def _run(self, payload: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.hook_path)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
        )

    def _transcript_with_signal_write(self) -> Path:
        # A synthetic Stop-hook transcript: one JSONL line whose tool_use writes a real
        # (non-underscore) file under data/signals/, the shape main() must resolve through
        # write_targets.from_tool_use before it ever reaches _in_repo().
        target = str(Path("data") / "signals" / "SIG-20260913-01.json")
        event = {
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": target},
                }]
            }
        }
        path = self.root / "transcript.jsonl"
        path.write_text(json.dumps(event) + "\n")
        return path

    def test_signal_write_script_invocation_does_not_crash(self):
        transcript = self._transcript_with_signal_write()

        # A complete postlude (ledger line + calibration, both dated today) so a correctly
        # working hook reaches "approve" rather than "block" -- proving the fix actually
        # restores the gate's logic, not merely that the process exits without a traceback.
        # radar-gate.py dates both checks with local datetime.date.today() (a separate
        # backlog row covers moving that to UTC), so the test uses the same basis.
        today = datetime.date.today().isoformat()
        ledger = self.root / "data" / "ledger.md"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(f"{today} 00:00Z | RADAR | run radar | by: test\n")
        log = self.root / "data" / "radar" / "scout-log.json"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({"calibration": {"generated_at": f"{today}T00:00:00Z"}}))

        result = self._run({"transcript_path": str(transcript)})

        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)
        self.assertIn("decision", decision)
        self.assertEqual(decision["decision"], "approve")


if __name__ == "__main__":
    unittest.main()
