#!/usr/bin/env python3
"""do_edgar_doc widened for 20-F and 6-K filers (BUILD item 3, 2026-09-13).

Before this, do_edgar_doc tried only an 8-K with item 2.02, then a 10-Q, then gave up.
That is a US-filer-shaped search: a foreign private issuer files neither. ABBNY, ASX, BP,
TSM, GSK, SNY, STN and KLIN all FAILED here with "no earnings document in <window>" even
though EDGAR carried real filings for every one of them, because 6-K (their interim
disclosure vehicle) and 20-F (their annual report) were never candidates.

What each test guards:
  - the two existing tiers (8-K/2.02, then 10-Q) still win over the two new ones --
    this is a widening, never a reordering, of the search.
  - 6-K is windowed by lookback_days exactly like 10-Q (it plays the same role for a
    foreign filer that a 10-Q plays for a domestic one).
  - 20-F is NOT windowed: it is an annual filing, and restricting it to the same
    ~200-day window as the other three tiers would routinely find none and defeat the
    point of a last-resort tier.
  - the chosen form is recorded on the written document (`form`), so a reader can tell
    which tier answered.
  - the EX-99 exhibit upgrade (the primary document is often just a cover) now also
    fires for a 6-K, the same as it already does for an 8-K.
  - a filer with nothing in any of the four tiers still fails closed with a clear reason.

No network: `fetch.requests` is replaced with a stub that answers from an in-memory
submissions fixture and canned document bodies; reaching anywhere else is a test failure.

Run: python3 -m unittest discover -s tools/tests -q
"""
import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch = _load("fetch_edgar_doc_mod", ROOT / "tools" / "fetch" / "fetch.py")

CIK = 1234567
LONG_TEXT = "<p>" + ("Earnings call commentary. " * 400) + "</p>"  # strips to > 8000 chars
SHORT_COVER = "<p>" + ("Cover page notice. " * 30) + "</p>"        # strips to < 8000 chars
EXHIBIT_TEXT = "<p>" + ("Full press release detail. " * 200) + "</p>"


def _submissions(rows):
    """rows: [(form, date, accession, primaryDocument, items), ...], newest first
    (do_edgar_doc scans in list order and takes the first match per tier)."""
    return {"filings": {"recent": {
        "form": [r[0] for r in rows],
        "filingDate": [r[1] for r in rows],
        "accessionNumber": [r[2] for r in rows],
        "primaryDocument": [r[3] for r in rows],
        "items": [r[4] for r in rows],
    }}}


class _Resp:
    def __init__(self, status_code, payload, is_json):
        self.status_code = status_code
        self._payload = payload
        self._is_json = is_json

    def json(self):
        return self._payload

    @property
    def text(self):
        return self._payload if not self._is_json else ""


class _EdgarDocRequests:
    """Routes by URL substring: submissions -> the fixture; a document URL -> whichever
    canned body was registered for it; index.json/directory listing -> the exhibit
    candidate list; anything unregistered -> a loud test failure via 404 + assertion."""

    def __init__(self, submissions, doc_bodies, index_json=None, dir_listing=None):
        self.submissions = submissions
        self.doc_bodies = doc_bodies  # {substring: html_text}
        self.index_json = index_json
        self.dir_listing = dir_listing
        self.urls = []

    def get(self, url, **k):
        self.urls.append(url)
        if "/submissions/" in url:
            return _Resp(200, self.submissions, True)
        if url.endswith("/index.json"):
            if self.index_json is None:
                return _Resp(404, {}, True)
            return _Resp(200, self.index_json, True)
        if url.endswith("/") and self.dir_listing is not None:
            return _Resp(200, self.dir_listing, False)
        for substr, body in self.doc_bodies.items():
            if substr in url:
                return _Resp(200, body, False)
        raise AssertionError(f"unregistered URL in test stub: {url}")


class EdgarDocTestCase(unittest.TestCase):
    def setUp(self):
        self._cik, self._req, self._data = fetch.cik_for, fetch.requests, fetch.DATA
        self._td = tempfile.TemporaryDirectory()
        fetch.DATA = Path(self._td.name) / "data"
        fetch.cik_for = lambda t: CIK
        fetch._last_edgar[0] = 0.0  # keep edgar_wait's 0.5s spacing from compounding

    def tearDown(self):
        fetch.cik_for, fetch.requests, fetch.DATA = self._cik, self._req, self._data
        self._td.cleanup()


