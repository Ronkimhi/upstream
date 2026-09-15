#!/usr/bin/env python3
"""Unit tests for FACT_MAP concept selection and derived fields in the SEC companyfacts
leg (`tools/fetch/fetch.py:_fundamentals_sec`).

Added 2026-09-15 alongside the fix for ETN, GEV, NVT, POWL, BDX, AON, AJG, WTW reading
NULL on fields their filings do carry, diagnosed from the profiles' NULL bases on origin.
Investigation via data.sec.gov/api/xbrl/companyfacts/CIK##########.json for all eight
found `pick()`'s freshest-wins/longest-tiebreak selection already correct (added
2026-09-04/06); the remaining gaps were either missing candidate concepts (NVT
receivables, AJG capex — both confirmed live in those filers' companyfacts) or missing
derived identities (ETN and BDX gross_profit, computable from revenue and
cost_of_revenue they both tag every year), or genuine absences with no fix possible
(GEV ppe_net/long_term_debt and POWL long_term_debt have zero annual-form facts under any
candidate concept in companyfacts; AJG tags no operating_income concept at all) — these
three are NOT covered by a test because there is nothing to assert: the correct behavior
is staying NULL.

What each test guards:
  - a field whose FIRST-preference concept is stale loses to a fresher candidate lower in
    the list (the general mechanism `pick()` already implements for concept selection).
  - a tie in freshness and series length is broken by list order (first listed wins),
    so widening a candidate list can never silently flip an existing filer's answer.
  - the two candidate concepts added 2026-09-15 (AccountsAndNotesReceivableNet for
    receivables, PaymentsForCapitalImprovements for capex) are actually read when they
    are a field's only source.
  - gross_profit = revenue - cost_of_revenue is derived, exactly like the pre-existing
    total_liabilities = total_assets - equity identity, only when GrossProfit itself
    is absent, and never overrides a real GrossProfit tag when one exists.

No network — same harness as test_fetch_fundamentals.py (`_SecRequests`, `_FakeResponse`,
`FundamentalsLegTestCase`). Run: python3 -m unittest discover -s tools/tests -q
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


# Reuse the no-network test harness rather than re-implementing it — importantly, this
# loads the SAME `fetch` module object the harness's setUp/tearDown patches DATA/requests/
# cik_for on. A second independent `_load("fetch.py")` here would give this file its own
# copy of the module, so `fetch.DATA` in the harness (still the real repo path) and
# `fetch.DATA` used by a test's own calls (a tempdir) would silently diverge — exactly
# what happened the first time this file was written: do_fundamentals ran against the
# harness's un-patched copy and wrote straight into this worktree's data/market/.
fund_tests = _load("fetch_fund_tests_mod", Path(__file__).resolve().parent / "test_fetch_fundamentals.py")
fetch = fund_tests.fetch


def _usd_facts(concepts):
    """Build a minimal companyfacts payload.

    Each row is either (start, end, val, form) for a duration fact or (end, val, form)
    for an instant fact (no start)."""
    gaap = {}
    for name, rows in concepts.items():
        vals = []
        for row in rows:
            if len(row) == 4:
                start, end, val, form = row
            else:
                end, val, form = row
                start = None
            entry = {"form": form, "end": end, "val": val}
            if start:
                entry["start"] = start
            vals.append(entry)
        gaap[name] = {"units": {"USD": vals}}
    return {"facts": {"us-gaap": gaap, "dei": {}}}


class ConceptFreshnessTestCase(fund_tests.FundamentalsLegTestCase):
    """Shared fixture: point the SEC leg at a synthetic companyfacts payload for one CIK."""

    def _fetch(self, ticker, cik, concepts):
        sec = fund_tests._SecRequests(_usd_facts(concepts))
        fetch.requests = sec
        fetch.cik_for = lambda t: cik
        fetch.do_fundamentals(ticker)
        return self.written(ticker)


class TestStaleConceptLosesToFreshFallback(ConceptFreshnessTestCase):
    def test_second_candidate_wins_when_first_is_stale(self):
        """total_debt's candidate list has LongTermDebtNoncurrent before LongTermDebt.
        A filer whose LongTermDebtNoncurrent stopped years ago but whose LongTermDebt
        keeps running must read the fresh one, not the stale first-preference concept."""
        f = self._fetch("STALECO", 1, {
            "Revenues": [("2023-01-01", "2023-12-31", 100.0, "10-K"),
                         ("2024-01-01", "2024-12-31", 110.0, "10-K"),
                         ("2025-01-01", "2025-12-31", 120.0, "10-K")],
            "LongTermDebtNoncurrent": [("2014-12-31", 999.0, "10-K")],
            "LongTermDebt": [("2023-12-31", 50.0, "10-K"),
                             ("2024-12-31", 55.0, "10-K"),
                             ("2025-12-31", 60.0, "10-K")],
        })
        self.assertEqual(list(f["long_term_debt_fy"][-1]), ["2025-12-31", 60.0])
        # The stale concept must not leak into the answer at all.
        self.assertNotIn(999.0, [v for _, v in f["long_term_debt_fy"]])


class TestTieKeepsPreference(ConceptFreshnessTestCase):
    def test_first_listed_concept_wins_an_exact_tie(self):
        """Two candidates with identical periods and values: list order must still pick
        the first (RevenueFromContractWithCustomerExcludingAssessedTax before Revenues),
        proven by making the two concepts DISAGREE on value so the winner is visible."""
        f = self._fetch("TIECO", 2, {
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                ("2024-01-01", "2024-12-31", 111.0, "10-K"),
                ("2025-01-01", "2025-12-31", 222.0, "10-K")],
            "Revenues": [
                ("2024-01-01", "2024-12-31", 999.0, "10-K"),
                ("2025-01-01", "2025-12-31", 888.0, "10-K")],
        })
        self.assertEqual(list(f["revenue_fy"][-1]), ["2025-12-31", 222.0])


class TestNewCandidateConcepts(ConceptFreshnessTestCase):
    """The two concepts added 2026-09-15, each as a field's only source."""

    def test_accounts_and_notes_receivable_net_is_read_for_receivables(self):
        f = self._fetch("NVTLIKE", 3, {
            "Revenues": [("2024-01-01", "2024-12-31", 100.0, "10-K"),
                         ("2025-01-01", "2025-12-31", 110.0, "10-K")],
            "AccountsAndNotesReceivableNet": [
                ("2024-12-31", 470.0, "10-K"), ("2025-12-31", 693.0, "10-K")],
        })
        self.assertEqual(list(f["receivables_fy"][-1]), ["2025-12-31", 693.0])

    def test_payments_for_capital_improvements_is_read_for_capex(self):
        f = self._fetch("AJGLIKE", 4, {
            "Revenues": [("2024-01-01", "2024-12-31", 100.0, "10-K"),
                         ("2025-01-01", "2025-12-31", 110.0, "10-K")],
            "PaymentsForCapitalImprovements": [
                ("2024-01-01", "2024-12-31", 141.9, "10-K"),
                ("2025-01-01", "2025-12-31", 145.0, "10-K")],
        })
        self.assertEqual(list(f["capex_fy"][-1]), ["2025-12-31", 145.0])

    def test_payments_for_capital_improvements_never_beats_a_fresher_ppe_purchase_line(self):
        """The new concept is listed last: it must lose to
        PaymentsToAcquirePropertyPlantAndEquipment whenever that one is at least as
        fresh, so no existing filer's capex answer can flip from this widening."""
        f = self._fetch("PPECO", 5, {
            "Revenues": [("2024-01-01", "2024-12-31", 100.0, "10-K"),
                         ("2025-01-01", "2025-12-31", 110.0, "10-K")],
            "PaymentsToAcquirePropertyPlantAndEquipment": [
                ("2024-01-01", "2024-12-31", 30.0, "10-K"),
                ("2025-01-01", "2025-12-31", 33.0, "10-K")],
            "PaymentsForCapitalImprovements": [
                ("2024-01-01", "2024-12-31", 141.9, "10-K"),
                ("2025-01-01", "2025-12-31", 145.0, "10-K")],
        })
        self.assertEqual(list(f["capex_fy"][-1]), ["2025-12-31", 33.0])


