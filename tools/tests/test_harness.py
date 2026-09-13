#!/usr/bin/env python3
"""Exact tests for Hitch: the trace miner, the harness audit and the report schema.

Every audit must be able to FAIL, and the miner must never copy transcript text: this repo is
public and a transcript holds everything a session saw.
"""
import copy
import datetime
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_harness  # noqa: E402
from harness_traces import mine  # noqa: E402
from model_tiers import tier_for  # noqa: E402
from queue_allowlist import is_allowed, reject_reason  # noqa: E402

NOW = datetime.datetime.now(datetime.timezone.utc)
SID = "11111111-1111-4111-8111-111111111111"
REF = f"{SID}:22222222-2222-4222-8222-222222222222"
HOOK_CMD = 'python3 "${CLAUDE_PROJECT_DIR}/.claude/hooks/ann-gate.py"'


def line(kind, days_ago=0.0, **fields):
    when = (NOW - datetime.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return json.dumps({"type": kind, "sessionId": SID, "uuid": str(uuid.uuid4()),
                       "timestamp": when, **fields})


def assistant(model="claude-sonnet-5", mid=None, days_ago=0.0, tool=None):
    content = ([{"type": "tool_use", "id": tool, "name": "Bash", "input": {"command": "SECRET-INPUT"}}]
               if tool else [{"type": "text", "text": "SECRET-TEXT"}])
    return line("assistant", days_ago, message={
        "id": mid or str(uuid.uuid4()), "model": model, "content": content,
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100,
                  "cache_creation_input_tokens": 1}})


def crash(days_ago=0.0):
    return line("attachment", days_ago, attachment={
        "type": "hook_non_blocking_error", "hookEvent": "Stop", "command": HOOK_CMD, "exitCode": 1,
        "stderr": "Traceback SECRET-STDERR /Users/someone/x.py"})


def write_transcripts(base: Path, main_lines, sub_lines=None, agent_type="ann"):
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{SID}.jsonl").write_text("\n".join(main_lines) + "\n")
    if sub_lines is not None:
        sub = base / SID / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "agent-a1.jsonl").write_text("\n".join(sub_lines) + "\n")
        (sub / "agent-a1.meta.json").write_text(json.dumps({"agentType": agent_type}))
    return base


def valid_report():
    return {
        "id": "HAR-2026-W37", "as_of": "2026-09-13", "generated_by": "hitch-harness",
        "window": {"from": "2026-09-06", "to": "2026-09-13", "days": 7},
        "scope": {"sessions": 11},
        "audits": [{"name": "traces", "examined": "11 sessions", "found": 2}],
        "findings": [{
            "id": "2026-W37-F1", "rank": 1, "element": "hook",
            "evidence": {"metric": ["hooks", "crashes", "ann-gate.py", "count"], "count": 1,
                         "denominator": 1, "refs": [REF]},
            "first_failure": "the gate raises before it can decide",
            "diagnosis": "an unguarded read", "smallest_change": "guard the read",
            "prediction": {"metric": ["hooks", "crashes", "ann-gate.py", "count"], "op": "<=",
                           "value": 0, "window_days": 7},
            "regression_case": "a test feeding the gate the shape that crashed it",
            "status": "OPEN"}],
        "deletion_candidate": {"path": "CLAUDE.md", "reason": "a stale note"},
        "prior_predictions": [],
        "changelog": [{"ts": "2026-09-13T04:00:00Z", "change": "first run"}],
    }


