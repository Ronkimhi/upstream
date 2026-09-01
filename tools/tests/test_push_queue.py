#!/usr/bin/env python3
"""The push queue and the coded conflict rules, proven on real git repos.

Every test builds a bare origin plus two clones and manufactures the exact collision the
machine hit on 2026-09-01. The property held hardest is the REFUSAL: a conflicted path
with no coded rule must stop the machine, because a resolver that guesses is how live
data dies looking tidy.
"""
import fcntl
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RESOLVER = ROOT / "tools" / "resolve_conflicts.py"
SAFE_PUSH = ROOT / "tools" / "safe_push.py"


def git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r


class TwoWriters:
    """A bare origin and two clones, both configured, both starting from one commit."""

    def __init__(self, td):
        base = Path(td)
        self.origin = base / "origin.git"
        git(base, "init", "--bare", "-q", "-b", "main", str(self.origin))
        # Seed through a FIRST, then clone b, so both clones track origin/main and
        # the branch name never depends on the host's init.defaultBranch.
        self.a = base / "a"
        git(base, "clone", "-q", str(self.origin), str(self.a))
        git(self.a, "config", "user.email", "t@example.com")
        git(self.a, "config", "user.name", "t")
        git(self.a, "checkout", "-q", "-b", "main")
        (self.a / "data").mkdir()
        # The union attribute ships with the repo, so the fixture carries it too.
        (self.a / ".gitattributes").write_text("data/ledger.md merge=union\n")
        (self.a / "data" / "ledger.md").write_text("2026-09-01 00:00Z | NOTE | seed\n")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-q", "-m", "seed")
        git(self.a, "push", "-q", "-u", "origin", "main")
        self.b = base / "b"
        git(base, "clone", "-q", str(self.origin), str(self.b))
        git(self.b, "config", "user.email", "t@example.com")
        git(self.b, "config", "user.name", "t")

    def commit_all(self, clone, msg):
        git(clone, "add", "-A")
        git(clone, "commit", "-q", "-m", msg)

    def race(self, path_writer_a, path_writer_b):
        """b pushes first; a's push is rejected and a rebases into the conflict."""
        path_writer_b(self.b)
        self.commit_all(self.b, "b side")
        git(self.b, "push", "-q")
        path_writer_a(self.a)
        self.commit_all(self.a, "a side")
        self.assertion_push_rejected = git(self.a, "push", check=False).returncode != 0
        return git(self.a, "pull", "--rebase", check=False)


