#!/usr/bin/env python3
"""chain-gate.py and radar-gate.py must date their checks in UTC, like the other five gates.

Every ledger line is stamped in UTC and every calibration's `generated_at` is a UTC
timestamp. These two gates computed "today" with local `datetime.date.today()`, so on any
machine whose local date differed from the UTC date they looked for a ledger line and a
calibration dated a day off, and blocked a session that had closed its loop correctly.

Each test runs the hook as a subprocess under a TZ whose local date differs from the UTC date
at that moment, so a local-date gate fails this test at any hour, not only near midnight.
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS = ROOT / ".claude" / "hooks"

# hook -> (store it watches, file written there, ledger line, calibration file)
CASES = {
    "chain-gate.py": ("chains", "ai-infrastructure.json",
                      "{d} 00:00Z | RUN | run chain ai-infrastructure | by: test",
                      ("chains", "_map-log.json")),
    "radar-gate.py": ("signals", "SIG-20260913-01.json",
                      "{d} 00:00Z | RADAR | run radar | by: test",
                      ("radar", "scout-log.json")),
}


def off_utc_zone(now=None) -> str:
    """A TZ name whose local date differs from the UTC date right now.

    UTC+14 and UTC-12 sit 26 hours apart, so at any instant at least one of them is on a
    different calendar date than UTC. POSIX `Etc/` names invert the sign.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if (now + datetime.timedelta(hours=14)).date() != now.date():
        return "Etc/GMT-14"
    return "Etc/GMT+12"


class TestGatesDateInUtc(unittest.TestCase):
    def run_hook(self, hook: str) -> subprocess.CompletedProcess:
        store, name, ledger_line, (cal_dir, cal_file) = CASES[hook]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            hooks = root / ".claude" / "hooks"
            hooks.mkdir(parents=True)
            shutil.copy(HOOKS / hook, hooks / hook)
            shutil.copy(HOOKS / "write_targets.py", hooks / "write_targets.py")

            utc_today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
            (root / "data").mkdir()
            (root / "data" / "ledger.md").write_text(ledger_line.format(d=utc_today) + "\n")
            cal = root / "data" / cal_dir / cal_file
            cal.parent.mkdir(parents=True, exist_ok=True)
            cal.write_text(json.dumps({"calibration": {"generated_at": f"{utc_today}T00:00:00Z"}}))

            transcript = root / "transcript.jsonl"
            event = {"message": {"content": [{
                "type": "tool_use", "name": "Write",
                "input": {"file_path": str(Path("data") / store / name)}}]}}
            transcript.write_text(json.dumps(event) + "\n")

            return subprocess.run(
                [sys.executable, str(hooks / hook)],
                input=json.dumps({"transcript_path": str(transcript)}),
                capture_output=True, text=True,
                env={**os.environ, "TZ": off_utc_zone()},
            )

    def assert_approves(self, result: subprocess.CompletedProcess):
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["decision"], "approve", result.stdout)

    def test_zone_really_sits_on_another_date(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        zone = off_utc_zone(now)
        child = subprocess.run(
            [sys.executable, "-c", "import datetime; print(datetime.date.today())"],
            capture_output=True, text=True, env={**os.environ, "TZ": zone})
        self.assertNotEqual(child.stdout.strip(), now.date().isoformat(),
                            f"TZ={zone} did not move the child's local date, so the gate tests "
                            f"below would pass on a local-date gate")

    def test_chain_gate_approves_a_utc_dated_loop(self):
        self.assert_approves(self.run_hook("chain-gate.py"))

    def test_radar_gate_approves_a_utc_dated_loop(self):
        self.assert_approves(self.run_hook("radar-gate.py"))


if __name__ == "__main__":
    unittest.main()
