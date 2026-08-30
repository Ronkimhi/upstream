#!/usr/bin/env python3
"""Exact tests for the occurrence log, its clustering, and the gate over both.

Every test breaks a STRUCTURALLY VALID fixture in exactly one place, because a fixture that
inherits the live repo's state cannot show that a check fires on one specific defect.

The load-bearing one is `test_the_gate_refuses_a_log_that_stopped_ingesting`: an occurrence
log that quietly stopped taking new rows looks, from every count in the file, exactly like a
quiet week. That is the failure this whole store exists to make impossible.
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from theme_calibrate import _hit, iso_week, theme_match  # noqa: E402
from theme_calibrate import build as _build  # noqa: E402


def build(root):
    """The calibrator prints its denominator on every run, which is right in a postlude
    and is 20 lines of noise in a test suite."""
    with contextlib.redirect_stdout(io.StringIO()):
        return _build(root)

TODAY = datetime.now(timezone.utc).date()
TSTR = TODAY.isoformat()


def gate(root, *extra):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_themes.py"), "--root", str(root), *extra],
        capture_output=True, text=True)


class Fixture:
    """A minimal machine: one feed item, one theme that claims it, and a calibration
    computed by the real calibrator rather than typed here."""

    def __init__(self, td):
        self.root = Path(td)
        (self.root / "data" / "themes").mkdir(parents=True)
        (self.root / "data" / "feeds").mkdir(parents=True)
        (self.root / "data" / "signals").mkdir(parents=True)
        (self.root / "data" / "radar").mkdir(parents=True)
        (self.root / "data" / "calendar").mkdir(parents=True)
        self.write("data/feeds/latest.json", {
            "as_of": TSTR, "last_run": {},
            "items": [{"id": "aaaa1111bbbb2222", "source": "BBC World", "family": "GEO",
                       "ts": TSTR, "title": "Tanker traffic reroutes around the strait",
                       "url": "https://example.org/a", "summary": ""}]})
        self.write("data/radar/candidates.json", {"as_of": TSTR, "candidates": []})
        self.write("data/calendar/events.json", {"as_of": TSTR, "events": []})
        self.write("data/themes/themes.json", {
            "as_of": TSTR, "owner": "Tally", "themes": [{
                "id": "THM-01", "label": "Hormuz", "definition": "Tanker rerouting.",
                "created_at": TSTR, "created_by": "Tally",
                "match": {"any": ["tanker"], "all": [], "not": []}, "signal_refs": [],
                "changelog": [{"ts": TSTR + "T00:00:00Z", "by": "Tally", "change": "CREATE"}],
            }], "notes": [], "changelog": [
                {"ts": TSTR + "T00:00:00Z", "by": "Tally", "change": "CREATE"}]})
        build(self.root)
        self.ledger(f"{TSTR} 00:00Z | THEMES | run themes | by: ron | wrote: data/themes/ "
                    f"| result: logged: 1, assigned: 1 | health: 1/1 | artifact: skipped(test)")

    def write(self, rel, obj):
        (self.root / rel).write_text(json.dumps(obj, indent=1) + "\n")

    def read(self, rel):
        return json.loads((self.root / rel).read_text())

    def ledger(self, text):
        (self.root / "data" / "ledger.md").write_text(text + "\n")


class TestThemeGate(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.fx = Fixture(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_a_valid_store_passes(self):
        r = gate(self.fx.root)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("check_themes: OK", r.stdout)

    def test_the_gate_refuses_a_log_that_stopped_ingesting(self):
        """The defect this store exists to prevent. A feed item arrives, the log does not
        take it, and every count in the file still reads clean: 1 of 1 assigned, no
        unassigned, no findings. Indistinguishable from a quiet week."""
        feeds = self.fx.read("data/feeds/latest.json")
        feeds["items"].append({"id": "cccc3333dddd4444", "source": "Guardian World",
                               "family": "GEO", "ts": TSTR,
                               "title": "A second occurrence nobody logged",
                               "url": "https://example.org/b", "summary": ""})
        self.fx.write("data/feeds/latest.json", feeds)
        r = gate(self.fx.root)
        self.assertEqual(1, r.returncode, r.stdout)
        self.assertIn("no row in the log", r.stdout)

    def test_the_gate_refuses_an_assignment_with_no_basis(self):
        occ = self.fx.read("data/themes/occurrences.json")
        occ["occurrences"][0]["theme_basis"] = ""
        self.fx.write("data/themes/occurrences.json", occ)
        r = gate(self.fx.root)
        self.assertEqual(1, r.returncode, r.stdout)
        self.assertIn("no theme_basis", r.stdout)

    def test_the_gate_refuses_a_theme_that_does_not_exist(self):
        occ = self.fx.read("data/themes/occurrences.json")
        occ["occurrences"][0]["theme_id"] = "THM-99"
        occ["occurrences"][0]["theme_by"] = "Tally"
        self.fx.write("data/themes/occurrences.json", occ)
        r = gate(self.fx.root)
        self.assertEqual(1, r.returncode, r.stdout)
        self.assertIn("name a theme that does not exist", r.stdout)

    def test_the_gate_refuses_a_hand_edited_calibration(self):
        """Every number in the derived block is computed. A count typed by a session is
        the one thing this file must never be able to carry."""
        thm = self.fx.read("data/themes/themes.json")
        thm["calibration"]["denominators"]["assigned"] = 47
        self.fx.write("data/themes/themes.json", thm)
        r = gate(self.fx.root)
        self.assertEqual(1, r.returncode, r.stdout)
        self.assertIn("disagrees with a fresh count", r.stdout)

    def test_the_gate_refuses_a_duplicate_occurrence(self):
        occ = self.fx.read("data/themes/occurrences.json")
        clone = dict(occ["occurrences"][0])
        clone["id"] = "OCC-20260830-999"
        occ["occurrences"].append(clone)
        self.fx.write("data/themes/occurrences.json", occ)
        r = gate(self.fx.root)
        self.assertEqual(1, r.returncode, r.stdout)
        self.assertIn("logged twice", r.stdout)

    def test_the_gate_refuses_a_write_with_no_ledger_line(self):
        self.fx.ledger("# ledger with nothing today")
        r = gate(self.fx.root)
        self.assertEqual(1, r.returncode, r.stdout)
        self.assertIn("no THEMES ledger line", r.stdout)

    def test_the_gate_refuses_a_ledger_line_with_no_write(self):
        """The mirror image, and the one a Stop hook cannot catch: a THEMES line recording
        a run that produced nothing. Guarded here because a record of work that did not
        happen is worse than a missing record of work that did."""
        occ = self.fx.read("data/themes/occurrences.json")
        thm = self.fx.read("data/themes/themes.json")
        old = (TODAY - timedelta(days=3)).isoformat()
        occ["as_of"] = thm["as_of"] = old
        for entry in occ["changelog"] + thm["changelog"]:
            entry["ts"] = old + "T00:00:00Z"
        thm["calibration"]["generated_at"] = old + " 00:00Z"
        self.fx.write("data/themes/occurrences.json", occ)
        self.fx.write("data/themes/themes.json", thm)
        r = gate(self.fx.root)
        self.assertEqual(1, r.returncode, r.stdout)
        self.assertIn("neither store was touched", r.stdout)

    def test_an_absent_store_is_not_reported_as_a_pass(self):
        """A gate that returns OK over nothing trains the reader to ignore it."""
        empty = Path(self.td.name) / "empty"
        (empty / "data").mkdir(parents=True)
        r = gate(empty)
        self.assertEqual(0, r.returncode)
        self.assertIn("NO STORE", r.stdout)
        self.assertNotIn("check_themes: OK", r.stdout)

    def test_direct_check_themes_refusal_is_observed(self):
        """The audit's witness, and not a synthetic one: it runs the gate FILE and watches
        it exit 1. Every other test here goes through the `gate()` helper, which is easier
        to read and which tools/check_machine.py cannot certify, because a helper hides
        whether the thing that refused was really this gate."""
        occ = self.fx.read("data/themes/occurrences.json")
        occ["occurrences"][0]["theme_id"] = "THM-does-not-exist"
        occ["occurrences"][0]["theme_by"] = "Tally"
        self.fx.write("data/themes/occurrences.json", occ)
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "check_themes.py"),
             "--root", str(self.fx.root)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("name a theme that does not exist", result.stdout)

    def test_the_gate_always_prints_its_denominator(self):
        r = gate(self.fx.root)
        self.assertIn("occurrence(s) logged", r.stdout)
        self.assertIn("assigned across", r.stdout)


class TestThemeMatching(unittest.TestCase):
    """Whole-word matching, and the substring bug it replaced."""

    def test_a_short_term_does_not_match_inside_a_longer_word(self):
        """`"ai" in "ukraine"` is True, and the first draft used exactly that test, which
        would have filed every Ukraine headline under the AI buildout."""
        t = {"id": "THM-03", "match": {"any": ["ai"]}}
        self.assertEqual("", theme_match({"title": "Ukraine attacks Danube port"}, t))
        self.assertEqual("", theme_match({"title": "Air travel and rail"}, t))
        self.assertEqual("ai", theme_match({"title": "AI industry says"}, t))

    def test_multi_word_terms_still_match(self):
        t = {"id": "X", "match": {"any": ["northern sea route"]}}
        self.assertEqual("northern sea route",
                         theme_match({"title": "The Northern Sea Route opens"}, t))

    def test_a_not_term_wins_over_an_any_term(self):
        t = {"id": "X", "match": {"any": ["fire"], "not": ["theme park"]}}
        self.assertEqual("", theme_match({"title": "Fire at the theme park"}, t))
        self.assertEqual("fire", theme_match({"title": "Forest fire in Angola"}, t))

    def test_the_source_is_part_of_the_haystack(self):
        """Most physical-hazard items are only identifiable by which feed they came from,
        and the basis written on the row says so rather than pretending to read the title."""
        t = {"id": "X", "match": {"any": ["usgs significant quakes"]}}
        self.assertEqual("usgs significant quakes",
                         theme_match({"title": "M 5.8 - 4 km N of Toride, Japan",
                                      "source": "USGS significant quakes"}, t))

    def test_hit_is_case_insensitive_at_the_boundary(self):
        self.assertTrue(_hit("nato", "romania scrambles f-16s for nato"))
        self.assertFalse(_hit("nato", "natobank holdings"))

    def test_iso_week_refuses_to_guess(self):
        """An undated occurrence is counted as undated, never bucketed into this week,
        which would make a quiet week look busy."""
        self.assertEqual("2026-W35", iso_week("2026-08-26"))
        self.assertEqual("", iso_week(""))
        self.assertEqual("", iso_week("not a date"))


class TestIngestIdentity(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.fx = Fixture(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_ingest_is_idempotent(self):
        before = self.fx.read("data/themes/occurrences.json")["occurrences"]
        build(self.fx.root)
        build(self.fx.root)
        after = self.fx.read("data/themes/occurrences.json")["occurrences"]
        self.assertEqual([r["id"] for r in before], [r["id"] for r in after])

    def test_a_candidate_promotes_its_feed_row_instead_of_adding_a_second(self):
        """One occurrence in the world is one row. Without this, every count
        double-counts exactly the items that travelled furthest through the funnel."""
        cands = self.fx.read("data/radar/candidates.json")
        cands["candidates"].append({
            "id": "CAND-20260830-01", "date": TSTR, "title": "Tanker rerouting",
            "family": "GEO", "source_name": "BBC", "why": "x", "status": "AMBIENT",
            "added_by": "ron", "changelog": [], "first_feed_item_id": "aaaa1111bbbb2222"})
        self.fx.write("data/radar/candidates.json", cands)
        build(self.fx.root)
        rows = self.fx.read("data/themes/occurrences.json")["occurrences"]
        self.assertEqual(1, len(rows), [r["origin_ref"] for r in rows])
        self.assertEqual("candidate", rows[0]["origin"])
        self.assertEqual("aaaa1111bbbb2222", rows[0]["promoted_from_feed"])

    def test_the_snapshot_survives_the_feed_store_pruning(self):
        """The reason this is a store and not a view: latest.json prunes at 500 items over
        14 days, so a clustering computed over it would show last week and nothing else."""
        self.fx.write("data/feeds/latest.json", {"as_of": TSTR, "last_run": {}, "items": []})
        build(self.fx.root)
        rows = self.fx.read("data/themes/occurrences.json")["occurrences"]
        self.assertEqual(1, len(rows))
        self.assertEqual("Tanker traffic reroutes around the strait", rows[0]["title"])

    def test_a_hand_assignment_is_never_overwritten_by_a_rule(self):
        occ = self.fx.read("data/themes/occurrences.json")
        occ["occurrences"][0].update({"theme_id": None, "theme_by": "Tally",
                                      "theme_basis": "Ron read it and said so"})
        self.fx.write("data/themes/occurrences.json", occ)
        build(self.fx.root)
        row = self.fx.read("data/themes/occurrences.json")["occurrences"][0]
        self.assertEqual("Tally", row["theme_by"])
        self.assertIsNone(row["theme_id"])


if __name__ == "__main__":
    unittest.main()
