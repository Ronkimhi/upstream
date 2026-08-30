#!/usr/bin/env python3
"""Exact tests for Adam's machine audit. Every audit must be able to FAIL."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from check_machine import gate_refusal_certifications  # noqa: E402


def run(root, *extra):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_machine.py"), "--root", str(root), *extra],
        capture_output=True, text=True)


class Fixture:
    """A minimal but STRUCTURALLY VALID machine, which each test then breaks in one place.

    Built rather than copied from the live repo: a fixture that inherits the repo's real
    backlog cannot show that an audit fires on one specific defect.
    """

    def __init__(self, td):
        self.root = Path(td)
        (self.root / "tools" / "tests").mkdir(parents=True)
        (self.root / ".claude" / "hooks").mkdir(parents=True)
        (self.root / ".claude" / "agents").mkdir(parents=True)
        (self.root / "docs").mkdir(parents=True)
        (self.root / "data" / "health").mkdir(parents=True)

        self.commands = [
            "| `run alpha` (agent: **Ann**, `.claude/agents/ann.md`) | x | y "
            "| checked by `tools/check_alpha.py` |",
        ]
        self.method = "- A routine is LIVE only after its first evidenced fire.\n"
        self.gates = {"check_alpha.py": "#!/usr/bin/env python3\n"}
        # The gate is invoked by a path that RESOLVES to tools/check_alpha.py, which is the
        # shape every real test in this repo uses. A bare 'check_alpha.py' would run whatever
        # file of that name the working directory holds, which is not the audited gate.
        self.tests = {"test_alpha.py": (
            "import subprocess\n"
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_refuses(self):\n"
            "        r = subprocess.run([str(ROOT / 'tools' / 'check_alpha.py')])\n"
            "        self.assertEqual(r.returncode, 1)\n"
        )}
        self.hooks = ["ann-gate.py"]
        self.registered = ["ann-gate.py"]
        self.agents = ["ann.md"]
        self.ledger = "2026-08-30 00:00Z | NOTE | run alpha by ann | by: ron | result: ok\n"
        self.sessions = {"routine_status": {"radar": "LIVE"},
                         "routine_evidence": {"radar": {"first_evidenced_fire": "2026-08-29"}}}

    def write(self):
        r = self.root
        (r / "CLAUDE.md").write_text(
            "| Command | Reads | Writes | Must verify |\n|---|---|---|---|\n"
            + "\n".join(self.commands) + "\n")
        (r / "docs" / "method.md").write_text(self.method)
        for name, body in self.gates.items():
            (r / "tools" / name).write_text(body)
        (r / "tools" / "validate.py").write_text("#\n")
        for name, body in self.tests.items():
            (r / "tools" / "tests" / name).write_text(body)
        for name in self.hooks:
            (r / ".claude" / "hooks" / name).write_text("#\n")
        for name in self.agents:
            (r / ".claude" / "agents" / name).write_text("# agent\n")
        (r / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [
            {"matcher": "*", "hooks": [
                {"type": "command", "command": f'python3 "${{CLAUDE_PROJECT_DIR}}/.claude/hooks/{n}"'}
                for n in self.registered]}]}}))
        (r / "data" / "ledger.md").write_text(self.ledger)
        (r / "data" / "health" / "sessions.json").write_text(json.dumps(self.sessions))
        return self.root


class TestAuditsCanFail(unittest.TestCase):
    """A gate that cannot fail is not a gate. One test per audit, each breaking one thing."""

    def build(self, mutate=None):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        f = Fixture(self.td.name)
        if mutate:
            mutate(f)
        return f.write()

    def test_clean_fixture_has_no_findings(self):
        r = run(self.build())
        self.assertIn("check_machine: OK", r.stdout, r.stdout)
        self.assertEqual(r.returncode, 0)

    def test_command_with_no_agent_is_found(self):
        def m(f):
            f.commands.append("| `run beta` | x | y | checked by `tools/check_alpha.py` |")
        r = run(self.build(m))
        self.assertIn("names no owning agent", r.stdout)

    def test_command_with_no_gate_is_found(self):
        def m(f):
            f.commands.append("| `run beta` (agent: **Bo**, `x`) | x | y | done carefully |")
        r = run(self.build(m))
        self.assertIn("names no tools/check_*.py", r.stdout)

    def test_gate_with_no_failure_test_is_found(self):
        def m(f):
            f.gates["check_beta.py"] = "#\n"
            f.tests["test_beta.py"] = (
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_names_only(self):\n"
                "        check_beta\n"
                "        self.assertTrue(True)\n"
            )
        r = run(self.build(m))
        self.assertIn("check_beta.py is named in test_beta.py, but no test method both",
                      r.stdout)

    def test_bare_gate_name_and_fake_returncode_do_not_certify_coverage(self):
        """A bare name plus a synthetic result is Cass's original spoof."""
        def m(f):
            f.tests = {"test_spoof.py": (
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_spoof(self):\n"
                "        check_alpha\n"
                "        r = type('R', (), {'returncode': 1})()\n"
                "        self.assertEqual(r.returncode, 1)\n"
            )}
        certs = gate_refusal_certifications(self.build(m))
        self.assertNotIn("check_alpha", certs)

    def test_indirect_gate_name_and_fake_returncode_do_not_certify_coverage(self):
        """Resolving a command variable is insufficient without executing it."""
        def m(f):
            f.tests = {"test_spoof.py": (
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_spoof(self):\n"
                "        command = ['check_alpha.py']\n"
                "        r = type('R', (), {'returncode': 1})()\n"
                "        self.assertNotEqual(r.returncode, 0)\n"
            )}
        certs = gate_refusal_certifications(self.build(m))
        self.assertNotIn("check_alpha", certs)

    def test_false_with_gate_as_later_argv_data_does_not_certify_coverage(self):
        def m(f):
            f.tests = {"test_spoof.py": (
                "import subprocess\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_spoof(self):\n"
                "        r = subprocess.run(['false', 'check_alpha.py'])\n"
                "        self.assertEqual(r.returncode, 1)\n"
            )}
        self.assertNotIn("check_alpha", gate_refusal_certifications(self.build(m)))

    def test_echo_with_gate_as_later_argv_data_does_not_certify_coverage(self):
        def m(f):
            f.tests = {"test_spoof.py": (
                "import subprocess\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_spoof(self):\n"
                "        r = subprocess.run(['echo', 'check_alpha.py'])\n"
                "        self.assertEqual(r.returncode, 1)\n"
            )}
        self.assertNotIn("check_alpha", gate_refusal_certifications(self.build(m)))

    def test_shell_text_mention_does_not_certify_coverage(self):
        def m(f):
            f.tests = {"test_spoof.py": (
                "import subprocess\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_spoof(self):\n"
                "        r = subprocess.run('echo check_alpha.py', shell=True)\n"
                "        self.assertEqual(r.returncode, 1)\n"
            )}
        self.assertNotIn("check_alpha", gate_refusal_certifications(self.build(m)))

    def test_bare_gate_filename_does_not_certify_coverage(self):
        """Cass 2026-08-30, generalised: naming a gate is not invoking it.

        `['check_alpha.py']` executes whatever file of that name the test's working directory
        holds. It is not the audited gate, it may not exist, and the audit cannot tell the
        difference. Only a path resolving to tools/<gate>.py certifies.
        """
        def m(f):
            f.tests = {"test_spoof.py": (
                "import subprocess\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_spoof(self):\n"
                "        r = subprocess.run(['check_alpha.py'])\n"
                "        self.assertEqual(r.returncode, 1)\n"
            )}
        self.assertNotIn("check_alpha", gate_refusal_certifications(self.build(m)))

    def test_cwd_relative_gate_path_does_not_certify_coverage(self):
        def m(f):
            f.tests = {"test_spoof.py": (
                "import subprocess\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_spoof(self):\n"
                "        r = subprocess.run(['./check_alpha.py'])\n"
                "        self.assertNotEqual(r.returncode, 0)\n"
            )}
        self.assertNotIn("check_alpha", gate_refusal_certifications(self.build(m)))

    def test_gate_name_outside_the_tools_directory_does_not_certify_coverage(self):
        """A same-named script anywhere else is a different file."""
        def m(f):
            f.tests = {"test_spoof.py": (
                "import subprocess\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_spoof(self):\n"
                "        r = subprocess.run([str(ROOT / 'fixtures' / 'check_alpha.py')])\n"
                "        self.assertEqual(r.returncode, 1)\n"
            )}
        self.assertNotIn("check_alpha", gate_refusal_certifications(self.build(m)))

    def test_unresolved_spoof_is_reported_as_an_uncovered_gate(self):
        """The audit must SAY the gate is uncovered, not merely decline to certify it."""
        def m(f):
            f.tests = {"test_spoof.py": (
                "import subprocess\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_spoof(self):\n"
                "        r = subprocess.run(['false', 'check_alpha.py'])\n"
                "        self.assertEqual(r.returncode, 1)\n"
            )}
        r = run(self.build(m))
        self.assertIn("gate falsifiability: 0 of 1 gate(s)", r.stdout)
        self.assertIn("no test method both", r.stdout)
        self.assertNotIn("check_machine: OK", r.stdout)

    def test_direct_subprocess_result_certifies_exact_gate(self):
        root = self.build()
        certs = gate_refusal_certifications(root)
        self.assertEqual(certs["check_alpha"], [("test_alpha.py", "test_refuses")])

    def test_python_then_gate_script_certifies_exact_gate(self):
        def m(f):
            f.tests = {"test_alpha.py": (
                "import subprocess\n"
                "import sys\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_refuses(self):\n"
                "        r = subprocess.run([sys.executable,\n"
                "                            str(ROOT / 'tools' / 'check_alpha.py')])\n"
                "        self.assertEqual(r.returncode, 1)\n"
            )}
        certs = gate_refusal_certifications(self.build(m))
        self.assertEqual(certs["check_alpha"], [("test_alpha.py", "test_refuses")])

    def test_direct_imported_gate_function_result_certifies_exact_gate(self):
        def m(f):
            f.tests = {"test_alpha.py": (
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_refuses(self):\n"
                "        from check_alpha import main as invoke_alpha\n"
                "        r = invoke_alpha()\n"
                "        self.assertEqual(r, 1)\n"
            )}
        certs = gate_refusal_certifications(self.build(m))
        self.assertEqual(certs["check_alpha"], [("test_alpha.py", "test_refuses")])

    def test_two_gates_one_generic_assert_does_not_spoof_coverage(self):
        """One result variable must not certify two separately invoked gates."""
        def m(f):
            f.gates["check_beta.py"] = "#\n"
            f.tests = {"test_spoof.py": (
                "import subprocess\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_generic(self):\n"
                "        r = subprocess.run([str(ROOT / 'tools' / 'check_alpha.py')])\n"
                "        r = subprocess.run([str(ROOT / 'tools' / 'check_beta.py')])\n"
                "        self.assertEqual(r.returncode, 1)\n"
            )}
        root = self.build(m)
        certs = gate_refusal_certifications(root)
        self.assertNotIn("check_alpha", certs)
        self.assertNotIn("check_beta", certs)
        r = run(root)
        self.assertIn("check_alpha.py", r.stdout)
        self.assertIn("check_beta.py", r.stdout)

    def test_gate_named_in_no_test_is_found(self):
        def m(f):
            f.gates["check_gamma.py"] = "#\n"
        r = run(self.build(m))
        self.assertIn("check_gamma.py is named in no test file", r.stdout)

    def test_registered_hook_missing_from_disk_is_found(self):
        def m(f):
            f.registered.append("ghost-gate.py")
        r = run(self.build(m))
        self.assertIn("registered in settings.json but absent", r.stdout)

    def test_hook_on_disk_registered_nowhere_is_found(self):
        def m(f):
            f.hooks.append("orphan-gate.py")
        r = run(self.build(m))
        self.assertIn("registered nowhere", r.stdout)

    def test_silent_agent_is_found(self):
        def m(f):
            f.agents.append("zed.md")
        r = run(self.build(m))
        self.assertIn("agent zed has no ledger evidence", r.stdout)

    def test_registered_but_never_fired_routine_is_found(self):
        def m(f):
            f.sessions["routine_status"]["smoke"] = "REGISTERED"
        r = run(self.build(m))
        self.assertIn("REGISTERED IS NOT LIVE", r.stdout)

    def test_live_routine_with_no_evidence_is_found(self):
        def m(f):
            f.sessions["routine_status"]["digest"] = "LIVE"
        r = run(self.build(m))
        self.assertIn("marked LIVE with no routine_evidence", r.stdout)


