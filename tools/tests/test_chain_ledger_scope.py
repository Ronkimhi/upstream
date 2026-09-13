"""Which ledger lines the chain gate reads as chain runs (2026-09-13).

The archetype rule binds every chain run logged today. It used to find them by searching the
whole line, so a `request data BWET` line whose result text said the chain refresh would come
next was read as a chain run with no archetypes, and CI stayed red on a run that never happened.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_chain  # noqa: E402


class TestChainRunLines(unittest.TestCase):
    def test_a_request_line_that_mentions_a_chain_refresh_is_not_a_chain_run(self):
        line = ("2026-09-13 15:10Z | RUN | request data BWET | by: ron | wrote: data/requests.json | "
                "result: Next: refresh data/chains/hormuz-maritime.json (Atlas) | health: n/a")
        self.assertEqual([], check_chain.chain_run_lines([line]))

    def test_chain_runs_and_chain_refreshes_still_count(self):
        run = "2026-09-13 15:02Z | RUN | run chain SIG-20260901-01 | by: ron | result: archetypes: none applied"
        amend = "2026-09-13 18:00Z | AMEND | refresh data/chains/hormuz-maritime.json | by: ron | result: x"
        note = "2026-09-13 14:17Z | NOTE | run chain claimed by the full-cycle run | by: ron | result: x"
        self.assertEqual([run, amend], check_chain.chain_run_lines([run, amend, note]))


if __name__ == "__main__":
    unittest.main()
