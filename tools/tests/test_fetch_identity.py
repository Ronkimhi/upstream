#!/usr/bin/env python3
"""Unit tests for ticker->CIK identity resolution (cik_for + explicit overrides).

Added 2026-09-01 with the fix for the listing-identity-normalization defect
(tasks/backlog.md rows 46-47): the old cik_for() stripped the exchange suffix and
matched the stub against SEC's US ticker table, so a suffixed foreign listing
silently adopted an unrelated US filer's CIK (ENR.DE -> Energizer, BA.L -> Boeing)
and do_fundamentals wrote ANOTHER COMPANY's companyfacts into the foreign file,
tagged VERIFIED, with no gate able to fire.

What each test guards:
  - a suffixed ticker NEVER resolves via the base-strip path. The only dotted form
    that may match is the SEC table's own class-share spelling (BRK.B -> BRK-B).
  - an explicit request-row `cik` override wins over any lookup, in both the
    dotted and bare spellings, because that is how a real ADR link (BP.L) keeps
    its SEC leg now that inference is forbidden.
  - a bare ticker still resolves exactly as before, hyphenated or not.
  - a garbage override is ignored loudly, never crashes the run.
  - the do_prices failure note names the per-leg reasons, not the one-word lie
    "price sources unreachable" that blamed the wrong plane for MMC.

No network: _company_tickers is pre-seeded and requests is never touched.

Run: python3 -m unittest discover -s tools/tests -q
"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch = _load("fetch_identity_mod", ROOT / "tools" / "fetch" / "fetch.py")

# What the SEC table build produces: raw spelling plus hyphen-stripped alias.
SEED = {
    "ENR": 1632790,       # Energizer Holdings — the US filer ENR.DE must NOT adopt
    "BA": 12927,          # Boeing — BA.L (BAE) must NOT adopt
    "BRK-B": 1067983,     # Berkshire class B, raw SEC spelling
    "BRKB": 1067983,      # hyphen-stripped alias of the same
    "NXT": 1852131,       # Nextracker — bare NXT resolves to it, NXT.AX must not
}


class TestCikFor(unittest.TestCase):
    def setUp(self):
        fetch._company_tickers = dict(SEED)
        fetch._cik_overrides.clear()

    def test_suffixed_foreign_ticker_gets_no_cik(self):
        for t in ("ENR.DE", "BA.L", "NXT.AX", "SU.PA", "7011.T", "000660.KS"):
            self.assertIsNone(fetch.cik_for(t), t)

    def test_class_share_resolves_via_sec_spelling(self):
        self.assertEqual(fetch.cik_for("BRK.B"), 1067983)
        self.assertEqual(fetch.cik_for("BRK-B"), 1067983)

    def test_bare_ticker_still_resolves(self):
        self.assertEqual(fetch.cik_for("ENR"), 1632790)
        self.assertEqual(fetch.cik_for("NXT"), 1852131)

    def test_override_wins_for_suffixed_ticker(self):
        fetch.register_cik_override("BP.L", 313807)
        self.assertEqual(fetch.cik_for("BP.L"), 313807)
        # and is case-insensitive on the ticker
        self.assertEqual(fetch.cik_for("bp.l"), 313807)

    def test_override_wins_over_table_for_bare_ticker(self):
        fetch.register_cik_override("NXT", 999999)
        self.assertEqual(fetch.cik_for("NXT"), 999999)

    def test_garbage_override_is_ignored(self):
        fetch.register_cik_override("BP.L", "not-a-cik")
        self.assertIsNone(fetch.cik_for("BP.L"))

    def test_tier_still_derived_from_cik_and_suffix(self):
        self.assertEqual(fetch.tier_for("ENR", fetch.cik_for("ENR")), "T1")
        self.assertEqual(fetch.tier_for("ENR.DE", fetch.cik_for("ENR.DE")), "T3")
        fetch.register_cik_override("BP.L", 313807)
        self.assertEqual(fetch.tier_for("BP.L", fetch.cik_for("BP.L")), "T2")


class TestPriceFailureNote(unittest.TestCase):
    """The all-legs-empty raise must name each leg's reason and the probe's."""

    def setUp(self):
        fetch._company_tickers = dict(SEED)
        fetch._cik_overrides.clear()
        self._ds = fetch.dual_source_price
        self._series = fetch.fetch_series
        self._control = dict(fetch._control)

    def tearDown(self):
        fetch.dual_source_price = self._ds
        fetch.fetch_series = self._series
        fetch._control.update(self._control)

    def test_note_carries_leg_reasons_and_probe_reason(self):
        fetch.dual_source_price = lambda t: {
            "status": "NO-DATA",
            "detail": {"legs": {"yfinance": {"status": "NO_DATA", "note": "no history for symbol"},
                                "stockanalysis": {"status": "ERROR", "note": "http 400"}}}}
        fetch.fetch_series = lambda t: (None, None)
        fetch._control.update({"checked": True, "ok": False,
                               "reason": "yfinance AAPL history came back empty"})
        with self.assertRaises(RuntimeError) as cm:
            fetch.do_prices("YTLPOWR.KL")
        msg = str(cm.exception)
        self.assertIn("no history for symbol", msg)
        self.assertIn("http 400", msg)
        self.assertIn("yfinance AAPL history came back empty", msg)
        self.assertNotIn("price sources unreachable", msg)


if __name__ == "__main__":
    unittest.main()