class TestScopeHonesty(unittest.TestCase):
    """The audit auditing itself: what would it report if it looked at nothing?"""

    def test_direct_check_machine_refusal_is_observed(self):
        """This is the audit's own real gate-refusal witness, not a synthetic result."""
        with tempfile.TemporaryDirectory() as td:
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "check_machine.py"),
                 "--root", td, "--strict"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_empty_tree_is_not_a_clean_machine(self):
        with tempfile.TemporaryDirectory() as td:
            r = run(td)
            self.assertIn("SCOPE EMPTY", r.stdout)
            self.assertNotIn("check_machine: OK", r.stdout)
            self.assertEqual(run(td, "--strict").returncode, 1)

    def test_unparseable_command_table_is_a_finding_not_a_pass(self):
        with tempfile.TemporaryDirectory() as td:
            f = Fixture(td)
            f.commands = []
            root = f.write()
            r = run(root)
            self.assertIn("parsed to 0 rows", r.stdout)
            self.assertNotIn("check_machine: OK", r.stdout)

    def test_ledger_window_with_no_lines_says_so(self):
        with tempfile.TemporaryDirectory() as td:
            f = Fixture(td)
            f.ledger = "2020-01-01 00:00Z | NOTE | ancient | by: ron | result: ok\n"
            r = run(f.write())
            self.assertIn("NO ledger lines in the window at all", r.stdout)

    def test_advisory_by_default_strict_blocks(self):
        def m(f):
            f.commands.append("| `run beta` | x | y | done carefully |")
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td)
            m(fx)
            root = fx.write()
            self.assertEqual(run(root).returncode, 0, "advisory must exit 0")
            self.assertEqual(run(root, "--strict").returncode, 1, "--strict must block")

    def test_every_reported_count_carries_a_denominator(self):
        """Rule 21 as a test: no examined line may state a bare count."""
        with tempfile.TemporaryDirectory() as td:
            r = run(Fixture(td).write())
            checked = 0
            for line in r.stdout.splitlines():
                if line.strip().startswith(("promise ledger", "owners:", "gate falsifiability",
                                            "hooks:", "agents:", "routines:")):
                    checked += 1
                    self.assertTrue(" of " in line or "declared" in line,
                                    f"count with no denominator: {line}")
            # The test's own denominator: asserting over zero lines would pass silently, which
            # is the exact defect this test exists to forbid.
            # 7, not 6: the promise ledger emits two lines, the exact-command one and the
            # labelled prose heuristic. Pinned so a dropped audit shows up here as a count
            # change rather than as silence.
            self.assertEqual(checked, 7, f"expected 7 examined lines, saw {checked}")