class Fixture:
    """A minimal STRUCTURALLY CLEAN harness, which each test then breaks in one place."""

    def __init__(self, td):
        self.root = Path(td) / "repo"
        self.claude_md = ("| Command | Reads | Writes | Must verify |\n|---|---|---|---|\n"
                          "| `run alpha` (agent: **Ann**, `.claude/agents/ann.md`) | x | y "
                          "| checked by `tools/check_alpha.py` |\n")
        self.hook_test = "import unittest\n# runs .claude/hooks/ann-gate.py, asserts a block\n"
        self.ci = ('on:\n  push:\n    paths: ["tools/**", ".claude/**", "CLAUDE.md"]\n'
                   "jobs:\n  v:\n    steps:\n      - run: python3 tools/check_machine.py\n"
                   "      - run: python3 tools/check_model.py\n"
                   "      - run: python3 tools/check_harness.py\n")

    def write(self):
        r = self.root
        for d in ("tools/tests", ".claude/hooks", ".claude/agents", ".github/workflows"):
            (r / d).mkdir(parents=True, exist_ok=True)
        (r / "CLAUDE.md").write_text(self.claude_md)
        (r / ".claude" / "agents" / "ann.md").write_text(
            "---\nname: ann\ndescription: Ann.\nmodel: claude-sonnet-5\n---\n# Ann\n")
        (r / ".claude" / "hooks" / "ann-gate.py").write_text("#\n")
        (r / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [
            {"matcher": "*", "hooks": [{"type": "command", "command": HOOK_CMD}]}]}}))
        (r / "tools" / "check_alpha.py").write_text("#\n")
        (r / "tools" / "model_tiers.py").write_text(
            'TIER_MODEL = {"fast": "claude-sonnet-5", "strong": "claude-sonnet-5"}\n'
            'COMMAND_TIER = {"run alpha": "strong"}\n')
        (r / "tools" / "tests" / "test_ann_hook.py").write_text(self.hook_test)
        (r / ".github" / "workflows" / "ci.yml").write_text(self.ci)
        return r


def gate(root, tdir, *extra):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "check_harness.py"),
                           "--root", str(root), "--transcripts", str(tdir), *extra],
                          capture_output=True, text=True)