class TestExistingTiersUnchanged(EdgarDocTestCase):
    """The widening must never reorder the two tiers that already worked."""

    def test_8k_item_202_wins_over_everything_else(self):
        rows = [
            ("8-K", "2026-08-01", "0001-26-000001", "cover.htm", "2.02"),
            ("10-Q", "2026-07-15", "0001-26-000002", "q.htm", ""),
            ("6-K", "2026-07-20", "0001-26-000003", "sixk.htm", ""),
        ]
        fetch.requests = _EdgarDocRequests(
            _submissions(rows), {"cover.htm": LONG_TEXT})
        fetch.do_edgar_doc("FOO", lookback_days=200)
        doc = fetch.jload(fetch.DATA / "edgar" / "docs" / "FOO.json", None)
        self.assertEqual(doc["form"], "8-K")
        self.assertEqual(doc["accession"], "0001-26-000001")

    def test_10q_wins_when_no_matching_8k_exists(self):
        rows = [
            ("8-K", "2026-08-01", "0001-26-000001", "cover.htm", "5.02"),  # wrong item
            ("10-Q", "2026-07-15", "0001-26-000002", "q.htm", ""),
            ("6-K", "2026-07-20", "0001-26-000003", "sixk.htm", ""),
        ]
        fetch.requests = _EdgarDocRequests(
            _submissions(rows), {"q.htm": LONG_TEXT})
        fetch.do_edgar_doc("FOO", lookback_days=200)
        doc = fetch.jload(fetch.DATA / "edgar" / "docs" / "FOO.json", None)
        self.assertEqual(doc["form"], "10-Q")


class Test6KAnd20FTiers(EdgarDocTestCase):
    def test_6k_is_used_when_no_8k_202_or_10q_exists(self):
        rows = [
            ("20-F", "2025-01-10", "0001-25-000009", "annual.htm", ""),  # old, last resort
            ("6-K", "2026-08-05", "0001-26-000004", "sixk.htm", ""),
        ]
        fetch.requests = _EdgarDocRequests(
            _submissions(rows), {"sixk.htm": LONG_TEXT})
        fetch.do_edgar_doc("BAR", lookback_days=200)
        doc = fetch.jload(fetch.DATA / "edgar" / "docs" / "BAR.json", None)
        self.assertEqual(doc["form"], "6-K")
        self.assertEqual(doc["accession"], "0001-26-000004")

    def test_6k_outside_the_window_does_not_count(self):
        """6-K is windowed exactly like 10-Q: stale enough and it is not a candidate."""
        rows = [
            ("6-K", "2024-01-01", "0001-24-000001", "old6k.htm", ""),   # way outside 200d
            ("20-F", "2025-06-01", "0001-25-000002", "annual.htm", ""),
        ]
        fetch.requests = _EdgarDocRequests(
            _submissions(rows), {"annual.htm": LONG_TEXT})
        fetch.do_edgar_doc("BAR", lookback_days=200)
        doc = fetch.jload(fetch.DATA / "edgar" / "docs" / "BAR.json", None)
        self.assertEqual(doc["form"], "20-F")

    def test_20f_is_the_last_resort_and_is_not_windowed_by_lookback_days(self):
        """20-F is annual; a 200-day cutoff would routinely find none. The single latest
        one on file is taken regardless of how old it is."""
        rows = [
            ("20-F", "2023-03-15", "0001-23-000003", "annual-old.htm", ""),  # far outside
        ]
        fetch.requests = _EdgarDocRequests(
            _submissions(rows), {"annual-old.htm": LONG_TEXT})
        fetch.do_edgar_doc("BAZ", lookback_days=200)
        doc = fetch.jload(fetch.DATA / "edgar" / "docs" / "BAZ.json", None)
        self.assertEqual(doc["form"], "20-F")
        self.assertEqual(doc["filing_date"], "2023-03-15")

    def test_the_latest_20f_wins_when_more_than_one_is_on_file(self):
        # SEC's submissions.json always lists `recent` filings newest-first; do_edgar_doc
        # takes the FIRST 20-F it meets in list order, so the newest-first fixture order
        # below is what makes "latest" true, exactly like the pre-existing 10-Q tier.
        rows = [
            ("20-F", "2025-03-20", "0001-25-000005", "annual-new.htm", ""),
            ("20-F", "2023-03-15", "0001-23-000003", "annual-old.htm", ""),
        ]
        fetch.requests = _EdgarDocRequests(
            _submissions(rows), {"annual-new.htm": LONG_TEXT, "annual-old.htm": LONG_TEXT})
        fetch.do_edgar_doc("BAZ", lookback_days=200)
        doc = fetch.jload(fetch.DATA / "edgar" / "docs" / "BAZ.json", None)
        self.assertEqual(doc["accession"], "0001-25-000005")

    def test_a_filer_with_nothing_in_any_tier_fails_closed_with_a_clear_reason(self):
        rows = [("8-K", "2026-08-01", "0001-26-000001", "cover.htm", "5.02")]
        fetch.requests = _EdgarDocRequests(_submissions(rows), {})
        with self.assertRaises(RuntimeError) as ctx:
            fetch.do_edgar_doc("NONE", lookback_days=200)
        msg = str(ctx.exception)
        self.assertIn("6-K", msg)
        self.assertIn("20-F", msg)


