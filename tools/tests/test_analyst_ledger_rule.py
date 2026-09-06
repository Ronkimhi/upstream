import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location("check_analyst", ROOT / "tools" / "check_analyst.py")
ca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ca)

TODAY = "2026-09-06"
RUN = (f"{TODAY} 18:40Z | RUN | run deepdive ONT minor-nsr-permits | by: ron | wrote: data/stocks/ONT__minor-nsr-permits.json"
       " | result: verdict:WATCH clock:COMPOUNDER grade:B | health: 1/1 | artifact: skipped(x) | model: claude-sonnet-5")
NOTE_PENDING = (f"{TODAY} 17:53Z | NOTE | run deepdive CAT minor-nsr-permits | by: ron | wrote: data/requests.json data/ledger.md"
                " | result: PENDING_DATA, no dive file written. The exact O1 chain resolves clean | health: n/a | artifact: skipped(x) | model: claude-sonnet-5")
NOTE_BLOCKED = (f"{TODAY} 17:55Z | NOTE | run deepdive STE glp1-fill-finish | by: ron | wrote: data/ledger.md"
                " | result: BLOCKED at the admission gate, not attempted | health: n/a | artifact: skipped(x) | model: claude-sonnet-5")
NOTE_PROSE = (f"{TODAY} 12:00Z | NOTE | build ceiling | by: ron | wrote: app/build.py"
              " | result: every deepdive now carried whole on the page | health: n/a | artifact: skipped(x) | model: claude-sonnet-5")
RUN_NO_GRADE = (f"{TODAY} 18:41Z | RUN | run redteam ONT minor-nsr-permits | by: ron | wrote: data/stocks/ONT__minor-nsr-permits.json"
                " | result: verdict:WATCH clock:COMPOUNDER | health: 1/1 | artifact: skipped(x) | model: claude-sonnet-5")


class TestDiveLedgerRule(unittest.TestCase):
    def test_blocked_and_pending_notes_are_not_dive_lines(self):
        ledger = "\n".join([NOTE_PENDING, NOTE_BLOCKED, NOTE_PROSE, RUN])
        self.assertEqual(ca.dive_ledger_failures(ledger, TODAY), [])

    def test_a_real_dive_line_still_needs_all_three_tokens(self):
        ledger = "\n".join([RUN, RUN_NO_GRADE])
        out = ca.dive_ledger_failures(ledger, TODAY)
        self.assertEqual(len(out), 1)
        self.assertIn("grade:", out[0])

    def test_a_run_line_that_pends_is_still_held_to_the_rule(self):
        pending_run = NOTE_PENDING.replace("| NOTE |", "| RUN |")
        out = ca.dive_ledger_failures("\n".join([RUN, pending_run]), TODAY)
        self.assertEqual(len(out), 3)

    def test_notes_alone_do_not_satisfy_the_day(self):
        out = ca.dive_ledger_failures("\n".join([NOTE_PENDING, NOTE_BLOCKED]), TODAY)
        self.assertEqual(len(out), 1)
        self.assertIn("no ledger line", out[0])


if __name__ == "__main__":
    unittest.main()
