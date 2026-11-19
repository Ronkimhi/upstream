#!/usr/bin/env python3
"""A replaced filing stays verifiable (tools/evidence_docs.py + fetch.archive_outgoing_doc).

The defect, 2026-11-19, three times in one day: `data/edgar/docs/<T>.json` holds one filing
per ticker, an edgar_doc request replaced it with the newest one, and every pipeline item,
screen nugget and dive quote that cited the old filing failed its gate, though each quote
was true of the filing it cites (ACM, CAT, GD/HUBB/NVT). Each time a Sieve agent re-cited
by hand before anything could build.

What each test guards:
  ACCEPT  a quote whose filing was replaced but archived, cited by accession, in all three
          gates (screen nuggets, pipeline items, dive quotes) and in the resolver itself;
          an uncited quote found only in an archived filing.
  REFUSE  a quote in no stored filing; a quote citing an accession that is stored nowhere
          (even when its words happen to be in the current document); a tampered archive
          (edited text, wrong accession for its filename, wrong ticker, bad text_sha256);
          a ticker with nothing stored at all.
  FETCHER the outgoing document is archived byte-identical before replacement, only when
          it differs, never overwritten, and a failed fetch archives nothing.

No network: the fetcher's `requests` is a stub.
Run: python3 -m unittest discover -s tools/tests -q
"""
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_analyst  # noqa: E402
import check_pipeline  # noqa: E402
import check_screen  # noqa: E402
import evidence_docs  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch = _load("fetch_evidence_docs_mod", ROOT / "tools" / "fetch" / "fetch.py")

OLD_ACC = "0000868857-26-000100"
NEW_ACC = "0000868857-26-000200"
OLD_TEXT = ("Fiscal 2026 results. Total backlog increased by 13% to a record high, driven by "
            "record total wins of $13.4 billion in the year. " + "Filler sentence. " * 40)
NEW_TEXT = ("First quarter fiscal 2027 results. Revenue grew modestly as project timing "
            "shifted. " + "Other filler. " * 40)
OLD_QUOTE = "Total backlog increased by 13% to a record high"
NEW_QUOTE = "Revenue grew modestly as project timing shifted"
FAKE_QUOTE = "management raised full year guidance to 40% growth"


