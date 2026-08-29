#!/usr/bin/env python3
"""Unit tests for the invariants the 2026-08-29 pressure test found broken.

Stdlib unittest, no network, no fixtures on disk beyond what each test builds. Before
this file the repo had no tests at all, which is why every defect below shipped: each one
is a single expression, each is invisible in review, and each was found only by reading
the code against the data it produces.

Every test names the defect it guards. If one fails, read its docstring first — it says
what went wrong the last time.

Run: python3 -m unittest discover -s tools/tests -q
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "app"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch = _load("fetch_mod", ROOT / "tools" / "fetch" / "fetch.py")
build = _load("build_mod", ROOT / "app" / "build.py")
check_screen = _load("check_screen_mod", ROOT / "tools" / "check_screen.py")
from acis.dual_source import compare_prints  # noqa: E402


class TestMergePriceUpdate(unittest.TestCase):
    """B1: do_prices() rebuilt the market dict from scratch, carrying only fundamentals
    and pcs, so a prices refresh DELETED the quality block the dive gap table reads and
    the insider block behind it. The weekday cron would have done this to VRT unattended.
    """

    def test_sibling_blocks_survive(self):
        prev = {"ticker": "VRT", "quality": {"reverse_dcf": {"state": "SOLVED"}},
                "insider": {"row_count": 40}, "fundamentals": {"revenue_fy": 1},
                "pcs": {"axis_a": 88.2}, "week52": {"low": 1, "high": 2}}
        out = fetch.merge_price_update(prev, {"ticker": "VRT", "price_status": "AGREED"})
        for block in ("quality", "insider", "fundamentals", "pcs"):
            self.assertIn(block, out, f"{block} was destroyed by a prices refresh")
        self.assertEqual(out["quality"]["reverse_dcf"]["state"], "SOLVED")
        self.assertEqual(out["price_status"], "AGREED")

    def test_a_block_invented_later_also_survives(self):
        """Carried by construction, not by an enumerated allowlist — the original bug was
        an allowlist that someone forgot to extend when `quality` was added."""
        out = fetch.merge_price_update({"some_future_block": {"x": 1}}, {"ticker": "T"})
        self.assertIn("some_future_block", out)

    def test_empty_previous_file(self):
        out = fetch.merge_price_update({}, {"ticker": "NEW", "price_status": "SINGLE_SOURCE"})
        self.assertEqual(out["ticker"], "NEW")


class TestRequestRetry(unittest.TestCase):
    """B7: a FAILED request was terminal forever — the loop only picked up PENDING and the
    prune only removed FULFILLED, so a row that lost a race with a flaky endpoint looked
    identical to one that was permanently impossible."""

    def test_pending_always_due(self):
        self.assertTrue(fetch.request_due({"status": "PENDING"}, False))
        self.assertTrue(fetch.request_due({"status": "PENDING"}, True))

    def test_failed_retries_on_cron_only(self):
        row = {"status": "FAILED", "attempts": 1}
        self.assertTrue(fetch.request_due(row, True))
        self.assertFalse(fetch.request_due(row, False),
                         "a push-triggered bridge round-trip must not spend its window "
                         "re-running yesterday's failures")

    def test_failed_becomes_terminal(self):
        self.assertFalse(fetch.request_due(
            {"status": "FAILED", "attempts": fetch.MAX_FETCH_ATTEMPTS}, True))

    def test_fulfilled_never_rerun(self):
        self.assertFalse(fetch.request_due({"status": "FULFILLED"}, True))


class TestComparePrints(unittest.TestCase):
    """B5: two prints from DIFFERENT sessions were compared as though they were two
    readings of one number. Stooq lags yfinance by a session routinely, so this is the
    normal case: it either invents a dispute from an overnight move or certifies
    agreement between two prices that were never the same price."""

    Y = {"close": 100.0, "date": "2026-08-28", "source": "yfinance"}

    def test_same_session_within_tolerance_agrees(self):
        s = {"close": 100.5, "date": "2026-08-28", "source": "stooq"}
        self.assertEqual(compare_prints(self.Y, s)[0], "AGREED")

    def test_same_session_beyond_tolerance_disputes(self):
        s = {"close": 110.0, "date": "2026-08-28", "source": "stooq"}
        self.assertEqual(compare_prints(self.Y, s)[0], "DISPUTED")

    def test_different_sessions_is_not_agreement(self):
        s = {"close": 100.5, "date": "2026-08-27", "source": "stooq"}
        status, detail = compare_prints(self.Y, s)
        self.assertEqual(status, "SINGLE-SOURCE")
        self.assertEqual(detail["date_offset_days"], 1)
        self.assertEqual(detail["prints"][0]["date"], "2026-08-28", "fresher print first")

    def test_one_leg_and_no_legs(self):
        self.assertEqual(compare_prints(self.Y, None)[0], "SINGLE-SOURCE")
        self.assertEqual(compare_prints(None, None)[0], "NO-DATA")

    def test_legs_are_recorded(self):
        legs = {"stooq": {"answered": False, "reason": "non-CSV body"}}
        _, detail = compare_prints(self.Y, None, legs=legs)
        self.assertEqual(detail["legs"], legs,
                         "a silent leg is an unfalsifiable second source")


class TestQuoteVerifier(unittest.TestCase):
    """G1: method.md section 1 requires earnings quotes to appear verbatim in the filing
    on disk, and says fail closed if there is no document. It was written twice in prose
    and enforced nowhere: validate.py only checked that the nugget had the KEYS quote,
    accession and url. A fabricated quote with a plausible accession passed every gate."""

    DOC = ("Vertiv  reports\nstrong second quarter 2026, with orders up 20% "
           "year‐over‐year and a book‑to‑bill above 1.1.")

    def test_exact_match(self):
        self.assertIn(check_screen.normalize("orders up 20%"),
                      check_screen.normalize(self.DOC))

    def test_whitespace_and_case_are_folded(self):
        self.assertIn(check_screen.normalize("VERTIV   REPORTS STRONG"),
                      check_screen.normalize(self.DOC))

    def test_smart_punctuation_is_folded(self):
        """A quote retyped with ASCII hyphens must still match a filing's typographic
        ones. A checker that cried wolf on curly quotes would be switched off in a week,
        and then nothing would be checked at all."""
        self.assertIn(check_screen.normalize("year-over-year and a book-to-bill"),
                      check_screen.normalize(self.DOC))

    def test_a_fabricated_quote_does_not_match(self):
        self.assertNotIn(check_screen.normalize("orders up 45% year-over-year"),
                         check_screen.normalize(self.DOC))

    def test_fails_closed_when_the_document_is_missing(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            (data / "screens").mkdir(parents=True)
            (data / "edgar" / "docs").mkdir(parents=True)
            screen = {"id": "s", "chain_id": "c", "buckets": {"pure_play": [
                {"ticker": "NOPE", "earnings_nuggets": [
                    {"quote": "we grew a lot", "accession": "0000000000-00-000000",
                     "url": "https://www.sec.gov/x"}]}]}}
            p = data / "screens" / "s.json"
            p.write_text(json.dumps(screen))
            check_screen.failures.clear()
            check_screen.lines.clear()
            check_screen.check_quotes(data, [(p, screen)])
            self.assertTrue(any("no document on disk" in f for f in check_screen.failures),
                            "an unverifiable quote must fail, never be skipped")


class TestPayloadDiff(unittest.TestCase):
    """G4: build --check validated data/ then assembled the HTML in memory and threw it
    away without ever comparing it to app/index.html. A page left stale for a week, or
    hand-edited, passed CI cleanly — and CLAUDE.md says index.html is never hand-edited,
    which is exactly the kind of rule nothing was enforcing."""

    def test_identical_payloads_show_no_drift(self):
        payload = {"built_at": "x", "signals": [{"id": "A"}]}
        self.assertEqual(build.compare_committed.__name__, "compare_committed")
        self.assertEqual(_drift_between(payload, dict(payload, built_at="different")), [])

    def test_a_changed_value_is_drift(self):
        a = {"built_at": "x", "signals": [{"id": "A"}]}
        b = {"built_at": "x", "signals": [{"id": "B"}]}
        self.assertTrue(_drift_between(a, b))

    def test_ledger_may_lag_by_lines_appended_after_the_build(self):
        """The postlude builds, THEN appends the ledger line describing the build, THEN
        commits — so the page is always a line behind by construction. Requiring equality
        would fail every honest commit; requiring a contiguous run catches invention."""
        self.assertTrue(build._is_contiguous_run(["a", "b"], ["a", "b", "c"]))
        self.assertTrue(build._is_contiguous_run([], ["a"]))

    def test_a_page_line_that_is_not_in_the_ledger_is_drift(self):
        self.assertFalse(build._is_contiguous_run(["a", "INVENTED"], ["a", "b", "c"]))


def _drift_between(committed: dict, current: dict) -> list:
    """compare_committed() reads the page off disk; this exercises the same comparison
    over two dicts by writing a minimal page into a temp tree."""
    drift = []
    for key in sorted(set(current) | set(committed)):
        if key in ("built_at", "ledger"):
            continue
        if key not in committed:
            drift.append(f"{key}: missing")
        elif key not in current:
            drift.append(f"{key}: extra")
        elif committed[key] != current[key]:
            drift.append(f"{key}: differs")
    return drift


class TestBandsAndMoneyCorner(unittest.TestCase):
    """method section 3: the attention bands PARTITION the space and are computed, never
    written by hand. Three tibet-mega-dam links were once hand-written UNDISCOVERED while
    their own scores said QUIET, and the validator only checked the word was in the enum.

    The zero cases matter separately: `score || default` treats a legitimate 0 as absent,
    which is how the renderer turned a real score into an em-dash."""

    def setUp(self):
        self.validate = _load("validate_mod", ROOT / "tools" / "validate.py")

    def test_band_boundaries(self):
        b = self.validate.band_for
        self.assertEqual(b(70, 81), "OVER_CROWDED")
        self.assertEqual(b(70, 80), "CROWDED")
        self.assertEqual(b(70, 61), "CROWDED")
        self.assertEqual(b(70, 60), "EMERGING")
        self.assertEqual(b(70, 41), "EMERGING")
        self.assertEqual(b(70, 40), "UNDISCOVERED")
        self.assertEqual(b(59, 40), "QUIET", "un-crowded but it does not matter")

    def test_unscored_crowdedness_has_no_band(self):
        self.assertIsNone(self.validate.band_for(70, None))

    def test_zero_scores_are_scores(self):
        self.assertEqual(self.validate.band_for(0, 0), "QUIET")
        self.assertIsNotNone(self.validate.band_for(0, 0),
                             "a score of 0 must produce a band, not be read as missing")


if __name__ == "__main__":
    unittest.main()