class TestEx99UpgradeAppliesTo6K(EdgarDocTestCase):
    """The primary document upgrade (cover -> EX-99 exhibit) already existed for 8-K;
    widened to 6-K on the same reasoning: a foreign filer's 6-K cover is the same shape
    of stub, and the substance is an exhibit."""

    def test_a_thin_6k_cover_is_upgraded_to_the_longer_exhibit(self):
        rows = [("6-K", "2026-08-05", "0001-26-000004", "sixk-cover.htm", "")]
        fetch.requests = _EdgarDocRequests(
            _submissions(rows),
            doc_bodies={"sixk-cover.htm": SHORT_COVER, "ex991.htm": EXHIBIT_TEXT},
            index_json={"directory": {"item": [{"name": "ex991.htm"}]}})
        fetch.do_edgar_doc("EXH", lookback_days=200)
        doc = fetch.jload(fetch.DATA / "edgar" / "docs" / "EXH.json", None)
        self.assertEqual(doc["form"], "6-K")
        self.assertIn("Full press release detail", doc["text"])
        self.assertNotIn("Cover page notice", doc["text"])

    def test_a_thin_6k_cover_with_no_better_exhibit_keeps_the_cover(self):
        rows = [("6-K", "2026-08-05", "0001-26-000004", "sixk-cover.htm", "")]
        fetch.requests = _EdgarDocRequests(
            _submissions(rows),
            doc_bodies={"sixk-cover.htm": SHORT_COVER},
            index_json={"directory": {"item": []}})
        fetch.do_edgar_doc("EXH2", lookback_days=200)
        doc = fetch.jload(fetch.DATA / "edgar" / "docs" / "EXH2.json", None)
        self.assertIn("Cover page notice", doc["text"])


class TestFormFieldRecordsWhichTierAnswered(EdgarDocTestCase):
    """Restated as its own test because it is exactly what the task asks for: 'record
    which form was used' -- proven for every one of the four tiers in one place."""

    def test_every_tier_stamps_its_own_form_code(self):
        cases = {
            "8-K": [("8-K", "2026-08-01", "0001-26-000001", "a.htm", "2.02")],
            "10-Q": [("10-Q", "2026-08-01", "0001-26-000002", "b.htm", "")],
            "6-K": [("6-K", "2026-08-01", "0001-26-000003", "c.htm", "")],
            "20-F": [("20-F", "2020-01-01", "0001-20-000004", "d.htm", "")],
        }
        for form, rows in cases.items():
            acc = rows[0][2].replace("-", "")
            fetch.requests = _EdgarDocRequests(_submissions(rows), {acc: LONG_TEXT})
            fetch.do_edgar_doc(f"T-{form}", lookback_days=200)
            doc = fetch.jload(fetch.DATA / "edgar" / "docs" / f"T-{form}.json", None)
            self.assertEqual(doc["form"], form, f"tier {form} mis-stamped its form")


if __name__ == "__main__":
    unittest.main()