def _doc(acc, text, ticker="ACM", date="2026-11-16", sha=True):
    d = {"ticker": ticker, "cik": 868857, "fetched_at": "2026-11-16T00:00:00+00:00",
         "form": "8-K", "filing_date": date, "accession": acc,
         "url": f"https://www.sec.gov/Archives/edgar/data/868857/{acc.replace('-', '')}/ex99.htm",
         "chars": len(text), "text": text}
    if sha:
        d["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return d


class _Tree:
    """A data/ tree where ACM's current doc is NEW_ACC and OLD_ACC sits in the archive."""

    def __init__(self, td, archive=True, current=True):
        self.data = Path(td) / "data"
        docs = self.data / "edgar" / "docs"
        docs.mkdir(parents=True)
        if current:
            (docs / "ACM.json").write_text(json.dumps(_doc(NEW_ACC, NEW_TEXT), indent=1))
        if archive:
            adir = docs / "_archive" / "ACM"
            adir.mkdir(parents=True)
            (adir / f"{OLD_ACC}.json").write_text(
                json.dumps(_doc(OLD_ACC, OLD_TEXT, date="2026-08-05", sha=False), indent=1))

    @property
    def archived(self):
        return self.data / "edgar" / "docs" / "_archive" / "ACM" / f"{OLD_ACC}.json"


class TestResolver(unittest.TestCase):
    def test_accepts_a_quote_whose_filing_was_replaced_but_archived(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            res = evidence_docs.find_quote(t.data, "ACM", OLD_QUOTE, accession=OLD_ACC)
            self.assertEqual(res["state"], "MATCH")
            self.assertEqual(res["where"], "archive")
            self.assertEqual(res["accession"], OLD_ACC)

    def test_accession_in_undashed_form_and_from_url_resolve_the_same_filing(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            r1 = evidence_docs.find_quote(t.data, "ACM", OLD_QUOTE,
                                          accession=OLD_ACC.replace("-", ""))
            url = f"https://www.sec.gov/Archives/edgar/data/868857/{OLD_ACC.replace('-', '')}/x.htm"
            r2 = evidence_docs.find_quote(t.data, "ACM", OLD_QUOTE, url=url)
            self.assertEqual((r1["state"], r2["state"]), ("MATCH", "MATCH"))

    def test_current_document_still_answers_for_its_own_accession(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            res = evidence_docs.find_quote(t.data, "ACM", NEW_QUOTE, accession=NEW_ACC)
            self.assertEqual((res["state"], res["where"]), ("MATCH", "current"))

    def test_uncited_quote_falls_back_to_the_archive(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            res = evidence_docs.find_quote(t.data, "ACM", OLD_QUOTE)
            self.assertEqual((res["state"], res["where"]), ("MATCH", "archive"))

    def test_refuses_a_quote_in_no_stored_filing(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            self.assertEqual(evidence_docs.find_quote(t.data, "ACM", FAKE_QUOTE)["state"],
                             "MISMATCH")
            self.assertEqual(evidence_docs.find_quote(
                t.data, "ACM", FAKE_QUOTE, accession=OLD_ACC)["state"], "MISMATCH")

    def test_refuses_a_cited_accession_that_is_not_stored(self):
        """Even when the words are in the current document: the citation names a filing
        that cannot be read, and it is never silently re-pointed at another one."""
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            res = evidence_docs.find_quote(t.data, "ACM", NEW_QUOTE,
                                           accession="0000868857-25-999999")
            self.assertEqual(res["state"], "NOT_STORED")
            self.assertIn("stored neither", evidence_docs.describe(res, "ACM"))

    def test_refuses_when_nothing_is_stored(self):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td, archive=False, current=False)
            self.assertEqual(evidence_docs.find_quote(t.data, "ACM", OLD_QUOTE)["state"],
                             "NO_DOC")
            self.assertEqual(evidence_docs.find_quote(
                t.data, "ACM", OLD_QUOTE, accession=OLD_ACC)["state"], "NO_DOC")

    def _tamper(self, mutate):
        with tempfile.TemporaryDirectory() as td:
            t = _Tree(td)
            d = json.loads(t.archived.read_text())
            mutate(d)
            t.archived.write_text(json.dumps(d))
            res = evidence_docs.find_quote(t.data, "ACM", OLD_QUOTE, accession=OLD_ACC)
            return res

    def test_refuses_a_tampered_archive_with_edited_text(self):
        res = self._tamper(lambda d: d.update(text=d["text"] + " Inserted claim."))
        self.assertEqual(res["state"], "NOT_STORED")
        self.assertTrue(res["tampered"])
        self.assertIn("integrity", evidence_docs.describe(res, "ACM"))

    def test_refuses_a_tampered_archive_with_a_bad_hash(self):
        res = self._tamper(lambda d: d.update(text_sha256="0" * 64))
        self.assertEqual(res["state"], "NOT_STORED")

    def test_refuses_an_archive_whose_accession_disagrees_with_its_filename(self):
        res = self._tamper(lambda d: d.update(accession="0000868857-26-000999"))
        self.assertEqual(res["state"], "NOT_STORED")

    def test_refuses_an_archive_filed_under_the_wrong_ticker(self):
        res = self._tamper(lambda d: d.update(ticker="CAT"))
        self.assertEqual(res["state"], "NOT_STORED")


class TestScreenGate(unittest.TestCase):
    def _run(self, data, nugget):
        screen = {"buckets": {"b": [{"ticker": "ACM", "earnings_nuggets": [nugget]}]}}
        check_screen.failures.clear()
        check_screen.lines.clear()
        check_screen.check_quotes(data, [(Path("probe.json"), screen)])
        return list(check_screen.failures), list(check_screen.lines)

    def test_accepts_nugget_citing_an_archived_filing(self):
        with tempfile.TemporaryDirectory() as td:
            failures, lines = self._run(_Tree(td).data,
                                        {"quote": OLD_QUOTE, "accession": OLD_ACC})
            self.assertEqual(failures, [])
            self.assertTrue(any("1/1" in ln and "1 against an archived" in ln for ln in lines),
                            lines)

    def test_refuses_nugget_citing_an_unstored_accession(self):
        with tempfile.TemporaryDirectory() as td:
            failures, _ = self._run(_Tree(td).data,
                                    {"quote": NEW_QUOTE, "accession": "0000868857-25-999999"})
            self.assertEqual(len(failures), 1, failures)
            self.assertIn("cites accession 0000868857-25-999999", failures[0])

    def test_refuses_nugget_quote_absent_from_its_filing(self):
        with tempfile.TemporaryDirectory() as td:
            failures, _ = self._run(_Tree(td).data, {"quote": FAKE_QUOTE, "accession": OLD_ACC})
            self.assertEqual(len(failures), 1, failures)
            self.assertIn("does NOT appear", failures[0])


class TestPipelineGate(unittest.TestCase):
    def _item(self, ref, excerpt):
        return {"source_kind": "edgar_doc", "source_ref": ref}, excerpt

    def test_accepts_excerpt_from_a_replaced_filing_via_the_ticker_ref(self):
        with tempfile.TemporaryDirectory() as td:
            data = _Tree(td).data
            item, ex = self._item("data/edgar/docs/ACM.json", OLD_QUOTE)
            self.assertEqual(check_pipeline._doc_failures(item, ex, data), [])

    def test_accepts_an_archive_ref_naming_one_filing(self):
        with tempfile.TemporaryDirectory() as td:
            data = _Tree(td).data
            item, ex = self._item(f"data/edgar/docs/_archive/ACM/{OLD_ACC}.json", OLD_QUOTE)
            self.assertEqual(check_pipeline._doc_failures(item, ex, data), [])

    def test_an_archive_ref_does_not_reach_the_current_document(self):
        with tempfile.TemporaryDirectory() as td:
            data = _Tree(td).data
            item, ex = self._item(f"data/edgar/docs/_archive/ACM/{OLD_ACC}.json", NEW_QUOTE)
            out = check_pipeline._doc_failures(item, ex, data)
            self.assertEqual(len(out), 1)
            self.assertIn("does not appear verbatim", out[0])

    def test_refuses_an_unstored_archive_ref_and_an_absent_excerpt(self):
        with tempfile.TemporaryDirectory() as td:
            data = _Tree(td).data
            item, ex = self._item("data/edgar/docs/_archive/ACM/0000868857-25-999999.json",
                                  NEW_QUOTE)
            self.assertIn("no document on disk", check_pipeline._doc_failures(item, ex, data)[0])
            item, ex = self._item("data/edgar/docs/ACM.json", FAKE_QUOTE)
            self.assertIn("does not appear verbatim",
                          check_pipeline._doc_failures(item, ex, data)[0])

    def test_refuses_a_ref_outside_the_edgar_store(self):
        with tempfile.TemporaryDirectory() as td:
            data = _Tree(td).data
            item, ex = self._item("data/edgar/docs/../web/x.json", OLD_QUOTE)
            self.assertIn("must be data/edgar/docs/",
                          check_pipeline._doc_failures(item, ex, data)[0])


class TestDiveGate(unittest.TestCase):
    def _run(self, data, dive):
        check_analyst.failures.clear()
        check_analyst.lines.clear()
        check_analyst.check_dive_quotes(data, "probe.json", "ACM", dive)
        return list(check_analyst.failures), list(check_analyst.lines)

    def test_accepts_a_dive_quote_citing_an_archived_filing(self):
        with tempfile.TemporaryDirectory() as td:
            failures, lines = self._run(_Tree(td).data, {"filing_evidence": [
                {"quote": OLD_QUOTE, "accession": OLD_ACC},
                {"quote": NEW_QUOTE, "accession": NEW_ACC}]})
            self.assertEqual(failures, [])
            self.assertTrue(any("2/2" in ln and "1 archived" in ln for ln in lines), lines)

    def test_refuses_a_dive_quote_citing_an_unstored_accession(self):
        with tempfile.TemporaryDirectory() as td:
            failures, _ = self._run(_Tree(td).data, {"filing_evidence": [
                {"quote": NEW_QUOTE, "accession": "0000868857-25-999999"}]})
            self.assertEqual(len(failures), 1, failures)
            self.assertIn("stored neither", failures[0])

    def test_refuses_a_fabricated_dive_quote(self):
        with tempfile.TemporaryDirectory() as td:
            failures, _ = self._run(_Tree(td).data, {"filing_evidence": [
                {"quote": FAKE_QUOTE, "accession": OLD_ACC}]})
            self.assertEqual(len(failures), 1, failures)
            self.assertIn("does NOT appear", failures[0])


class _Resp:
    def __init__(self, code, payload, is_json):
        self.status_code, self._p, self._j = code, payload, is_json

    def json(self):
        return self._p

    @property
    def text(self):
        return "" if self._j else self._p


class _Requests:
    def __init__(self, acc, body):
        self.acc, self.body = acc, body

    def get(self, url, **k):
        if "/submissions/" in url:
            return _Resp(200, {"filings": {"recent": {
                "form": ["10-Q"], "filingDate": ["2026-11-10"],
                "accessionNumber": [self.acc], "primaryDocument": ["q.htm"],
                "items": [""]}}}, True)
        if url.endswith("q.htm"):
            return _Resp(200 if self.body else 404, self.body or "", False)
        raise AssertionError(f"unexpected URL {url}")


class TestFetcherArchivesOutgoingDoc(unittest.TestCase):
    def setUp(self):
        self._saved = (fetch.cik_for, fetch.requests, fetch.DATA)
        self._td = tempfile.TemporaryDirectory()
        fetch.DATA = Path(self._td.name) / "data"
        fetch.cik_for = lambda t: 868857
        fetch._last_edgar[0] = 0.0
        self.docs = fetch.DATA / "edgar" / "docs"
        self.docs.mkdir(parents=True)
        self.cur = self.docs / "ACM.json"
        self.old_bytes = json.dumps(_doc(OLD_ACC, self._stored(OLD_TEXT), sha=False),
                                    indent=1).encode()
        self.cur.write_bytes(self.old_bytes)
        self.adir = self.docs / "_archive" / "ACM"

    def tearDown(self):
        fetch.cik_for, fetch.requests, fetch.DATA = self._saved
        self._td.cleanup()

    def _stored(self, text):
        """The text exactly as the fetcher stores it (HTML-stripped and trimmed)."""
        return fetch._strip_html(f"<p>{text}</p>")[:fetch.MAX_DOC_CHARS]

    def _fetch(self, acc, text):
        fetch.requests = _Requests(acc, f"<p>{text}</p>" if text else None)
        return fetch.do_edgar_doc("ACM", lookback_days=200)

    def test_new_accession_archives_the_old_file_byte_identical(self):
        wrote = self._fetch(NEW_ACC, NEW_TEXT)
        arch = self.adir / f"{OLD_ACC}.json"
        self.assertEqual(arch.read_bytes(), self.old_bytes)
        self.assertIn(f"data/edgar/docs/_archive/ACM/{OLD_ACC}.json", wrote)
        new = json.loads(self.cur.read_text())
        self.assertEqual(new["accession"], NEW_ACC)
        self.assertEqual(new["text_sha256"],
                         hashlib.sha256(new["text"].encode("utf-8")).hexdigest())

    def test_end_to_end_the_old_quote_still_verifies_after_replacement(self):
        self._fetch(NEW_ACC, NEW_TEXT)
        res = evidence_docs.find_quote(fetch.DATA, "ACM", OLD_QUOTE, accession=OLD_ACC)
        self.assertEqual((res["state"], res["where"]), ("MATCH", "archive"))
        res = evidence_docs.find_quote(fetch.DATA, "ACM", NEW_QUOTE, accession=NEW_ACC)
        self.assertEqual((res["state"], res["where"]), ("MATCH", "current"))

    def test_refetching_the_same_filing_with_the_same_text_archives_nothing(self):
        wrote = self._fetch(OLD_ACC, OLD_TEXT)
        self.assertFalse(self.adir.exists())
        self.assertEqual(wrote, ["data/edgar/docs/ACM.json"])

    def test_same_accession_with_changed_text_is_archived(self):
        self._fetch(OLD_ACC, OLD_TEXT + " Longer exhibit text.")
        self.assertEqual((self.adir / f"{OLD_ACC}.json").read_bytes(), self.old_bytes)

    def test_an_existing_archive_is_never_overwritten(self):
        self.adir.mkdir(parents=True)
        other = b'{"pre": "existing"}'
        (self.adir / f"{OLD_ACC}.json").write_bytes(other)
        self._fetch(NEW_ACC, NEW_TEXT)
        self.assertEqual((self.adir / f"{OLD_ACC}.json").read_bytes(), other)
        variants = [p for p in self.adir.iterdir() if p.name.startswith(OLD_ACC + "~")]
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].read_bytes(), self.old_bytes)

    def test_a_failed_fetch_archives_and_replaces_nothing(self):
        with self.assertRaises(RuntimeError):
            self._fetch(NEW_ACC, None)
        self.assertFalse(self.adir.exists())
        self.assertEqual(self.cur.read_bytes(), self.old_bytes)

    def test_first_fetch_for_a_ticker_archives_nothing(self):
        self.cur.unlink()
        wrote = self._fetch(NEW_ACC, NEW_TEXT)
        self.assertEqual(wrote, ["data/edgar/docs/ACM.json"])
        self.assertFalse(self.adir.exists())


if __name__ == "__main__":
    unittest.main()
