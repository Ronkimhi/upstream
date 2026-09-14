#!/usr/bin/env python3
"""The reader-side market path must equal the fetcher's filename for every ticker shape."""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import market_paths  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMarketPaths(unittest.TestCase):
    SHAPES = ["AAPL", "1072.HK", "600875.SS", "ANDR.VI", "BRK.B", "HPS.A", "7011.T",
              "0390.HK", "NHPC.NS", "SGL.DE", "RR.L", "BP", "3816-KL"]

    def test_reader_rule_matches_fetcher_rule(self):
        fetch = _load("fetch_paths_mod", ROOT / "tools" / "fetch" / "fetch.py")
        for t in self.SHAPES:
            self.assertEqual(market_paths.safe_name(t), fetch.safe_name(t), t)

    def test_market_path_shape(self):
        self.assertEqual(market_paths.market_path(Path("data"), "1072.HK"),
                         Path("data/market/1072-HK.json"))
        self.assertEqual(market_paths.edgar_doc_path(Path("data"), "BRK.B"),
                         Path("data/edgar/docs/BRK-B.json"))

    def test_every_market_file_on_disk_is_reachable_from_a_dotted_reference(self):
        """A file the fetcher wrote must resolve from the ticker the mapping references."""
        folder = ROOT / "data" / "market"
        if not folder.is_dir():
            self.skipTest("no data/market on this tree")
        for f in folder.glob("*.json"):
            if f.name.startswith("_"):
                continue
            dotted = f.stem.replace("-", ".", 1) if "-" in f.stem else f.stem
            self.assertEqual(market_paths.market_path(ROOT / "data", dotted).name, f.name)


class TestResolveMarketStem(unittest.TestCase):
    """app/build.py's shared resolver for a mapping listing that carries a bare local
    code and no `market_ticker` (168 of 370 listings on 2026-09-13): the fetcher still
    wrote the file under an exchange-suffixed name, and the resolver must recover it from
    `exchange` alone. Fixtures are the two real cases named in the engineering brief."""

    def test_bare_taiwan_code_resolves_through_exchange_suffix(self):
        available = {"2330-TW", "AAPL"}
        self.assertEqual(
            market_paths.resolve_market_stem(ticker="2330", exchange="TWSE",
                                              available=available),
            "2330-TW")

    def test_bare_toronto_code_resolves_through_exchange_suffix(self):
        available = {"WSP-TO"}
        self.assertEqual(
            market_paths.resolve_market_stem(ticker="WSP", exchange="TSX",
                                              available=available),
            "WSP-TO")

    def test_market_ticker_wins_over_a_derived_suffix(self):
        # The mapping's own fetched-form record is stronger evidence than a derived guess.
        available = {"2330-TWO"}
        self.assertEqual(
            market_paths.resolve_market_stem(ticker="2330", exchange="TWSE",
                                              market_ticker="2330.TWO",
                                              available=available),
            "2330-TWO")

    def test_already_dotted_ticker_needs_no_suffix(self):
        available = {"2899-HK"}
        self.assertEqual(
            market_paths.resolve_market_stem(ticker="2899.HK", exchange="HKEX",
                                              available=available),
            "2899-HK")

    def test_plain_us_ticker_resolves_bare(self):
        available = {"POWL"}
        self.assertEqual(
            market_paths.resolve_market_stem(ticker="POWL", exchange="NASDAQ",
                                              available=available),
            "POWL")

    def test_unfetched_listing_resolves_to_none_never_fabricated(self):
        self.assertIsNone(
            market_paths.resolve_market_stem(ticker="2330", exchange="TWSE",
                                              available={"AAPL"}))
        self.assertIsNone(
            market_paths.resolve_market_stem(ticker="ZZZZ", exchange="UNKNOWN EXCHANGE",
                                              available={"AAPL"}))

    def test_unknown_exchange_never_invents_a_suffix(self):
        self.assertIsNone(
            market_paths.resolve_market_stem(ticker="1234", exchange="SOME MADE UP VENUE",
                                              available={"1234-XX"}))

    def test_available_as_directory_checks_disk(self):
        folder = ROOT / "data" / "market"
        if not folder.is_dir():
            self.skipTest("no data/market on this tree")
        any_file = next(folder.glob("*.json"), None)
        if any_file is None:
            self.skipTest("data/market is empty on this tree")
        stem = any_file.stem
        dotted = stem.replace("-", ".", 1) if "-" in stem else stem
        self.assertEqual(
            market_paths.resolve_market_stem(ticker=dotted, available=folder), stem)


if __name__ == "__main__":
    unittest.main()
