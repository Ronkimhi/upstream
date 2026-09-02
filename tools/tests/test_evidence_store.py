#!/usr/bin/env python3
"""The web evidence store: one canonical URL rule shared with the fetcher, one verbatim rule
shared with check_screen, and a verifier whose every state can be produced."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import evidence_store as es  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


URLS = [
    "https://www.Example.com/a/b/?utm_source=x&q=1#frag",
    "http://example.com:80/path/",
    "https://example.com/path?b=2&a=1",
    "https://data.sec.gov/submissions/CIK0001091587.json",
    "https://example.com",
]


class TestCanonicalUrl(unittest.TestCase):
    def test_tracking_fragment_and_case_are_folded(self):
        self.assertEqual(es.canonical_url(URLS[0]), "https://www.example.com/a/b?q=1")
        self.assertEqual(es.canonical_url(URLS[1]), "http://example.com/path")
        self.assertEqual(es.canonical_url(URLS[2]), "https://example.com/path?a=1&b=2")
        self.assertEqual(es.canonical_url(URLS[4]), "https://example.com/")

    def test_fetcher_and_reader_agree_on_every_shape(self):
        fetch = _load("fetch_web_mod", ROOT / "tools" / "fetch" / "fetch.py")
        for u in URLS:
            self.assertEqual(fetch._web_canonical(u), es.canonical_url(u), u)

    def test_id_is_stable_across_spellings(self):
        self.assertEqual(es.web_doc_id("https://Example.com/x?utm_medium=m"),
                         es.web_doc_id("https://example.com/x/"))


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        (self.data / "web").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def store(self, url, text, status=200):
        (self.data / "web" / f"{es.web_doc_id(url)}.json").write_text(json.dumps(
            {"url": url, "http_status": status, "text": text}))

    def test_every_state_is_reachable(self):
        url = "https://example.com/story"
        item = {"url": url, "source_excerpt": "Exports fell to 4.9 mb/d — the lowest since 2021."}
        self.assertEqual(es.verify(self.data, item)["state"], "UNFETCHED")
        self.store(url, "Analysts said exports fell to 4.9 mb/d - the lowest since 2021. More.")
        self.assertEqual(es.verify(self.data, item)["state"], "MATCH",
                         "an em dash in the quote must fold to the ASCII dash in the page")
        self.store(url, "Exports rose to 6.1 mb/d.")
        self.assertEqual(es.verify(self.data, item)["state"], "MISMATCH")
        self.store(url, "", status=403)
        self.assertEqual(es.verify(self.data, item)["state"], "HTTP_403")
        self.store(url, "")
        self.assertEqual(es.verify(self.data, item)["state"], "EMPTY")
        self.assertEqual(es.verify(self.data, {"url": url})["state"], "NO_EXCERPT")
        self.assertEqual(es.verify(self.data, {"claim": "x"})["state"], "NO_URL")

    def test_corpus_findings_fail_only_stored_contradictions(self):
        good, bad, gone = ("https://e.com/1", "https://e.com/2", "https://e.com/3")
        self.store(good, "the plant makes 200 GW of parts")
        self.store(bad, "the plant makes 474 GW of parts")
        items = [
            ("a", {"url": good, "source_excerpt": "makes 200 GW"}),
            ("b", {"url": bad, "source_excerpt": "makes 200 GW"}),
            ("c", {"url": gone, "source_excerpt": "makes 200 GW"}),
        ]
        failures, counts = es.corpus_web_findings(self.data, items, None)
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("b:", failures[0])
        self.assertEqual(counts, {"MATCH": 1, "MISMATCH": 1, "UNFETCHED": 1})


if __name__ == "__main__":
    unittest.main()
