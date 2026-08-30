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
    BANDS, MONEY_BANDS, audit_excerpt, compute, expected_review_by, rank_key,
    scan_numerics, scan_ticker_venue,
)
from queue_allowlist import is_allowed, reject_reason  # noqa: E402

TODAY = "2026-08-30"


def leg(score, tag="INFERRED"):
    return {
        "score": score,
        "rationale": "A written rationale.",
        "evidence": [{"claim": "The programme is worth $167bn.",
                      "source_excerpt": "Officials put the programme at USD 167 billion "
                                        "over the decade, the largest award of its kind.",
                      "source_name": "Source",
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
            "evidence": [{"claim": "The pool totals about USD 30bn.",
                          "source_excerpt": "The approved plan is valued at roughly $29.7 "
                                            "billion of committed spending to 2035.",
                          "source_name": "Source",
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


# ---------------------------------------------------------------- the source_excerpt bar
#
# Fixtures reproduce fabrications confirmed by direct fetch on 2026-08-30, when adversarial
# verifiers opened every cited URL across 36 appraisals and found roughly 85% carrying at
# least one evidence item whose source does not contain the claim.

FAB_THROUGHPUT = {
    "claim": "Throughput averaged 4.9 mb/d in Q2 2026, down from 21.6 mb/d in 4Q25.",
    "source_excerpt": "The pipeline has faced repeated disruption this year, with operators "
                      "warning that repairs could take months and exports remaining well "
                      "below pre-war levels.",
    "source_name": "CNN", "source_date": "2026-08-01",
    "url": "https://example.com/cnn", "tag": "INFERRED",
}
FAB_VALUATION = {
    "claim": "The transaction values the unit at EUR 4 billion.",
    "source_excerpt": "The parties have agreed not to disclose the financial terms of the "
                      "transaction, which remains subject to regulatory approval.",
    "source_name": "Company press release", "source_date": "2026-08-01",
    "url": "https://example.com/pr", "tag": "INFERRED",
}
FAB_QUEUE = {
    "claim": "There are 200 gigawatts of pending interconnection requests.",
    "source_excerpt": "More than 474 GW of generation and storage capacity is sitting in "
                      "the interconnection queue nationwide.",
    "source_name": "Trade press", "source_date": "2026-08-01",
    "url": "https://example.com/gw", "tag": "INFERRED",
}
FAB_WAIT = {
    "claim": "Wait times have run as long as 19 days.",
    "source_excerpt": "At the worst point the backlog pushed waits out to a maximum of "
                      "8 days before clearing.",
    "source_name": "Trade press", "source_date": "2026-08-01",
    "url": "https://example.com/wait", "tag": "INFERRED",
}


class TestExcerptMatcher(unittest.TestCase):
    """The tolerance table. Generous about FORM, strict about DIGITS."""

    def assertClean(self, item):
        r = audit_excerpt(item)
        self.assertEqual(r["findings"], [], f"false failure on {item['claim']!r}")

    def assertRefused(self, item):
        r = audit_excerpt(item)
        self.assertTrue(r["findings"], f"accepted a fabrication: {item['claim']!r}")
        return r

    def test_formatting_variations_must_never_fail(self):
        """The false-failure direction is the dangerous one: a verifier that rejects true
        quotes gets switched off, and then nothing is verified at all."""
        for claim, excerpt in [
            ("Throughput averaged 4.9 mb/d over the quarter.",
             "Average throughput over the period was 4.90 million barrels per day."),
            ("The fleet moved 21.6 million tonnes.",
             "Cargo volumes on the route reached 21,600,000 tonnes over the year."),
            ("The fleet moved 21,600,000 tonnes of cargo.",
             "Cargo volumes on the route reached 21.6 million tonnes over the year."),
            ("The programme is worth $167bn.",
             "The programme carries a headline value of USD 167 billion through 2032."),
            ("The programme is worth 167 billion dollars.",
             "Officials put the programme at $167bn over the coming decade."),
            ("The plan totals about USD 30bn.",
             "The approved plan is valued at roughly $29.7 billion to 2035."),
            ("Spending grew roughly 700 percent between FY23 and FY26.",
             "European counter-UAS spending rose about 700% across that four-year period."),
            ("Davie will build 6 icebreakers.",
             "Davie has been contracted to build six icebreakers for the Coast Guard."),
            ("NATO announced the package on 2026-07-07 in Ankara.",
             "Meeting in Ankara on July 7, 2026, Allies agreed the counter-drone package."),
            ("The corridor delivers 8GW over 2,681km.",
             "The ultra-high voltage corridor is rated at 8 GW and spans 2681 km."),
            ("The index fell -0.25% on the session.",
             "The benchmark closed down 0.25 percent on the day, a third straight decline."),
        ]:
            self.assertClean({"claim": claim, "source_excerpt": excerpt})

    def test_a_list_of_spans_is_a_valid_excerpt(self):
        """A claim honestly resting on two sentences of one page quotes both."""
        self.assertClean({
            "claim": "Ottawa awarded $8bn for six icebreakers.",
            "source_excerpt": ["The contract is valued at USD 8 billion, fixed price.",
                               "Davie will deliver six vessels to the Coast Guard."]})

    def test_fabricated_throughput_is_refused(self):
        r = self.assertRefused(FAB_THROUGHPUT)
        self.assertIn("4.9", r["unmatched"])
        self.assertIn("21.6", r["unmatched"])

    def test_confidential_terms_cannot_support_a_valuation(self):
        self.assertIn("4", self.assertRefused(FAB_VALUATION)["unmatched"])

    def test_wrong_order_of_magnitude_is_refused(self):
        self.assertIn("200", self.assertRefused(FAB_QUEUE)["unmatched"])

    def test_wrong_small_integer_is_refused(self):
        self.assertIn("19", self.assertRefused(FAB_WAIT)["unmatched"])

    def test_a_misdated_claim_is_refused_when_the_excerpt_carries_a_date(self):
        self.assertRefused({
            "claim": "The rule was published on 2026-07-14.",
            "source_excerpt": "The final rule was published in the Federal Register on "
                              "February 14, 2026 and takes effect in 60 days."})

    def test_excerpt_identical_to_the_claim_is_refused(self):
        r = self.assertRefused({"claim": "The programme is worth $167bn over the decade.",
                                "source_excerpt": "The programme is worth $167bn over the "
                                                  "decade."})
        self.assertIn("byte-identical", r["findings"][0])

    def test_identifier_tokens_are_reported_not_checked(self):
        """The named blind spot: a ticker symbol or product code is a name, not a quantity.
        `1SXP` cited to a release naming only an ISIN is NOT deterministically catchable
        here, and the gate says so in its denominator rather than implying coverage."""
        r = audit_excerpt({
            "claim": "The shares trade under the ticker 1SXP.",
            "source_excerpt": "The instrument is admitted to trading under ISIN "
                              "SE0021309087 as of the listing date."})
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["identifiers"], 1)
        self.assertEqual(r["numerics"], 0)
        self.assertIn("1SXP", scan_numerics("ticker 1SXP")["identifiers"])


class TestDerivedFrom(unittest.TestCase):
    EXCERPT = ("Washington awarded $8bn, Ottawa $6.8bn and Brussels $23.8bn across the "
               "three programmes announced this year.")

    def test_a_derived_figure_with_no_declaration_is_refused(self):
        r = audit_excerpt({"claim": "The three awards total 38.6 billion dollars.",
                           "source_excerpt": self.EXCERPT})
        self.assertIn("38.6", r["unmatched"])

    def test_a_correctly_declared_derivation_passes(self):
        r = audit_excerpt({
            "claim": "The three awards total 38.6 billion dollars.",
            "source_excerpt": self.EXCERPT,
            "derived_from": [{"value": "38.6", "from": ["8", "6.8", "23.8"],
                              "how": "sum of the three signed awards"}]})
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["derived"], 1)

    def test_a_derivation_whose_inputs_are_not_quoted_is_refused(self):
        """The escape buys a computation, never an unsourced number."""
        r = audit_excerpt({
            "claim": "The three awards total 38.6 billion dollars.",
            "source_excerpt": "Washington awarded $8bn and Ottawa $6.8bn across the two "
                              "programmes announced this year.",
            "derived_from": [{"value": "38.6", "from": ["8", "6.8", "23.8"],
                              "how": "sum"}]})
        self.assertTrue(any("only as sourced as its inputs" in f for f in r["findings"]))

    def test_a_derivation_of_a_figure_the_claim_never_states_exempts_nothing(self):
        r = audit_excerpt({
            "claim": "The three awards total 38.6 billion dollars.",
            "source_excerpt": self.EXCERPT,
            "derived_from": [{"value": "99.9", "from": ["8"], "how": "sum"}]})
        self.assertTrue(any("exempts nothing" in f for f in r["findings"]))
        self.assertIn("38.6", r["unmatched"])

    def test_a_malformed_declaration_is_refused(self):
        r = audit_excerpt({"claim": "The three awards total 38.6 billion dollars.",
                           "source_excerpt": self.EXCERPT,
                           "derived_from": [{"value": "38.6"}]})
        self.assertTrue(any("needs value" in f for f in r["findings"]))


class TestExcerptGate(unittest.TestCase):
    """The matcher above as an exit code. A gate nobody has watched refuse is not a gate.

    The harness is borrowed rather than inherited: subclassing TestGate would re-run its
    whole suite under a second name, which inflates the count without testing anything new.
    """

    run_gate = TestGate.run_gate

    def test_missing_source_excerpt_fails(self):
        def m(a):
            a["public_reach"]["evidence"][0].pop("source_excerpt")
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("carries no source_excerpt", r.stdout)

    def test_claim_numeric_absent_from_the_excerpt_fails(self):
        def m(a):
            a["public_reach"]["evidence"][0] = dict(FAB_QUEUE)
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("['200']", r.stdout)

    def test_fabricated_throughput_figures_fail_the_gate(self):
        def m(a):
            a["capture_odds"]["evidence"][0] = dict(FAB_THROUGHPUT)
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("does not contain", r.stdout)

    def test_confidential_valuation_fails_the_gate(self):
        def m(a):
            a["money_at_stake"]["evidence"][0] = dict(FAB_VALUATION)
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("does not contain", r.stdout)

    def test_derived_numeric_with_no_derived_from_fails_the_gate(self):
        def m(a):
            a["timing_fit"]["evidence"][0] = {
                "claim": "The three awards total 38.6 billion dollars.",
                "source_excerpt": TestDerivedFrom.EXCERPT,
                "source_name": "Source", "source_date": "2026-08-01",
                "url": "https://example.com/d", "tag": "INFERRED"}
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("derived_from", r.stdout)

    def test_a_declared_derivation_passes_the_gate(self):
        def m(a):
            a["timing_fit"]["evidence"][0] = {
                "claim": "The three awards total 38.6 billion dollars.",
                "source_excerpt": TestDerivedFrom.EXCERPT,
                "derived_from": [{"value": "38.6", "from": ["8", "6.8", "23.8"],
                                  "how": "sum of the three signed awards"}],
                "source_name": "Source", "source_date": "2026-08-01",
                "url": "https://example.com/d", "tag": "INFERRED"}
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_null_leg_needs_no_excerpts(self):
        """A NULL leg records what was looked for. There is nothing to quote."""
        def m(a):
            a["public_reach"] = {"score": None, "basis": "searched, nothing published"}
            a["impact_score"], a["impact_band"] = None, "UNRANKED"
            a["unranked_reason"] = "public_reach: no published figure"
            return a
        r = self.run_gate(m)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_gate_reports_its_excerpt_denominators(self):
        """Rule 21. A count of zero unmatched numerics is not evidence of health unless the
        line says how many items and how many numerics it actually looked at."""
        r = self.run_gate()
        self.assertIn("evidence item(s) on scored legs examined", r.stdout)
        self.assertIn("claim numeric(s) cross-checked", r.stdout)
        self.assertIn("identifier token(s) not numerically checkable", r.stdout)


class TestLegacyExemption(unittest.TestCase):
    """The one pre-bar appraisal is exempt by exact committed content, visibly and dated."""

    LEGACY = ROOT / "data" / "impact" / "IMP-20260830-01.json"

    def run_over_legacy(self, mutate=None):
        payload = json.loads(self.LEGACY.read_text())
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for sub in ("impact", "signals", "radar", "market"):
                (root / "data" / sub).mkdir(parents=True)
            write_signal(root, "SIG-20260829-01")
            (root / "data" / "radar" / "candidates.json").write_text(
                json.dumps({"candidates": []}))
            target = root / "data" / "impact" / "IMP-20260830-01.json"
            if mutate:
                target.write_text(json.dumps(mutate(payload), indent=2))
            else:
                target.write_bytes(self.LEGACY.read_bytes())
            (root / "data" / "impact" / "_rank-log.json").write_text(json.dumps(
                {"as_of": TODAY,
                 "calibration": {"as_of": TODAY,
                                 "denominators": {"occurrences_on_disk": 1,
                                                  "unappraised": 0}},
                 "queue": [queue_row(payload)]}))
            (root / "data" / "ledger.md").write_text("")
            return subprocess.run(
                [sys.executable, str(ROOT / "tools" / "check_impact.py"),
                 "--root", str(root), "--date", "2026-09-15"],
                capture_output=True, text=True)

    def test_the_committed_legacy_appraisal_is_exempt_and_says_so_every_run(self):
        r = self.run_over_legacy()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WARNING IMP-20260830-01.json", r.stdout)
        self.assertIn("owed a repair", r.stdout)

    def test_amending_the_legacy_appraisal_drops_its_exemption(self):
        """An exemption bound to bytes cannot be inherited by an edited file, and cannot be
        claimed by a new file backdated into the same name."""
        def touch(payload):
            payload["notes"].append({"ts": f"{TODAY}T12:00:00Z", "by": "tally-appraiser",
                                     "text": "an amendment"})
            return payload
        r = self.run_over_legacy(touch)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("carries no source_excerpt", r.stdout)


if __name__ == "__main__":
    unittest.main()
