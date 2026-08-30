#!/usr/bin/env python3
"""Exact tests for the impact appraisal: band arithmetic, the gate, and the queue shape."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from impact_score import (  # noqa: E402
    BANDS, MONEY_BANDS, compute, expected_review_by, rank_key, scan_ticker_venue,
)
from queue_allowlist import is_allowed, reject_reason  # noqa: E402

TODAY = "2026-08-30"


def leg(score, tag="INFERRED"):
    return {
        "score": score,
        "rationale": "A written rationale.",
        "evidence": [{"claim": "A dated claim.", "source_name": "Source",
                      "source_date": "2026-08-01", "url": "https://example.com/a",
                      "tag": tag}],
    }


def valid_appraisal():
    return {
        "id": "IMP-20260830-01",
        "occurrence_id": "SIG-20260829-01",
        "as_of": TODAY,
        "anchor_date": "2025-07-01",
        "money_at_stake": {
            "band": "B10_100",
            "rationale": "A written rationale.",
            "evidence": [{"claim": "A dated, sourced pool figure.", "source_name": "Source",
                          "source_date": "2026-08-01", "url": "https://example.com/m",
                          "tag": "INFERRED"}],
        },
        "public_reach": leg(70),
        "capture_odds": leg(65),
        "timing_fit": leg(70),
        "impact_score": None,   # filled by the fixture builder below
        "impact_band": None,
        "ticker_refs": [],
        "review_by": "2026-11-28",
        "confidence_audit": {"verified": 0, "inferred": 4, "speculative": 0, "null": 0},
        "notes": [],
        "changelog": [{"ts": f"{TODAY}T00:00:00Z", "by": "tally-appraiser",
                       "change": "new appraisal"}],
    }


def sealed(a=None):
    """An appraisal whose computed fields agree with its own legs."""
    a = a or valid_appraisal()
    c = compute(a)
    a["impact_score"], a["impact_band"] = c["impact_score"], c["impact_band"]
    return a


class TestBandArithmetic(unittest.TestCase):
    def mk(self, band, reach, capture, timing):
        return {"id": "X", "money_at_stake": {"band": band},
                "public_reach": {"score": reach}, "capture_odds": {"score": capture},
                "timing_fit": {"score": timing}}

    def test_bands_partition_the_space(self):
        """Every combination lands in exactly one band. This is the defect the 2026-08-29
        section 3 amendment fixed after three links shipped on a gap in a table."""
        seen = set()
        for band in MONEY_BANDS:
            for reach in (0, 39, 40, 59, 60, 85, 100):
                for capture in (0, 39, 40, 59, 60, 100):
                    for timing in (0, 60, 100):
                        got = compute(self.mk(band, reach, capture, timing))["impact_band"]
                        self.assertIn(got, BANDS)
                        seen.add(got)
        self.assertEqual(seen, set(BANDS) - {"UNRANKED"})

    def test_leaky_is_reachable_money_that_is_not_reachable(self):
        self.assertEqual(compute(self.mk("GT_100B", 20, 80, 80))["impact_band"], "LEAKY")

    def test_competed_is_its_own_band_not_a_fall_through(self):
        self.assertEqual(compute(self.mk("GT_100B", 80, 25, 80))["impact_band"], "COMPETED")

    def test_prime_needs_all_three(self):
        self.assertEqual(compute(self.mk("GT_100B", 80, 70, 80))["impact_band"], "PRIME")
        self.assertNotEqual(compute(self.mk("B1_10", 80, 70, 80))["impact_band"], "PRIME")

    def test_a_null_leg_yields_no_score_at_all(self):
        """A partial average is still a number, and a number gets sorted and quoted."""
        c = compute(self.mk("B10_100", None, 60, 60))
        self.assertEqual(c["impact_band"], "UNRANKED")
        self.assertIsNone(c["impact_score"])
        self.assertEqual(c["null_legs"], ["public_reach"])

    def test_geometric_mean_punishes_one_weak_leg(self):
        """One weak leg drags the composite well below the arithmetic mean, without
        zeroing it. An occurrence moving $100B that no listed issuer touches should not
        read as four fifths of an opportunity, which is what averaging would give it."""
        legs = (90, 10, 90, 90)
        arithmetic = sum(legs) / 4                       # 70.0
        got = compute(self.mk("GT_100B", 10, 90, 90))["impact_score"]
        self.assertLess(got, arithmetic - 15)
        self.assertGreater(got, 0)
        self.assertLess(got, compute(self.mk("GT_100B", 90, 90, 90))["impact_score"])

    def test_unranked_sorts_last(self):
        ranked = sealed()
        unranked = valid_appraisal()
        unranked["public_reach"] = {"score": None, "basis": "no source found"}
        order = sorted([unranked, ranked], key=rank_key)
        self.assertEqual(order[0]["id"], ranked["id"])


def queue_row(appraisal, unmappedness=80):
    c = compute(appraisal)
    return {
        "occurrence_id": appraisal["occurrence_id"],
        "appraisal_id": appraisal["id"],
        "impact_score": c["impact_score"],
        "impact_band": c["impact_band"],
        "unmappedness": unmappedness,
    }


def write_signal(root, sig_id, anchor="2025-07-01", unmappedness=80):
    (root / "data" / "signals" / f"{sig_id}.json").write_text(json.dumps({
        "id": sig_id,
        "occurrence": {"anchor_date": anchor},
        "unmappedness": {"score": unmappedness},
    }))


class TestVenueScanner(unittest.TestCase):
    def test_expected_review_by_is_plus_ninety_days(self):
        self.assertEqual(expected_review_by("2026-08-30"), "2026-11-28")

    def test_scan_finds_hidden_share_price(self):
        scan = scan_ticker_venue({
            "id": "X", "ticker_refs": [],
            "ticker_facts": {"ZZZ": {"share_price": 999999}},
        })
        self.assertEqual(scan["undeclared_top_keys"], ["ticker_facts"])
        self.assertEqual(scan["undeclared_tickers"], ["ZZZ"])
        self.assertEqual(scan["ticker_metrics"][0]["ticker"], "ZZZ")

    def test_scan_finds_nested_price_usd_alias(self):
        scan = scan_ticker_venue({
            "id": "X", "ticker_refs": [],
            "notes": [{"ts": "2026-08-30T00:00:00Z", "by": "x", "text": "probe",
                       "ticker": "ZZZ", "price_usd": 999999}],
        })
        self.assertEqual(scan["ticker_metrics"][0]["metric"], "price_usd")
        self.assertEqual(scan["undeclared_tickers"], ["ZZZ"])

    def test_scan_finds_arbitrary_numeric_alias(self):
        scan = scan_ticker_venue({
            "id": "X", "ticker_refs": [],
            "notes": [{"ts": "2026-08-30T00:00:00Z", "by": "x", "text": "probe",
                       "ticker": "ABC", "widget_count_usd": 12345.67}],
        })
        self.assertEqual(scan["ticker_metrics"][0]["ticker"], "ABC")
        self.assertEqual(scan["ticker_metrics"][0]["metric"], "widget_count_usd")


    def test_scan_finds_market_ticker_alias(self):
        scan = scan_ticker_venue({
            "id": "X", "ticker_refs": [],
            "notes": [{"ts": "2026-08-30T00:00:00Z", "by": "x", "text": "probe",
                       "market_ticker": "ZZZ", "widget_count_usd": 12345.67}],
        })
        self.assertEqual(scan["ticker_metrics"][0]["ticker"], "ZZZ")
        self.assertEqual(scan["ticker_metrics"][0]["metric"], "widget_count_usd")


class TestQueueShape(unittest.TestCase):
    def test_valid_shapes_allowed(self):
        self.assertTrue(is_allowed("run impact SIG-20260829-01"))
        self.assertTrue(is_allowed("run impact CAND-20260829-01"))

    def test_injection_probes(self):
        for bad in (
            "run impact SIG-20260829-01 && echo pwned",
            "run impact SIG-20260829-01; rm -rf data",
            "run impact SIG-20260829-01 | tee /tmp/x",
            "run impact SIG-20260829-01\nrun radar",
            "run impact SIG-20260829-01\r\nrun radar",
            "run impact SIG-20260829-01\x00",
            " run impact SIG-20260829-01",
            "run impact SIG-20260829-01 ",
            "run impact ../../etc/passwd",
            "run impact SIG-20260829-01 SIG-20260829-02",
            "run impact",
            "run impact SIG-1",
            "run impact CAND-2026-08-29",
        ):
            self.assertFalse(is_allowed(bad), f"allowed: {bad!r}")
            self.assertTrue(reject_reason(bad))


class TestGate(unittest.TestCase):
    """The gate has to FAIL on each defect. A gate that cannot fail is not a gate."""

    def run_gate(self, mutate=None, ledger=True, calibrate=True, extra_appraisals=None,
                 rank_queue=None, gate_date=TODAY, skip_primary=False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data" / "impact").mkdir(parents=True)
            (root / "data" / "signals").mkdir(parents=True)
            (root / "data" / "radar").mkdir(parents=True)
            (root / "data" / "market").mkdir(parents=True)

            (root / "data" / "signals" / "SIG-20260829-01.json").write_text(json.dumps(
                {"id": "SIG-20260829-01", "occurrence": {"anchor_date": "2025-07-01"},
                 "unmappedness": {"score": 80}}))
            (root / "data" / "radar" / "candidates.json").write_text(
                json.dumps({"candidates": []}))

            a = sealed()
            if mutate:
                a = mutate(copy.deepcopy(a))
            if not skip_primary:
                (root / "data" / "impact" / f"{a['id']}.json").write_text(json.dumps(a))

            for extra in extra_appraisals or []:
                (root / "data" / "impact" / f"{extra['id']}.json").write_text(json.dumps(extra))

            primary_row = queue_row(a)
            queue = rank_queue if rank_queue is not None else ([] if skip_primary else [primary_row])
            (root / "data" / "impact" / "_rank-log.json").write_text(json.dumps(
                {"as_of": gate_date,
                 "calibration": {"as_of": gate_date if calibrate else "2020-01-01",
                                 "denominators": {"occurrences_on_disk": 1, "unappraised": 0}},
                 "queue": queue}))
            run_day = gate_date == TODAY
            (root / "data" / "ledger.md").write_text(
                f"{gate_date} 00:00Z | IMPACT | run impact SIG-20260829-01 | by: ron | "
                f"wrote: data/impact | result: ranked: 1 band: PRIME | health: 1/1 | "
                f"artifact: skipped(test)\n" if ledger and run_day else "")

            return subprocess.run(
                [sys.executable, str(ROOT / "tools" / "check_impact.py"),
                 "--root", str(root), "--date", gate_date],
                capture_output=True, text=True)

    def test_clean_appraisal_passes(self):
        r = self.run_gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_hand_written_band_fails(self):
        self.assertEqual(compute(valid_appraisal())["impact_band"], "PRIME")  # the seal
        r = self.run_gate(lambda a: {**a, "impact_band": "REACHABLE"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("disagrees with its own legs", r.stdout)

    def test_speculative_money_fails(self):
        def m(a):
            a["money_at_stake"]["evidence"][0]["tag"] = "SPECULATIVE"
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 1)
        self.assertIn("SPECULATIVE", r.stdout)

    def test_missing_occurrence_fails(self):
        r = self.run_gate(lambda a: {**a, "occurrence_id": "SIG-20990101-99"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("no signal file", r.stdout)

    def test_anchor_disagreement_fails(self):
        r = self.run_gate(lambda a: {**a, "anchor_date": "2024-01-01"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("disagrees with", r.stdout)

    def test_unfetched_ticker_fails(self):
        r = self.run_gate(lambda a: {**a, "ticker_refs": ["VRT"]})
        self.assertEqual(r.returncode, 1)
        self.assertIn("never invents", r.stdout)

    def test_hidden_ticker_facts_share_price_fails(self):
        def m(a):
            a["ticker_facts"] = {"ZZZ": {"share_price": 999999}}
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ticker_facts", r.stdout)

    def test_empty_ticker_refs_with_hidden_facts_fails(self):
        """Venue rule must scan the tree, not ticker_refs alone."""
        def m(a):
            a["ticker_refs"] = []
            a["ticker_facts"] = {"ZZZ": {"share_price": 999999}}
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ZZZ", r.stdout)

    def test_two_appraisals_one_occurrence_fails(self):
        second = sealed(valid_appraisal())
        second["id"] = "IMP-20260830-02"
        second["occurrence_id"] = "SIG-20260829-01"
        r = self.run_gate(extra_appraisals=[second], rank_queue=[
            {"occurrence_id": "SIG-20260829-01", "appraisal_id": "IMP-20260830-01",
             "impact_band": "PRIME", "unmappedness": 80},
            {"occurrence_id": "SIG-20260829-01", "appraisal_id": "IMP-20260830-02",
             "impact_band": "PRIME", "unmappedness": 80},
        ])
        self.assertEqual(r.returncode, 1)
        self.assertIn("2 appraisal(s)", r.stdout)

    def test_review_by_one_day_after_as_of_fails(self):
        r = self.run_gate(lambda a: {**a, "review_by": "2026-08-31"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("as_of + 90 days", r.stdout)

    def test_duplicate_queue_occurrence_fails(self):
        row = {"occurrence_id": "SIG-20260829-01", "appraisal_id": "IMP-20260830-01",
               "impact_band": "PRIME", "unmappedness": 80}
        r = self.run_gate(rank_queue=[row, dict(row)])
        self.assertEqual(r.returncode, 1)
        self.assertIn("duplicates occurrence_id", r.stdout)

    def test_cass_tally_probe_fails(self):
        """Full Cass attack: two appraisals, one-day review_by, hidden ticker_facts."""
        def m(a):
            a["review_by"] = "2026-08-31"
            a["ticker_facts"] = {"ZZZ": {"share_price": 999999}}
            a["ticker_refs"] = []
            return a
        second = sealed(valid_appraisal())
        second["id"] = "IMP-20260830-02"
        second["occurrence_id"] = "SIG-20260829-01"
        second["review_by"] = "2026-08-31"
        r = self.run_gate(m, extra_appraisals=[second], rank_queue=[
            {"occurrence_id": "SIG-20260829-01", "appraisal_id": "IMP-20260830-01",
             "impact_band": "LEAKY", "unmappedness": 80},
            {"occurrence_id": "SIG-20260829-01", "appraisal_id": "IMP-20260830-02",
             "impact_band": "LEAKY", "unmappedness": 80},
        ])
        self.assertEqual(r.returncode, 1)
        self.assertIn("ticker_facts", r.stdout)
        self.assertIn("as_of + 90 days", r.stdout)
        self.assertIn("2 appraisal(s)", r.stdout)

    def test_off_day_malformed_appraisal_fails(self):
        """Corpus integrity runs even when today is not an impact run day."""
        bad = sealed(valid_appraisal())
        bad["review_by"] = "2026-08-31"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for sub in ("impact", "signals", "radar", "market"):
                (root / "data" / sub).mkdir(parents=True)
            (root / "data" / "signals" / "SIG-20260829-01.json").write_text(json.dumps(
                {"id": "SIG-20260829-01", "occurrence": {"anchor_date": "2025-07-01"},
                 "unmappedness": {"score": 80}}))
            (root / "data" / "radar" / "candidates.json").write_text(
                json.dumps({"candidates": []}))
            (root / "data" / "impact" / f"{bad['id']}.json").write_text(json.dumps(bad))
            row = queue_row(bad)
            (root / "data" / "impact" / "_rank-log.json").write_text(json.dumps(
                {"as_of": "2026-08-31",
                 "calibration": {"as_of": "2026-08-30",
                                 "denominators": {"occurrences_on_disk": 1, "unappraised": 0}},
                 "queue": [row]}))
            r = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "check_impact.py"),
                 "--root", str(root), "--date", "2026-08-31"],
                capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("NOT RUN TODAY", r.stdout)
        self.assertIn("as_of + 90 days", r.stdout)

    def test_empty_queue_with_appraisal_fails(self):
        r = self.run_gate(rank_queue=[])
        self.assertEqual(r.returncode, 1)
        self.assertIn("queue is empty", r.stdout)

    def test_nested_price_usd_alias_fails(self):
        def m(a):
            a["notes"] = [{"ts": f"{TODAY}T00:00:00Z", "by": "x", "text": "probe",
                           "ticker": "ZZZ", "price_usd": 999999}]
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 1)
        self.assertIn("price_usd", r.stdout)

    def test_arbitrary_alias_with_ticker_fails(self):
        def m(a):
            a["notes"] = [{"ts": f"{TODAY}T00:00:00Z", "by": "x", "text": "probe",
                           "ticker": "ABC", "widget_count_usd": 12345.67}]
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 1)
        self.assertIn("widget_count_usd", r.stdout)

    def test_market_ticker_alias_fails(self):
        def m(a):
            a["notes"] = [{"ts": f"{TODAY}T00:00:00Z", "by": "x", "text": "probe",
                           "market_ticker": "ZZZ", "price_usd": 999999}]
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 1)
        self.assertIn("price_usd", r.stdout)

    def test_queue_prime_written_as_thin_fails(self):
        a = sealed()
        row = queue_row(a)
        row["impact_band"] = "THIN"
        r = self.run_gate(rank_queue=[row])
        self.assertEqual(r.returncode, 1)
        self.assertIn("impact_band", r.stdout)

    def test_queue_falsified_score_fails(self):
        a = sealed()
        row = queue_row(a)
        row["impact_score"] = 0.1
        r = self.run_gate(rank_queue=[row])
        self.assertEqual(r.returncode, 1)
        self.assertIn("impact_score", r.stdout)

    def test_queue_falsified_unmappedness_fails(self):
        a = sealed()
        row = queue_row(a)
        row["unmappedness"] = 0
        r = self.run_gate(rank_queue=[row])
        self.assertEqual(r.returncode, 1)
        self.assertIn("unmappedness", r.stdout)

    def test_queue_wrong_order_fails(self):
        first = sealed(valid_appraisal())
        first["id"] = "IMP-20260830-01"
        first["occurrence_id"] = "SIG-20260829-01"
        second = sealed(valid_appraisal())
        second["id"] = "IMP-20260830-02"
        second["occurrence_id"] = "SIG-20260829-02"
        second["public_reach"] = leg(20)
        c = compute(second)
        second["impact_score"], second["impact_band"] = c["impact_score"], c["impact_band"]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for sub in ("impact", "signals", "radar", "market"):
                (root / "data" / sub).mkdir(parents=True)
            write_signal(root, "SIG-20260829-01")
            write_signal(root, "SIG-20260829-02")
            (root / "data" / "radar" / "candidates.json").write_text(
                json.dumps({"candidates": []}))
            (root / "data" / "impact" / f"{first['id']}.json").write_text(json.dumps(first))
            (root / "data" / "impact" / f"{second['id']}.json").write_text(json.dumps(second))
            # Higher score first in rank_key order, but queue reverses them.
            queue = [queue_row(second), queue_row(first)]
            (root / "data" / "impact" / "_rank-log.json").write_text(json.dumps(
                {"as_of": TODAY,
                 "calibration": {"as_of": TODAY,
                                 "denominators": {"occurrences_on_disk": 2, "unappraised": 0}},
                 "queue": queue}))
            (root / "data" / "ledger.md").write_text(
                f"{TODAY} 00:00Z | IMPACT | run impact | by: ron | wrote: data/impact | "
                f"result: ranked: 2 band: PRIME | health: 2/2 | artifact: skipped(test)\n")
            r = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "check_impact.py"),
                 "--root", str(root), "--date", TODAY],
                capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("queue order", r.stdout)

    def test_queue_tie_break_uses_appraisal_id(self):
        first = sealed(valid_appraisal())
        first["id"] = "IMP-20260830-01"
        second = sealed(valid_appraisal())
        second["id"] = "IMP-20260830-02"
        second["occurrence_id"] = "SIG-20260829-02"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for sub in ("impact", "signals", "radar", "market"):
                (root / "data" / sub).mkdir(parents=True)
            write_signal(root, "SIG-20260829-01")
            write_signal(root, "SIG-20260829-02")
            (root / "data" / "radar" / "candidates.json").write_text(
                json.dumps({"candidates": []}))
            (root / "data" / "impact" / f"{first['id']}.json").write_text(json.dumps(first))
            (root / "data" / "impact" / f"{second['id']}.json").write_text(json.dumps(second))
            # Same score; rank_key tie-break is appraisal id ascending.
            queue = [queue_row(second), queue_row(first)]
            (root / "data" / "impact" / "_rank-log.json").write_text(json.dumps(
                {"as_of": TODAY,
                 "calibration": {"as_of": TODAY,
                                 "denominators": {"occurrences_on_disk": 2, "unappraised": 0}},
                 "queue": queue}))
            (root / "data" / "ledger.md").write_text(
                f"{TODAY} 00:00Z | IMPACT | run impact | by: ron | wrote: data/impact | "
                f"result: ranked: 2 band: PRIME | health: 2/2 | artifact: skipped(test)\n")
            r = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "check_impact.py"),
                 "--root", str(root), "--date", TODAY],
                capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("rank_key order", r.stdout)

    def test_undated_evidence_fails(self):
        def m(a):
            a["public_reach"]["evidence"][0].pop("source_date")
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 1)
        self.assertIn("0 dated", r.stdout)

    def test_null_leg_without_basis_fails(self):
        def m(a):
            a["public_reach"] = {"score": None}
            a["impact_score"], a["impact_band"] = None, "UNRANKED"
            a["unranked_reason"] = "public_reach: no source found"
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 1)
        self.assertIn("NULL with no basis", r.stdout)

    def test_unranked_without_reason_fails(self):
        def m(a):
            a["public_reach"] = {"score": None, "basis": "searched, nothing published"}
            a["impact_score"], a["impact_band"] = None, "UNRANKED"
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 1)
        self.assertIn("unranked_reason", r.stdout)

    def test_stale_calibration_fails(self):
        r = self.run_gate(calibrate=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("impact_calibrate", r.stdout)

    def test_missing_ledger_line_fails(self):
        r = self.run_gate(ledger=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no IMPACT ledger line", r.stdout)

    def test_gate_reports_its_denominators(self):
        """Rule 21: a count with no denominator cannot fail."""
        r = self.run_gate()
        self.assertIn("of 1 appraisal(s) examined", r.stdout)


if __name__ == "__main__":
    unittest.main()
