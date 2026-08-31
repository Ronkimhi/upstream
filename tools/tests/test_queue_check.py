#!/usr/bin/env python3
"""Tests for the republish-attribution check.

The property that matters is the REFUSAL, not the pass. This exists to let a session skip
fetching 1.7 MB when a republish was plainly its own build, and the cost of a wrong "skip"
is an undrained click that nobody ever sees. So every test below that matters asks whether
it refuses: an unattributed publish, a stale page, a missing ledger line, a malformed
version id. Attribution is the narrow case; everything else falls back to reading the page,
which is what was always correct.
"""
import datetime
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from queue_check import DEFAULT_SLACK, check  # noqa: E402

PUB = datetime.datetime(2026, 8, 31, 23, 50, 32, tzinfo=datetime.timezone.utc)
VERSION = f"{int(PUB.timestamp())}-3a5e"


class Fixture:
    def __init__(self, td, built="2026-08-31 23:50Z", ledger_at="2026-08-31 23:49Z",
                 republished=True):
        self.root = Path(td)
        (self.root / "app").mkdir(parents=True)
        (self.root / "data").mkdir(parents=True)
        (self.root / "app" / "index.html").write_text(
            'window.UPSTREAM_DATA = {"built_at":"%s","signals":[]};' % built)
        tail = "artifact: republished" if republished else "artifact: skipped(none)"
        (self.root / "data" / "ledger.md").write_text(
            f"{ledger_at} | NOTE | a build | by: ron | wrote: app/index.html | "
            f"result: x | health: n/a | {tail}\n")


class TestQueueCheck(unittest.TestCase):
    def run_on(self, **kw):
        with tempfile.TemporaryDirectory() as td:
            f = Fixture(td, **kw)
            return check(VERSION, f.root, DEFAULT_SLACK)

    def test_our_own_build_and_republish_is_attributed(self):
        """The one case it may skip: the page was built here seconds before the publish and
        the ledger says we republished. app/build.py writes an empty queue into every page
        it assembles, so the blocks are empty by construction."""
        ok, msg = self.run_on()
        self.assertTrue(ok, msg)
        self.assertIn("ATTRIBUTED", msg)

    def test_the_ledger_line_may_carry_a_different_minute(self):
        """The protocol writes the line, commits, pushes, then republishes last, so a page
        built at 23:50 is announced by a 23:49 line. Matching the build minute exactly
        refused a republish that was plainly ours."""
        ok, _ = self.run_on(ledger_at="2026-08-31 23:52Z")
        self.assertTrue(ok)

    def test_a_publish_we_did_not_build_is_refused(self):
        """The click case. A click is published by the page splicing into the block it
        already had; it never comes from a build, so the page on disk will not match."""
        ok, msg = self.run_on(built="2026-08-30 12:00Z")
        self.assertFalse(ok)
        self.assertIn("Read the live page", msg)

    def test_a_publish_with_no_republish_line_is_refused(self):
        ok, msg = self.run_on(republished=False)
        self.assertFalse(ok)
        self.assertIn("unattributed publish is the shape a click has", msg)

    def test_a_publish_before_our_build_is_refused(self):
        """Negative delta: the page on disk is NEWER than what was published, so the
        published bytes are somebody else's."""
        ok, msg = self.run_on(built="2026-09-01 04:00Z")
        self.assertFalse(ok)
        self.assertIn("not the bytes this repo built", msg)

    def test_a_stale_ledger_line_does_not_attribute_a_later_publish(self):
        ok, msg = self.run_on(ledger_at="2026-08-31 20:00Z")
        self.assertFalse(ok)
        self.assertIn("no ledger line within", msg)

    def test_a_malformed_version_id_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            f = Fixture(td)
            for bad in ("not-a-version", "", "abcd-1234", "-", "99999999999999999999-x"):
                ok, msg = check(bad, f.root, DEFAULT_SLACK)
                self.assertFalse(ok, f"attributed a malformed id: {bad!r}")

    def test_an_unreadable_repo_is_refused_not_assumed(self):
        """Fails toward the read, never toward the skip."""
        with tempfile.TemporaryDirectory() as td:
            ok, msg = check(VERSION, Path(td), DEFAULT_SLACK)
            self.assertFalse(ok)
            self.assertIn("no readable built_at", msg)


if __name__ == "__main__":
    unittest.main()