class TestMiner(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.base = Path(self.td.name) / "t"

    def test_counts_every_signal_and_copies_no_text(self):
        main = [
            assistant(tool="toolu_01"), assistant(mid="dup"), assistant(mid="dup"),
            line("user", message={"content": [{"type": "tool_result", "tool_use_id": "toolu_01",
                                               "is_error": True,
                                               "content": "Permission to use Bash has been denied. SECRET-OUT"}]}),
            crash(),
            line("attachment", attachment={"type": "hook_blocking_error", "hookEvent": "SubagentStop",
                                           "blockingError": "This session wrote a signal card. SECRET-BLOCK"}),
            line("system", subtype="api_error", error={"message": "SECRET-API"}),
            line("system", subtype="stop_hook_summary", hookInfos=[{"command": HOOK_CMD}]),
            '{"type":"system", this line is not json',
        ]
        write_transcripts(self.base, main, [assistant(model="claude-opus-5")])
        out = mine(self.base, days=7, known_agents={"ann"})
        self.assertEqual(out["hooks"]["crashes"]["ann-gate.py"]["count"], 1)
        self.assertEqual(out["hooks"]["runs"], {"ann-gate.py": 1})
        self.assertEqual(out["hooks"]["blocks"]["radar-gate.py"]["count"], 1)
        self.assertEqual(out["tools"]["errors"], {"Bash": 1})
        self.assertEqual(out["tools"]["error_classes"], {"permission": 1})
        self.assertEqual(out["api_errors"]["count"], 1)
        self.assertEqual(out["scope"]["unparsed"], 1)
        self.assertEqual(out["scope"]["assistant_messages"], 3, "a repeated message id counts once")
        self.assertEqual(out["models"]["by_agent_type"], {"ann": {"claude-opus-5": 1}})
        self.assertTrue(all(check_harness.REF_RE.match(x)
                            for x in out["hooks"]["crashes"]["ann-gate.py"]["refs"]))
        blob = json.dumps(out)
        for secret in ("SECRET", "Traceback", "/Users/", "Permission to use"):
            self.assertNotIn(secret, blob)

    def test_lines_older_than_the_window_are_not_counted(self):
        write_transcripts(self.base, [assistant(), crash(days_ago=30)])
        self.assertEqual(mine(self.base, days=7)["hooks"]["crashes"], {})

    def test_agent_types_outside_the_roster_collapse_to_other(self):
        write_transcripts(self.base, [assistant()], [assistant()], agent_type="plugin:private-agent")
        self.assertEqual(list(mine(self.base, days=7)["models"]["by_agent_type"]), ["other"])


class TestAuditsCanFail(unittest.TestCase):
    """One test per audit, each breaking one thing in an otherwise clean fixture."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.fx = Fixture(self.td.name)
        self.tdir = Path(self.td.name) / "t"

    def test_clean_fixture_with_quiet_transcripts_is_ok(self):
        root = self.fx.write()
        write_transcripts(self.tdir, [assistant()])
        r = gate(root, self.tdir)
        self.assertIn("check_harness: OK", r.stdout, r.stdout)
        self.assertEqual(r.returncode, 0)

    def test_no_transcripts_is_scope_empty_and_never_ok(self):
        r = gate(self.fx.write(), Path(self.td.name) / "absent")
        self.assertIn("SCOPE EMPTY", r.stdout)
        self.assertNotIn("check_harness: OK", r.stdout)

    def test_hook_crash_is_found_and_strict_refuses(self):
        root = self.fx.write()
        write_transcripts(self.tdir, [assistant(), crash()])
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_harness.py"),
                            "--root", str(root), "--transcripts", str(self.tdir), "--strict"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("hook ann-gate.py crashed 1 time(s)", r.stdout)

    def test_transcript_format_drift_is_found(self):
        write_transcripts(self.tdir, [crash()])
        r = check_harness.Report()
        check_harness.audit_traces(r, mine(self.tdir, days=7))
        self.assertTrue(any("format has likely changed" in f for f in r.findings), r.findings)

    def test_agent_answering_off_its_tier_is_found(self):
        root = self.fx.write()
        write_transcripts(self.tdir, [assistant()], [assistant(model="claude-opus-5")])
        r = check_harness.Report()
        check_harness.audit_models(root, r, mine(self.tdir, days=7, known_agents={"ann"}),
                                   check_harness.load_tiers(root))
        self.assertTrue(any("agent ann answered 1 message(s) on ['claude-opus-5']" in f
                            for f in r.findings), r.findings)

    def test_budget_overrun_is_found(self):
        r = check_harness.Report()
        check_harness.audit_budget(self.fx.write(), r, budget={"CLAUDE.md": 10})
        self.assertTrue(any("over its 10-byte budget" in f for f in r.findings), r.findings)

    def test_cited_path_that_is_gone_is_found(self):
        self.fx.claude_md += "See `tools/check_gone.py`.\n"
        r = check_harness.Report()
        check_harness.audit_drift(self.fx.write(), r)
        self.assertTrue(any("tools/check_gone.py is cited" in f for f in r.findings), r.findings)

    def test_registered_hook_no_test_watches_is_found(self):
        self.fx.hook_test = "import unittest\n"
        r = check_harness.Report()
        check_harness.audit_drift(self.fx.write(), r)
        self.assertTrue(any("hook ann-gate.py is registered but no file" in f for f in r.findings))

    def test_ci_that_skips_harness_files_is_found(self):
        self.fx.ci = (self.fx.ci.replace(', ".claude/**", "CLAUDE.md"', "")
                      .replace("      - run: python3 tools/check_harness.py\n", ""))
        r = check_harness.Report()
        check_harness.audit_drift(self.fx.write(), r)
        found = " ".join(r.findings)
        for needle in ("omit .claude/**", "omit CLAUDE.md", "never runs tools/check_harness.py"):
            self.assertIn(needle, found)

    def test_codex_mirror_divergence_is_found(self):
        root = self.fx.write()
        (root / "AGENTS.md").write_text("see .Codex/agents/ann.md\n")
        r = check_harness.Report()
        check_harness.audit_drift(root, r)
        found = " ".join(r.findings)
        self.assertIn("mangled `.Codex/` paths", found)
        self.assertIn(".codex/hooks/ann-gate.py is missing", found)

    def test_fix_commit_touching_a_grader_is_found(self):
        root = self.fx.write()
        git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run(git + ["init", "-q"], check=True, capture_output=True)
        (root / "tools" / "check_harness.py").write_text("# the bar, moved\n")
        subprocess.run(git + ["add", "-A"], check=True, capture_output=True)
        subprocess.run(git + ["commit", "-q", "-m", "[run harness fix 2026-W37-F1] loosen"],
                       check=True, capture_output=True)
        r = check_harness.Report()
        check_harness.audit_self_edit(root, r)
        self.assertTrue(any("edited Hitch's own grader(s) ['tools/check_harness.py']" in f
                            for f in r.findings), r.lines)

    def test_prediction_that_did_not_hold_is_found(self):
        root = self.fx.write()
        report = valid_report()
        report["findings"][0].update(
            status="FIXED", fix_commit="abc1234", fail_before="test_guard failed",
            fixed_on=(NOW - datetime.timedelta(days=10)).date().isoformat())
        (root / "data" / "harness").mkdir(parents=True)
        (root / "data" / "harness" / "2026-W37.json").write_text(json.dumps(report))
        write_transcripts(self.tdir, [assistant(days_ago=2), crash(days_ago=2)])
        r = check_harness.Report()
        verdicts = check_harness.audit_predictions(root, r, self.tdir, NOW, set())
        self.assertEqual(verdicts[0]["verdict"], "DID_NOT_HOLD")
        self.assertTrue(any("DID_NOT_HOLD" in f for f in r.findings), r.findings)


class TestReportSchema(unittest.TestCase):
    def test_valid_report_passes(self):
        self.assertEqual(check_harness.validate_report(valid_report(), "2026-W37"), [])

    def test_transcript_text_paths_and_unfalsifiable_findings_are_refused(self):
        cases = {
            "absolute path": lambda o: o["findings"][0].update(diagnosis="see /Users/r/.claude/x.jsonl"),
            "over 600": lambda o: o.update(scope={"note": "x" * 601}),
            "evidence carries text": lambda o: o["findings"][0]["evidence"].update(metric=["hooks", "a\nb"]),
            "at most 3": lambda o: o.update(findings=[copy.deepcopy(o["findings"][0]) for _ in range(4)]),
            "refs must be": lambda o: o["findings"][0]["evidence"].update(refs=["not-a-ref"]),
            "FIXED without": lambda o: o["findings"][0].update(status="FIXED"),
            "prediction needs": lambda o: o["findings"][0]["prediction"].update(metric=["hookz", "x"]),
            "deletion_candidate needs": lambda o: o.update(deletion_candidate={}),
            "must be HAR-": lambda o: o.update(id="HAR-2026-W38"),
        }
        for needle, mutate in cases.items():
            with self.subTest(needle=needle):
                obj = valid_report()
                mutate(obj)
                errs = check_harness.validate_report(obj, "2026-W37")
                self.assertTrue(any(needle in e for e in errs), errs)

    def test_validate_registers_the_store(self):
        self.assertIn('("harness", DATA / "harness", "20*-W*.json", v_harness)',
                      (ROOT / "tools" / "validate.py").read_text())


class TestHarnessCommands(unittest.TestCase):
    def test_both_shapes_are_allowed_and_tiered(self):
        for command in ("run harness", "run harness fix 2026-W37-F1", "run harness fix 2027-W01-F3"):
            with self.subTest(command=command):
                self.assertTrue(is_allowed(command))
        self.assertEqual(tier_for("run harness"), "strong")
        self.assertEqual(tier_for("run harness fix"), "strong")

    def test_malformed_and_injected_commands_are_refused(self):
        for command in ("run harness fix 2026-W37-F4", "run harness fix 2026-37-F1", "run harness fix",
                        "run harness && echo pwned", "run harness fix 2026-W37-F1; rm -rf data",
                        "run harness fix ../CLAUDE.md", "run harness\nrun radar",
                        "run harness fix 2026-W37-F1 ", "run harnessfix 2026-W37-F1"):
            with self.subTest(command=command):
                self.assertFalse(is_allowed(command))
        self.assertIn("trailing text", reject_reason("run harness && echo pwned"))
        self.assertIn("control character", reject_reason("run harness fix 2026-W37-F1\nrun radar"))


if __name__ == "__main__":
    unittest.main()
