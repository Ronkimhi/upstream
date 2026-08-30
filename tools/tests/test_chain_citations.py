#!/usr/bin/env python3
"""The section 4 citation bar in `tools/check_chain.py`, watched refusing something.

The bar shipped 2026-08-29 as a WARNING because 36 of 36 seed links were uncited and a gate
that fails from its first run is a gate people route around. The backfill landed 2026-08-30
and took the corpus to zero uncited, so the flag flipped: uncited is now an ERROR by
default and `--warn-citations` is the dated escape hatch for a genuine backfill run.

Nothing anywhere watched `check_chain.py` refuse anything, which is the finding
`tools/check_machine.py` reports as gate falsifiability: a gate nobody has seen fail may not
be able to. These two tests are that coverage, and they pin the DEFAULT, because the defect
this class of change causes is silent inversion. A later edit that restores warn-by-default
now fails a test instead of quietly un-enforcing the bar.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# A chain that satisfies every other check in the gate, so the citation bar is the only
# variable: 8 links, positions 1..8, one connected acyclic component, every edge mirrored
# both ways, example_tickers present, a chain-specific map_limitation, no signal to agree
# with, and no write dated on the run day so the ledger and preservation checks stand down.
CHAIN_DATE = "2026-08-01"
GATE_DATE = "2026-08-15"


def _link(i: int, n: int, cited: bool) -> dict:
    link = {
        "id": f"L{i}",
        "name": f"stage {i}",
        "position": i,
        "upstream_of": [f"L{i + 1}"] if i < n else [],
        "downstream_of": [f"L{i - 1}"] if i > 1 else [],
        "example_tickers": ["AAA"],
        "bottleneck": {"criticality": "ROUTABLE"},
    }
    if cited:
        link["evidence"] = [{
            "claim": f"Stage {i} exists and feeds the next stage.",
            "source_name": "Industry reference",
            "source_date": "2026-07-01",
            "url": "https://example.com/chain",
            "tag": "INFERRED",
        }]
    return link


def chain_root(td: str, uncited_links=()) -> Path:
    """A repo root holding one otherwise-clean chain, with the named links uncited."""
    root = Path(td)
    (root / "data" / "chains").mkdir(parents=True)
    (root / "data" / "signals").mkdir(parents=True)
    n = 8
    chain = {
        "id": "test-chain",
        "created_at": CHAIN_DATE,
        "updated_at": CHAIN_DATE,
        "map_limitation": "This map cannot see privately held tier-3 suppliers.",
        "links": [_link(i, n, cited=f"L{i}" not in set(uncited_links))
                  for i in range(1, n + 1)],
        "scenarios": [],
    }
    (root / "data" / "chains" / "test-chain.json").write_text(json.dumps(chain))
    (root / "data" / "ledger.md").write_text("")
    return root


class TestCitationBar(unittest.TestCase):

    def test_an_uncited_link_fails_by_default(self):
        """The bar is an error, not a warning. A link is a claim that a stage exists."""
        with tempfile.TemporaryDirectory() as td:
            root = chain_root(td, uncited_links=["L4"])
            r = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "check_chain.py"),
                 "--root", str(root), "--date", GATE_DATE],
                capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("carry no dated evidence", r.stdout)
        self.assertIn("L4", r.stdout)
        self.assertIn("1 uncited (error mode)", r.stdout)

    def test_the_same_chain_passes_under_warn_citations(self):
        """`--warn-citations` is the dated escape hatch for a backfill run, and it must
        still SAY what it let through rather than printing a clean pass."""
        with tempfile.TemporaryDirectory() as td:
            root = chain_root(td, uncited_links=["L4"])
            r = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "check_chain.py"),
                 "--root", str(root), "--date", GATE_DATE, "--warn-citations"],
                capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WARNING: --warn-citations", r.stdout)
        self.assertIn("1 uncited (warning mode)", r.stdout)

    def test_a_fully_cited_chain_passes_in_the_default_error_mode(self):
        """The other half of the pair: the bar must not fail a chain that clears it, or the
        two tests above would be satisfied by a gate that simply always refuses."""
        with tempfile.TemporaryDirectory() as td:
            root = chain_root(td)
            r = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "check_chain.py"),
                 "--root", str(root), "--date", GATE_DATE],
                capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0 uncited (error mode)", r.stdout)


if __name__ == "__main__":
    unittest.main()