class TestCodedRules(unittest.TestCase):
    def test_ledger_union_needs_no_resolver_at_all(self):
        """The most frequent collision resolves inside git itself: merge=union keeps
        both appended lines in every venue, the Actions runner included."""
        with tempfile.TemporaryDirectory() as td:
            fx = TwoWriters(td)
            r = fx.race(
                lambda c: (c / "data" / "ledger.md").open("a").write(
                    "2026-09-01 01:00Z | NOTE | from a\n"),
                lambda c: (c / "data" / "ledger.md").open("a").write(
                    "2026-09-01 01:00Z | NOTE | from b\n"))
            self.assertEqual(0, r.returncode, r.stderr)
            text = (fx.a / "data" / "ledger.md").read_text()
            self.assertIn("from a", text)
            self.assertIn("from b", text)

    def test_requests_rows_union_and_transition_wins(self):
        with tempfile.TemporaryDirectory() as td:
            fx = TwoWriters(td)
            seed = {"version": 1, "requests": [
                {"id": "REQ-1", "status": "PENDING", "kind": "prices", "ticker": "X"}]}
            (fx.a / "data" / "requests.json").write_text(json.dumps(seed, indent=1))
            fx.commit_all(fx.a, "seed requests")
            git(fx.a, "push", "-q")
            git(fx.b, "pull", "-q")

            def side_a(c):  # a session appends a new PENDING row
                d = json.loads((c / "data" / "requests.json").read_text())
                d["requests"].append({"id": "REQ-2", "status": "PENDING",
                                      "kind": "prices", "ticker": "Y"})
                (c / "data" / "requests.json").write_text(json.dumps(d, indent=1))

            def side_b(c):  # the fetcher transitions the seed row
                d = json.loads((c / "data" / "requests.json").read_text())
                d["requests"][0]["status"] = "FULFILLED"
                d["requests"][0]["attempts"] = 1
                (c / "data" / "requests.json").write_text(json.dumps(d, indent=1))

            fx.race(side_a, side_b)
            rr = subprocess.run([sys.executable, str(RESOLVER), "--continue-rebase",
                                 "--root", str(fx.a)], capture_output=True, text=True)
            self.assertEqual(0, rr.returncode, rr.stdout + rr.stderr)
            d = json.loads((fx.a / "data" / "requests.json").read_text())
            by_id = {r["id"]: r for r in d["requests"]}
            self.assertEqual({"REQ-1", "REQ-2"}, set(by_id))
            self.assertEqual("FULFILLED", by_id["REQ-1"]["status"],
                             "the transitioned status is the later fact and must win")

    def test_fetcher_documents_take_the_newer_fetch(self):
        with tempfile.TemporaryDirectory() as td:
            fx = TwoWriters(td)
            doc = lambda ts: json.dumps({"ticker": "NVDA", "fetched_at": ts, "text": ts})
            fx.race(
                lambda c: (lambda p: (p.parent.mkdir(parents=True, exist_ok=True),
                                      p.write_text(doc("2026-09-01T01:00:00+00:00"))))(
                    c / "data" / "edgar" / "docs" / "NVDA.json"),
                lambda c: (lambda p: (p.parent.mkdir(parents=True, exist_ok=True),
                                      p.write_text(doc("2026-09-01T02:00:00+00:00"))))(
                    c / "data" / "edgar" / "docs" / "NVDA.json"))
            rr = subprocess.run([sys.executable, str(RESOLVER), "--continue-rebase",
                                 "--root", str(fx.a)], capture_output=True, text=True)
            self.assertEqual(0, rr.returncode, rr.stdout + rr.stderr)
            kept = json.loads((fx.a / "data" / "edgar" / "docs" / "NVDA.json").read_text())
            self.assertEqual("2026-09-01T02:00:00+00:00", kept["fetched_at"])

    def test_a_path_with_no_rule_is_refused_not_guessed(self):
        """The safety property. data/chains/ holds judgment work; no code may pick a side."""
        with tempfile.TemporaryDirectory() as td:
            fx = TwoWriters(td)
            (fx.a / "data" / "chains").mkdir()
            (fx.a / "data" / "chains" / "x.json").write_text('{"v": 0}')
            fx.commit_all(fx.a, "seed chain")
            git(fx.a, "push", "-q")
            git(fx.b, "pull", "-q")
            fx.race(
                lambda c: (c / "data" / "chains" / "x.json").write_text('{"v": "a"}'),
                lambda c: (c / "data" / "chains" / "x.json").write_text('{"v": "b"}'))
            rr = subprocess.run([sys.executable, str(RESOLVER), "--continue-rebase",
                                 "--root", str(fx.a)], capture_output=True, text=True)
            self.assertEqual(1, rr.returncode)
            self.assertIn("REFUSED data/chains/x.json", rr.stdout)
            self.assertTrue((fx.a / ".git" / "rebase-merge").exists() or
                            (fx.a / ".git" / "rebase-apply").exists(),
                            "the rebase must be left in place for a human")


class TestPushQueue(unittest.TestCase):
    def test_a_held_lock_queues_and_then_times_out_honestly(self):
        with tempfile.TemporaryDirectory() as td:
            fx = TwoWriters(td)
            lock_path = fx.a / ".git" / "upstream-push.lock"
            with open(lock_path, "w") as lk:
                fcntl.flock(lk, fcntl.LOCK_EX)
                r = subprocess.run([sys.executable, str(SAFE_PUSH), "--root", str(fx.a),
                                    "--lock-timeout", "2"],
                                   capture_output=True, text=True, timeout=30)
                self.assertEqual(1, r.returncode)
                self.assertIn("queue", r.stdout.lower())

    def test_safe_push_lands_a_racing_ledger_append(self):
        """End to end: b pushes first, a commits, safe_push rebases (union fires inside
        git), pushes, and origin holds BOTH lines."""
        with tempfile.TemporaryDirectory() as td:
            fx = TwoWriters(td)
            (fx.b / "data" / "ledger.md").open("a").write("line-from-b\n")
            fx.commit_all(fx.b, "b")
            git(fx.b, "push", "-q")
            (fx.a / "data" / "ledger.md").open("a").write("line-from-a\n")
            fx.commit_all(fx.a, "a")
            r = subprocess.run([sys.executable, str(SAFE_PUSH), "--root", str(fx.a)],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(0, r.returncode, r.stdout + r.stderr)
            show = git(fx.a, "show", "origin/main:data/ledger.md").stdout
            self.assertIn("line-from-a", show)
            self.assertIn("line-from-b", show)

    def test_safe_push_refuses_to_stack_on_an_existing_rebase(self):
        with tempfile.TemporaryDirectory() as td:
            fx = TwoWriters(td)
            (fx.a / ".git" / "rebase-merge").mkdir()
            r = subprocess.run([sys.executable, str(SAFE_PUSH), "--root", str(fx.a)],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(1, r.returncode)
            self.assertIn("already in progress", r.stdout)


if __name__ == "__main__":
    unittest.main()