class TestDigestValidation(unittest.TestCase):
    """Digest omissions must fail closed, not warn."""

    def _validate(self, digest):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data" / "digest").mkdir(parents=True)
            (root / "data" / "digest" / "2026-35.json").write_text(json.dumps(digest))
            return subprocess.run(
                [sys.executable, str(ROOT / "tools" / "validate.py"), "--root", str(root)],
                capture_output=True, text=True)

    def test_digest_missing_generated_by_and_machine_fails_closed(self):
        r = self._validate({"week": "2026-35", "summary": "x", "ranked": []})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("no generated_by", r.stdout)
        self.assertIn("no machine section", r.stdout)

    def test_digest_machine_section_requires_adam_fields(self):
        r = self._validate({
            "week": "2026-35",
            "summary": "x",
            "ranked": [],
            "generated_by": "adam-gm",
            "machine": {
                "as_of": "2026-08-30",
                "examined": {"commands": 1},
                "v8_reachable": True,
            },
        })
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        for key in ("findings_by_category", "promise_ledger_backlog",
                    "changed_since_last_week", "next_action"):
            self.assertIn(f"machine missing {key}", r.stdout)

    def test_live_digest_passes_machine_validation(self):
        live = json.loads((ROOT / "data" / "digest" / "2026-35.json").read_text())
        r = self._validate(live)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
