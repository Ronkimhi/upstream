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


if __name__ == "__main__":
    unittest.main()
