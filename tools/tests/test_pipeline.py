#!/usr/bin/env python3
"""Exact tests for the company-pipeline gate: excerpt verbatim, the numeric cross-check,
xbrl value equality, the NONE_FOUND/COMPLETE denominators, the verdict/entry-price leak,
and the NO STORE line over an empty corpus."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import evidence_store as es  # noqa: E402
from check_pipeline import validate_pipeline  # noqa: E402


def build_root(td) -> tuple[Path, str]:
    """A minimal repo tree: one profiled issuer, one stored EDGAR doc, one stored web page,
    and a market file with a fundamentals field -- enough to satisfy an item of any
    source_kind before the test mutates one field to break it."""
    root = Path(td)
    for d in ("data/companies", "data/edgar/docs", "data/market", "data/web", "data/pipelines"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "data" / "companies" / "ACM.json").write_text(json.dumps({"issuer_id": "ACM"}))
    (root / "data" / "edgar" / "docs" / "ACM.json").write_text(json.dumps({
        "ticker": "ACM", "cik": 868857, "form": "8-K", "accession": "0001104659-26-093421",
        "text": "Total backlog reached $27.8 billion, a 13 percent increase driven by a "
                "1.6 book-to-burn ratio.",
    }))
    (root / "data" / "market" / "ACM.json").write_text(json.dumps({
        "ticker": "ACM", "fetched_at": "2026-09-04T00:00:00Z", "price_status": "AGREED",
        "fundamentals": {"revenue_fy": 3586000000},
    }))
    url = "https://investors.example.com/acm-ir"
    (root / "data" / "web" / f"{es.web_doc_id(url)}.json").write_text(json.dumps({
        "url": url, "http_status": 200,
        "text": "The company was awarded a $500 million contract to build a new terminal.",
    }))
    return root, url


def base_pipeline(items=None, status="COMPLETE", searched=None) -> dict:
    return {
        "issuer_id": "ACM", "ticker": "ACM", "as_of": "2026-09-13",
        "generated_by": "sieve-profiler", "status": status, "items": items or [],
        "searched": searched or [], "notes": [],
        "changelog": [{"ts": "2026-09-13T00:00:00Z", "by": "sieve-profiler",
                       "change": "new pipeline"}],
    }


def edgar_item(**overrides) -> dict:
    item = {
        "type": "BACKLOG", "name": "Total backlog", "stage": None, "value": 27800000000,
        "currency": "USD", "date": "2026-08-10",
        "claim": "Total backlog reached $27.8 billion, up 13 percent.",
        "source_kind": "edgar_doc", "source_ref": "data/edgar/docs/ACM.json",
        "source_url": "https://www.sec.gov/Archives/edgar/data/868857/x.htm",
        "source_excerpt": "Total backlog reached $27.8 billion, a 13 percent increase "
                          "driven by a 1.6 book-to-burn ratio.",
    }
    item.update(overrides)
    return item


def web_item(url, **overrides) -> dict:
    item = {
        "type": "CONTRACT", "name": "New terminal contract", "stage": "awarded",
        "value": 500000000, "currency": "USD", "date": "2026-08-01",
        "claim": "Awarded a $500 million contract to build a new terminal.",
        "source_kind": "web_doc", "source_ref": f"data/web/{es.web_doc_id(url)}.json",
        "source_url": url,
        "source_excerpt": "The company was awarded a $500 million contract to build a "
                          "new terminal.",
    }
    item.update(overrides)
    return item


def xbrl_item(**overrides) -> dict:
    item = {
        "type": "BACKLOG", "name": "Revenue (FY)", "stage": None, "value": 3586000000,
        "currency": "USD", "date": "2026-08-10",
        "claim": "Revenue per the fetched fundamentals block.",
        "source_kind": "xbrl", "source_ref": "data/market/ACM.json#fundamentals.revenue_fy",
        "source_url": "https://data.sec.gov/x", "source_excerpt": "revenue_fy",
    }
    item.update(overrides)
    return item


class TestGate(unittest.TestCase):
    """The gate has to FAIL on each defect. A gate that cannot fail is not a gate."""

    def run_gate(self, items=None, status="COMPLETE", searched=None, pipeline=None,
                 unlink=None):
        with tempfile.TemporaryDirectory() as td:
            root, _url = build_root(td)
            for rel in unlink or []:
                (root / rel).unlink()
            obj = pipeline if pipeline is not None else base_pipeline(items, status, searched)
            (root / "data" / "pipelines" / "ACM.json").write_text(json.dumps(obj))
            return subprocess.run(
                [sys.executable, str(ROOT / "tools" / "check_pipeline.py"), "--root", str(root)],
                capture_output=True, text=True)

    def test_valid_edgar_doc_fixture_passes(self):
        r = self.run_gate(items=[edgar_item()])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("check_pipeline: OK", r.stdout)

    def test_valid_web_doc_fixture_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root, url = build_root(td)
            obj = base_pipeline([web_item(url)])
            (root / "data" / "pipelines" / "ACM.json").write_text(json.dumps(obj))
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_pipeline.py"), "--root", str(root)],
                                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_valid_xbrl_fixture_passes(self):
        r = self.run_gate(items=[xbrl_item()])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_fabricated_excerpt_is_refused(self):
        item = edgar_item(source_excerpt="This sentence appears nowhere in the filing.")
        r = self.run_gate(items=[item])
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not appear verbatim", r.stdout)

    def test_fabricated_web_excerpt_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root, url = build_root(td)
            item = web_item(url, source_excerpt="Nothing on this page says that.")
            obj = base_pipeline([item])
            (root / "data" / "pipelines" / "ACM.json").write_text(json.dumps(obj))
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_pipeline.py"), "--root", str(root)],
                                capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("MISMATCH", r.stdout)

    def test_number_missing_from_excerpt_is_refused(self):
        """The excerpt IS verbatim in the stored filing; the claim's own figure is not in
        that excerpt. This is the numeric cross-check, distinct from the verbatim check."""
        item = edgar_item(value=99999000000, claim="Total backlog was $99,999 million.")
        r = self.run_gate(items=[item])
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not contain", r.stdout)

    def test_xbrl_value_mismatch_is_refused(self):
        item = xbrl_item(value=1)
        r = self.run_gate(items=[item])
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not equal", r.stdout)

    def test_xbrl_item_is_exempt_from_the_excerpt_bar(self):
        """An xbrl item has no prose document to quote, so its source_excerpt is never
        checked against any stored document or numeric-cross-checked against its claim --
        only its value-equals-market-field rule (check 6) applies."""
        item = xbrl_item(source_excerpt="not a real excerpt of anything, on purpose")
        r = self.run_gate(items=[item])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_xbrl_missing_source_excerpt_still_fails_shape(self):
        """Exempt from the excerpt BAR, never from having the field at all."""
        item = xbrl_item(source_excerpt="")
        r = self.run_gate(items=[item])
        self.assertEqual(r.returncode, 1)
        self.assertIn("carries no source_excerpt", r.stdout)

    def test_none_found_without_two_searches_is_refused(self):
        r = self.run_gate(items=[], status="NONE_FOUND",
                          searched=[{"query_or_url": "q", "result": "nothing found"}])
        self.assertEqual(r.returncode, 1)
        self.assertIn("NONE_FOUND needs >= 2", r.stdout)

    def test_none_found_with_two_searches_passes(self):
        r = self.run_gate(items=[], status="NONE_FOUND", searched=[
            {"query_or_url": "ACM order backlog 2026", "result": "no new disclosure found"},
            {"query_or_url": "https://investors.example.com/acm-ir",
             "result": "no backlog table on this page"},
        ])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_none_found_with_items_is_refused(self):
        r = self.run_gate(items=[edgar_item()], status="NONE_FOUND", searched=[
            {"query_or_url": "q1", "result": "nothing"},
            {"query_or_url": "q2", "result": "nothing"},
        ])
        self.assertEqual(r.returncode, 1)
        self.assertIn("NONE_FOUND but items is non-empty", r.stdout)

    def test_complete_with_no_items_is_refused(self):
        r = self.run_gate(items=[], status="COMPLETE")
        self.assertEqual(r.returncode, 1)
        self.assertIn("COMPLETE needs >= 1 item", r.stdout)

    def test_unmapped_issuer_is_refused(self):
        r = self.run_gate(items=[edgar_item()], unlink=["data/companies/ACM.json"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("has no data/companies/ACM.json profile", r.stdout)

    def test_missing_edgar_document_fails_closed(self):
        r = self.run_gate(items=[edgar_item()], unlink=["data/edgar/docs/ACM.json"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("no document on disk", r.stdout)

    def test_unfetched_web_page_fails(self):
        item = web_item("https://never-fetched.example.com/page")
        r = self.run_gate(items=[item])
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNFETCHED", r.stdout)

    def test_non_200_web_page_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root, _url = build_root(td)
            url = "https://investors.example.com/gone"
            (root / "data" / "web" / f"{es.web_doc_id(url)}.json").write_text(json.dumps(
                {"url": url, "http_status": 404, "text": ""}))
            obj = base_pipeline([web_item(url)])
            (root / "data" / "pipelines" / "ACM.json").write_text(json.dumps(obj))
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_pipeline.py"), "--root", str(root)],
                                capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("HTTP_404", r.stdout)

    def test_verdict_vocabulary_is_refused(self):
        pipeline = base_pipeline([edgar_item()])
        pipeline["verdict"] = "INVESTABLE"
        r = self.run_gate(pipeline=pipeline)
        self.assertEqual(r.returncode, 1)
        self.assertIn("verdict fields belong only in Stocky dives", r.stdout)

    def test_entry_price_field_is_refused(self):
        pipeline = base_pipeline([edgar_item()])
        pipeline["entry_zone"] = {"low": 1, "high": 2, "basis": "x"}
        r = self.run_gate(pipeline=pipeline)
        self.assertEqual(r.returncode, 1)
        self.assertIn("entry-price field does not belong", r.stdout)

    def test_bad_ticker_shape_is_refused(self):
        pipeline = base_pipeline([edgar_item()])
        pipeline["ticker"] = "not a ticker"
        r = self.run_gate(pipeline=pipeline)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a valid ticker shape", r.stdout)

    def test_item_missing_date_is_refused(self):
        item = edgar_item(date=None)
        r = self.run_gate(items=[item])
        self.assertEqual(r.returncode, 1)
        self.assertIn("is not YYYY-MM-DD", r.stdout)

    def test_bad_xbrl_ref_shape_is_refused(self):
        item = xbrl_item(source_ref="data/market/ACM.json")
        r = self.run_gate(items=[item])
        self.assertEqual(r.returncode, 1)
        self.assertIn("fundamentals.<field>", r.stdout)

    def test_gate_does_not_print_ok_over_a_missing_store(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data").mkdir()
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_pipeline.py"), "--root", str(root)],
                                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("NO STORE", r.stdout)
            self.assertNotIn("check_pipeline: OK", r.stdout)

    def test_gate_does_not_print_ok_over_an_empty_pipelines_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root, _url = build_root(td)
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_pipeline.py"), "--root", str(root)],
                                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("NO STORE", r.stdout)
            self.assertNotIn("check_pipeline: OK", r.stdout)

    def test_gate_reports_its_scored_over_total_denominator(self):
        r = self.run_gate(items=[edgar_item()])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1/1 item(s) scored", r.stdout)


class TestValidatePipelineWiring(unittest.TestCase):
    """`tools/validate.py` imports `validate_pipeline` (`v_pipeline`), the same wiring
    `check_profile.validate_profile` and `check_map.validate_mapping` already have. A gate
    whose subprocess CLI disagrees with its own importable function is worse than either
    alone, so both are exercised here."""

    def test_valid_pipeline_has_no_findings(self):
        with tempfile.TemporaryDirectory() as td:
            root, _url = build_root(td)
            obj = base_pipeline([edgar_item()])
            path = root / "data" / "pipelines" / "ACM.json"
            path.write_text(json.dumps(obj))
            self.assertEqual(validate_pipeline(root, path, obj), [])

    def test_broken_pipeline_has_findings(self):
        with tempfile.TemporaryDirectory() as td:
            root, _url = build_root(td)
            obj = base_pipeline([edgar_item(value=1, claim="a number nowhere in the excerpt")])
            path = root / "data" / "pipelines" / "ACM.json"
            path.write_text(json.dumps(obj))
            self.assertTrue(validate_pipeline(root, path, obj))


if __name__ == "__main__":
    unittest.main()
