"""The run a ledger line records is its command field, not what its text mentions (2026-09-13)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from ledger_lines import command_lines  # noqa: E402

REQUEST = ("2026-09-13 15:10Z | RUN | request data BWET | by: ron | result: next: refresh data/chains/x.json, "
           "run heat hormuz-maritime and run scenarios hormuz-maritime | health: n/a")
HEAT = "2026-09-13 18:11Z | AMEND | run heat hormuz-maritime | by: ron | result: x"
NOTE = "2026-09-13 18:20Z | NOTE | run heat hormuz-maritime handed to local | by: ron | result: x"
OLD = "2026-09-12 09:00Z | RUN | run heat tibet-mega-dam | by: ember | result: x"


class TestCommandLines(unittest.TestCase):
    def test_a_line_that_only_mentions_a_command_is_not_its_run(self):
        for prefixes in (("run heat ",), ("run scenarios ",), ("run chain", "refresh data/chains")):
            self.assertEqual([], command_lines([REQUEST], prefixes))

    def test_the_run_line_counts_and_a_note_does_not(self):
        self.assertEqual([HEAT], command_lines([REQUEST, HEAT, NOTE], ("run heat ",)))

    def test_the_day_filter(self):
        self.assertEqual([HEAT], command_lines([OLD, HEAT], ("run heat ",), day="2026-09-13"))


if __name__ == "__main__":
    unittest.main()
