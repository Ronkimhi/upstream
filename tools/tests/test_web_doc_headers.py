import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location("fetch", ROOT / "tools" / "fetch" / "fetch.py")
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)


class TestWebDocHeaders(unittest.TestCase):
    """sec.gov pages fetched through the web_doc plane must carry the declared EDGAR agent
    (SEC access policy); everything else keeps the browser-like agent. 2026-09-06: two AEVA
    exhibit pages stored as 403 under the browser agent and failed validate for every
    session until the citation could be re-fetched."""

    def test_sec_hosts_use_edgar_agent(self):
        for url in ("https://www.sec.gov/Archives/edgar/data/1789029/000119312526335102/aeva-ex99_1.htm",
                    "https://data.sec.gov/submissions/CIK0001789029.json",
                    "https://efts.sec.gov/LATEST/search-index?q=x"):
            self.assertTrue(fetch._is_sec_host(url), url)
            self.assertEqual(fetch._web_headers(url)["User-Agent"], fetch.EDGAR_USER_AGENT)

    def test_other_hosts_keep_browser_agent(self):
        for url in ("https://www.globenewswire.com/x", "https://sec.gov.example.com/x",
                    "https://example.com/sec.gov", "not a url"):
            self.assertFalse(fetch._is_sec_host(url), url)
            self.assertEqual(fetch._web_headers(url)["User-Agent"], fetch.WEB_USER_AGENT)


if __name__ == "__main__":
    unittest.main()
