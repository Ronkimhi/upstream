#!/usr/bin/env python3
"""Exact tests for the radar postlude gate, which had none.

Two of its checks exist to stop the gate passing over nothing, and those are the ones worth
holding down hardest:

  * the EMPTY DAY check: a scanner that silently stopped searching writes zero cards, which
    is byte-identical to a quiet week until you ask how many feed items it examined.
  * the UNCLAIMED SURGE check: volume arriving under a theme with no signal card behind it.
    Before it, the clustering was a report nobody had to answer.

Every test builds a structurally valid radar day and breaks it in exactly one place.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TODAY = datetime.now(timezone.utc).date().isoformat()

LEDGER = (f"{TODAY} 08:00Z | RADAR | run radar | by: routine | wrote: data/signals/ | "
          f"result: 1 card. taste rules applied: 3 filtered. click queue: empty, checked "
          f"first | health: 1/1 | artifact: republished")


def gate(root, *extra):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_radar.py"), "--root", str(root), *extra],
        capture_output=True, text=True)


class Fixture:
    def __init__(self, td):
        self.root = Path(td)
        for d in ("signals", "radar", "calendar", "shadow", "themes"):
            (self.root / "data" / d).mkdir(parents=True, exist_ok=True)
        self.write("data/signals/SIG-20260830-01.json", {
            "id": "SIG-20260830-01", "created_at": TODAY, "updated_at": TODAY,
            "title": "A card", "status": "NEW",
            "occurrence": {"kind": "UNDERWAY", "anchor_date": "2026-02-28",
                           "window": "x", "label": "y"},
            "evidence": [{"claim": "a", "source_name": "s", "source_date": "2026-08-01", "tag": "VERIFIED"},
                         {"claim": "b", "source_name": "s", "source_date": "2026-08-02", "tag": "VERIFIED"}]})
        self.write("data/calendar/events.json", {"as_of": TODAY, "events": []})
        self.write("data/radar/candidates.json", {"as_of": TODAY, "candidates": []})
        self.write("data/shadow/book.json", {"rows": []})
        self.scout_log(spot_tests=[])
        self.themes(unclaimed=[])
        self.ledger(LEDGER)

    def write(self, rel, obj):
        (self.root / rel).write_text(json.dumps(obj, indent=1) + "\n")

    def ledger(self, text):
        (self.root / "data" / "ledger.md").write_text(text + "\n")

    def scout_log(self, spot_tests, notes=None, examined=317):
        self.write("data/radar/scout-log.json", {
            "as_of": TODAY, "proposed_rules": [], "repairs": [], "changelog": [],
            "spot_tests": spot_tests, "notes": notes or [],
            "calibration": {"generated_at": TODAY + " 08:00Z", "conversion": {},
                            "denominators": {"feed_items_examined": examined}}})

    def themes(self, unclaimed):
        """`unclaimed` is a list of theme ids that are surging with no card behind them."""
        self.write("data/themes/themes.json", {
            "as_of": TODAY, "themes": [], "changelog": [],
            "calibration": {"generated_at": TODAY + " 08:00Z", "denominators": {},
                            "per_theme": {}, "weeks": [],
                            "surges": [{"theme_id": t, "label": t, "week": "2026-W35",
                                        "count": 23, "baseline": 0.5, "claimed": False}
                                       for t in unclaimed]}})


class TestRadarGate(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.fx = Fixture(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_a_clean_radar_day_passes(self):
        r = gate(self.fx.root)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("check_radar: OK", r.stdout)

    def test_a_day_with_no_run_is_not_reported_as_a_pass(self):
        self.fx.ledger("# nothing today")
        (self.fx.root / "data" / "signals" / "SIG-20260830-01.json").unlink()
        r = gate(self.fx.root)
        self.assertEqual(0, r.returncode)
        self.assertIn("NOT RUN TODAY", r.stdout)
        self.assertNotIn("check_radar: OK", r.stdout)

    def test_an_unclaimed_surge_must_be_answered(self):
        """The check that turns the clustering from a report into a lead."""
        self.fx.themes(unclaimed=["THM-08"])
        r = gate(self.fx.root)
        self.assertEqual(1, r.returncode, r.stdout)
        self.assertIn("unclaimed surge(s) went unanswered", r.stdout)
        self.assertIn("THM-08", r.stdout)

    def test_declining_a_surge_in_writing_is_a_real_answer(self):
        """Most weeks most surges are noise. What is refused is silence, not a decline."""
        self.fx.themes(unclaimed=["THM-08"])
        self.fx.scout_log(spot_tests=[{
            "ts": TODAY + "T09:00:00Z", "rule": "THM-08 unclaimed surge",
            "verdict": "DECLINED",
            "note": "23 GDACS green fire notifications in one week is the feed's baseline "
                    "noise, not an occurrence. No card."}])
        r = gate(self.fx.root)
        self.assertEqual(0, r.returncode, r.stdout)
        self.assertIn("1 answered in today's scout log", r.stdout)

    def test_a_decline_dated_another_day_does_not_answer_today(self):
        """A stale note would let one week's decline silence every week after it."""
        self.fx.themes(unclaimed=["THM-08"])
        self.fx.scout_log(spot_tests=[{
            "ts": "2026-08-01T09:00:00Z", "rule": "THM-08 unclaimed surge",
            "verdict": "DECLINED", "note": "old"}])
        r = gate(self.fx.root)
        self.assertEqual(1, r.returncode, r.stdout)
        self.assertIn("THM-08", r.stdout)

    def test_a_claimed_surge_needs_no_answer(self):
        self.fx.write("data/themes/themes.json", {
            "as_of": TODAY, "themes": [], "changelog": [],
            "calibration": {"generated_at": TODAY + " 08:00Z", "denominators": {},
                            "per_theme": {}, "weeks": [],
                            "surges": [{"theme_id": "THM-01", "label": "x", "week": "2026-W35",
                                        "count": 13, "baseline": 0.5, "claimed": True,
                                        "signal_refs": ["SIG-20260830-01"]}]}})
        r = gate(self.fx.root)
        self.assertEqual(0, r.returncode, r.stdout)
        self.assertIn("0 of 1 surge(s) unclaimed", r.stdout)

    def test_a_missing_theme_store_says_so_rather_than_passing_quietly(self):
        (self.fx.root / "data" / "themes" / "themes.json").unlink()
        r = gate(self.fx.root)
        self.assertEqual(0, r.returncode, r.stdout)
        self.assertIn("no theme store on disk", r.stdout)

    def test_direct_check_radar_refusal_is_observed(self):
        """The audit's witness: runs the gate FILE and watches it exit 1. An empty day that
        examined nothing is a scanner that stopped searching, not a quiet week."""
        (self.fx.root / "data" / "signals" / "SIG-20260830-01.json").unlink()
        self.fx.scout_log(spot_tests=[], examined=0)
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "check_radar.py"),
             "--root", str(self.fx.root)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("stopped searching", result.stdout)

    def test_a_ledger_line_must_say_what_the_queue_held(self):
        """The postlude's republish clears the queue, so an undrained click is destroyed
        rather than delayed."""
        self.fx.ledger(f"{TODAY} 08:00Z | RADAR | run radar | by: routine | wrote: x | "
                       f"result: 1 card. taste rules applied: 3 | health: 1/1 | "
                       f"artifact: republished")
        r = gate(self.fx.root)
        self.assertEqual(1, r.returncode, r.stdout)
        self.assertIn("click queue held", r.stdout)


if __name__ == "__main__":
    unittest.main()