class TestGrossProfitDerivation(ConceptFreshnessTestCase):
    def test_derived_when_gross_profit_concept_is_absent(self):
        """ETN's shape: revenue and cost_of_revenue both tagged every year, no GrossProfit
        concept anywhere in companyfacts."""
        f = self._fetch("ETNLIKE", 6, {
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                ("2024-01-01", "2024-12-31", 24878.0, "10-K"),
                ("2025-01-01", "2025-12-31", 27448.0, "10-K")],
            "CostOfGoodsAndServicesSold": [
                ("2024-01-01", "2024-12-31", 15375.0, "10-K"),
                ("2025-01-01", "2025-12-31", 17131.0, "10-K")],
        })
        self.assertEqual(list(f["gross_profit_fy"][-1]), ["2025-12-31", 27448.0 - 17131.0])
        self.assertEqual(f["derived"]["gross_profit_fy"], "revenue_fy - cost_of_revenue_fy")
        self.assertNotIn("gross_profit", f["coverage"]["missing"])

    def test_derived_when_gross_profit_concept_is_stale(self):
        """BDX's shape: GrossProtif tagged through FY2020 only, then dropped as stale
        while revenue/cost_of_revenue keep running — the derivation must still fill it,
        not merely accept the stale value."""
        f = self._fetch("BDXLIKE", 7, {
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                ("2023-10-01", "2024-09-30", 20178.0, "10-K"),
                ("2024-10-01", "2025-09-30", 21840.0, "10-K")],
            "CostOfGoodsAndServicesSold": [
                ("2023-10-01", "2024-09-30", 11053.0, "10-K"),
                ("2024-10-01", "2025-09-30", 11915.0, "10-K")],
            "GrossProfit": [("2019-10-01", "2020-09-30", 9000.0, "10-K")],
        })
        self.assertEqual(list(f["gross_profit_fy"][-1]), ["2025-09-30", 21840.0 - 11915.0])
        self.assertIn("gross_profit", f["stale_dropped"])

    def test_real_gross_profit_concept_is_never_overridden_by_the_derivation(self):
        """A filer that still tags GrossProfit fresh must read its own filed number, not
        a computed one — even though it would compute to the same value here, the
        `derived` key must be absent, proving the real concept path was taken."""
        f = self._fetch("REALGP", 8, {
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                ("2024-01-01", "2024-12-31", 100.0, "10-K"),
                ("2025-01-01", "2025-12-31", 110.0, "10-K")],
            "CostOfGoodsAndServicesSold": [
                ("2024-01-01", "2024-12-31", 60.0, "10-K"),
                ("2025-01-01", "2025-12-31", 66.0, "10-K")],
            "GrossProfit": [("2024-01-01", "2024-12-31", 40.0, "10-K"),
                            ("2025-01-01", "2025-12-31", 44.0, "10-K")],
        })
        self.assertEqual(list(f["gross_profit_fy"][-1]), ["2025-12-31", 44.0])
        self.assertNotIn("gross_profit_fy", f.get("derived", {}))


if __name__ == "__main__":
    unittest.main()
