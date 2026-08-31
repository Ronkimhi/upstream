#!/usr/bin/env python3
"""Exact tests for the model-tier gate. Every audit must be able to FAIL."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def run(root, *extra):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_model.py"), "--root", str(root), *extra],
        capture_output=True, text=True)


class Fixture:
    """A minimal but STRUCTURALLY VALID machine, which each test then breaks in one place."""

    def __init__(self, td):
        self.root = Path(td)
        (self.root / "tools").mkdir(parents=True)
        (self.root / ".claude" / "agents").mkdir(parents=True)
        (self.root / "data").mkdir(parents=True)

        self.commands = [
            "| `run alpha` (agent: **Ann**, `.claude/agents/ann.md`) | x | y | z |",
        ]
        self.tiers = (
            "TIER_MODEL = {'fast': 'claude-haiku-4-5-20251001', 'strong': 'claude-sonnet-5'}\n"
            "COMMAND_TIER = {'run alpha': 'fast'}\n"
        )
        self.agents = {"ann.md": "---\nname: ann\ndescription: Ann.\n---\n# Ann\n"}
        self.ledger = ""

    def write(self):
        r = self.root
        (r / "CLAUDE.md").write_text(
            "| Command | Reads | Writes | Must verify |\n|---|---|---|---|\n"
            + "\n".join(self.commands) + "\n")
        (r / "tools" / "model_tiers.py").write_text(self.tiers)
        for name, body in self.agents.items():
            (r / ".claude" / "agents" / name).write_text(body)
        (r / "data" / "ledger.md").write_text(self.ledger)
        return self.root


class TestAuditsCanFail(unittest.TestCase):
    def build(self, mutate=None):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        f = Fixture(self.td.name)
        if mutate:
            mutate(f)
        return f.write()

    def test_clean_fixture_has_no_findings(self):
        r = run(self.build())
        self.assertIn("check_model: OK", r.stdout, r.stdout)
        self.assertEqual(r.returncode, 0)

    def test_command_with_no_tier_is_found(self):
        def m(f):
            f.commands.append(
                "| `run beta` (agent: **Bo**, `.claude/agents/bo.md`) | x | y | z |")
            f.agents["bo.md"] = "---\nname: bo\ndescription: Bo.\n---\n# Bo\n"
        r = run(self.build(m))
        self.assertIn("names no tier in tools/model_tiers.py", r.stdout)

    def test_frontmatter_mismatch_is_found(self):
        def m(f):
            f.agents["ann.md"] = (
                "---\nname: ann\ndescription: Ann.\nmodel: claude-sonnet-5\n---\n# Ann\n")
        r = run(self.build(m))
        self.assertIn("pins model: claude-sonnet-5", r.stdout)
        self.assertIn("require", r.stdout)

    def test_frontmatter_agreement_has_no_finding(self):
        def m(f):
            f.agents["ann.md"] = (
                "---\nname: ann\ndescription: Ann.\nmodel: claude-haiku-4-5-20251001\n"
                "---\n# Ann\n")
        r = run(self.build(m))
        self.assertIn("check_model: OK", r.stdout, r.stdout)

    def test_ledger_line_with_no_model_is_found(self):
        def m(f):
            f.ledger = "2026-08-30 00:00Z | RUN | run alpha | by: ron | result: ok\n"
        r = run(self.build(m))
        self.assertIn("names no model, tier requires claude-haiku-4-5-20251001", r.stdout)

    def test_ledger_line_with_wrong_model_is_found(self):
        def m(f):
            f.ledger = ("2026-08-30 00:00Z | RUN | run alpha | by: ron | result: ok "
                        "| model: claude-opus-5\n")
        r = run(self.build(m))
        self.assertIn("ran on claude-opus-5, tier requires claude-haiku-4-5-20251001", r.stdout)

    def test_ledger_line_with_matching_model_has_no_finding(self):
        def m(f):
            f.ledger = ("2026-08-30 00:00Z | RUN | run alpha | by: ron | result: ok "
                        "| model: claude-haiku-4-5-20251001\n")
        r = run(self.build(m))
        self.assertIn("check_model: OK", r.stdout, r.stdout)
        self.assertIn("1 of 1 governed ledger line(s)", r.stdout)

    def test_advisory_by_default_strict_blocks(self):
        def m(f):
            f.ledger = "2026-08-30 00:00Z | RUN | run alpha | by: ron | result: ok\n"
        root = self.build(m)
        self.assertEqual(run(root).returncode, 0, "advisory must exit 0")
        self.assertEqual(run(root, "--strict").returncode, 1, "--strict must block")


class TestScopeHonesty(unittest.TestCase):
    """The gate auditing itself: what would it report if it looked at nothing?"""

    def test_empty_tree_is_not_a_clean_machine(self):
        """This is the gate's own real refusal witness, not a synthetic result."""
        with tempfile.TemporaryDirectory() as td:
            r = run(td)
            self.assertIn("SCOPE EMPTY", r.stdout)
            self.assertNotIn("check_model: OK", r.stdout)
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "check_model.py"),
                 "--root", td, "--strict"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_ledger_window_with_no_governed_lines_says_so(self):
        with tempfile.TemporaryDirectory() as td:
            f = Fixture(td)
            f.ledger = "2020-01-01 00:00Z | NOTE | ancient | by: ron | result: ok\n"
            r = run(f.write())
            self.assertIn("NO governed ledger lines in the window at all", r.stdout)

    def test_every_reported_count_carries_a_denominator(self):
        with tempfile.TemporaryDirectory() as td:
            r = run(Fixture(td).write())
            checked = 0
            for line in r.stdout.splitlines():
                if line.strip().startswith(("coverage:", "frontmatter:", "ledger:")):
                    checked += 1
                    self.assertTrue(" of " in line, f"count with no denominator: {line}")
            self.assertEqual(checked, 3, f"expected 3 examined lines, saw {checked}")


if __name__ == "__main__":
    unittest.main()
