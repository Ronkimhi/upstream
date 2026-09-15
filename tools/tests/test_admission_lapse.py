#!/usr/bin/env python3
"""admission_lapse (method section 7, Ron's decision 2026-09-14), watched refusing.

The motivating case: data/stocks/WSP.TO__data-center-moratoria.json is FINAL INVESTABLE on
the WSP-GLOBAL placement for permitting-and-environmental-consultancy, and a fresh audit is
about to FAIL that placement because no readable source states WSP's data-center permitting
role. Ron's call: downgrade to WATCH, keep the dive visible naming the missing evidence,
let it return to INVESTABLE when a source proves the role. `tools/check_analyst.py` had no
way to represent "this FINAL dive's admission used to resolve and no longer does" — it is
either failed outright or silently exempt, and both are wrong. `admission_lapse` is the
third state.

These tests cover, in order: the object's own shape (admission_lapse_failures), the
gating decision (admission_lapse_outcome), the real gate end to end (StockyAdmissionTree,
imported rather than re-built), the untouched corpus (the 16 committed dives carry no
admission_lapse and the mechanism is provably a no-op for every one of them), and the three
other gates named in the build task (check_campaign.py, check_profile.py, check_screen.py),
each pinned against the real functions to show why no code change was needed there.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_analyst  # noqa: E402
import check_campaign  # noqa: E402
import check_profile  # noqa: E402
from test_stocky_admission import StockyAdmissionTree  # noqa: E402


def lapse(**over):
    d = {
        "date": "2026-09-14",
        "reason": ("no readable source states WSP's data-center permitting role for this "
                   "placement"),
        "audit_ref": "data/mappings/data-center-moratoria.json placement_audits for WSP-GLOBAL",
        "prior_verdict": "INVESTABLE",
        "restore_when": "a primary source documents WSP's data-center permitting role",
    }
    d.update(over)
    return d


# ---------------------------------------------------------------------------
# 1. admission_lapse_failures: the object's own shape, independent of the dive it sits on.
# ---------------------------------------------------------------------------
class AdmissionLapseShape(unittest.TestCase):
    def test_well_formed_lapse_has_no_findings(self):
        self.assertEqual(check_analyst.admission_lapse_failures(lapse()), [])

    def test_not_an_object(self):
        self.assertEqual(
            check_analyst.admission_lapse_failures("a string"),
            ["admission_lapse must be an object"])

    def test_a_list_is_not_an_object(self):
        self.assertEqual(
            check_analyst.admission_lapse_failures([]),
            ["admission_lapse must be an object"])

    def test_missing_fields_are_named(self):
        findings = check_analyst.admission_lapse_failures({"date": "2026-09-14"})
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("audit_ref", findings[0])
        self.assertIn("reason", findings[0])
        self.assertIn("prior_verdict", findings[0])
        self.assertIn("restore_when", findings[0])

    def test_bad_date(self):
        findings = check_analyst.admission_lapse_failures(lapse(date="09/14/2026"))
        self.assertTrue(any("date" in f and "YYYY-MM-DD" in f for f in findings), findings)

    def test_token_reason_is_not_substantive(self):
        findings = check_analyst.admission_lapse_failures(lapse(reason="looks good to me"))
        self.assertTrue(any("reason must be substantive" in f for f in findings), findings)

    def test_empty_reason(self):
        findings = check_analyst.admission_lapse_failures(lapse(reason="  "))
        self.assertTrue(any("reason must be substantive" in f for f in findings), findings)

    def test_empty_audit_ref(self):
        findings = check_analyst.admission_lapse_failures(lapse(audit_ref="  "))
        self.assertTrue(any("audit_ref must name" in f for f in findings), findings)

    def test_bad_prior_verdict(self):
        findings = check_analyst.admission_lapse_failures(lapse(prior_verdict="MAYBE"))
        self.assertTrue(any("prior_verdict" in f for f in findings), findings)

    def test_non_substantive_restore_when(self):
        findings = check_analyst.admission_lapse_failures(lapse(restore_when="soon"))
        self.assertTrue(any("restore_when must be substantive" in f for f in findings), findings)


# ---------------------------------------------------------------------------
# 2. admission_lapse_outcome: the gating decision, as a pure function.
# ---------------------------------------------------------------------------
class AdmissionLapseOutcome(unittest.TestCase):
    def test_no_lapse_is_unchanged_behaviour(self):
        """A dive with neither admission nor a lapse fails exactly as today: every
        admission finding becomes a fail message, verbatim, and nothing is reported."""
        fail_msgs, lapse_msgs = check_analyst.admission_lapse_outcome(
            {"status": "FINAL", "verdict": "WATCH"}, ["boom"])
        self.assertEqual(fail_msgs, ["boom"])
        self.assertEqual(lapse_msgs, [])

    def test_eligible_lapse_moves_findings_to_reported(self):
        """A well-formed lapse on a FINAL WATCH dive: not failed on admission findings,
        which are reported as a lapse instead."""
        d = {"status": "FINAL", "verdict": "WATCH", "admission_lapse": lapse()}
        fail_msgs, lapse_msgs = check_analyst.admission_lapse_outcome(d, ["boom"])
        self.assertEqual(fail_msgs, [])
        self.assertEqual(lapse_msgs, ["boom"])

    def test_stale_lapse_over_clean_admission_fails(self):
        """Keep the check honest: an admission that actually passes while a lapse is still
        present is a finding ('stale lapse: remove it')."""
        d = {"status": "FINAL", "verdict": "WATCH", "admission_lapse": lapse()}
        fail_msgs, lapse_msgs = check_analyst.admission_lapse_outcome(d, [])
        self.assertEqual(lapse_msgs, [])
        self.assertEqual(len(fail_msgs), 1, fail_msgs)
        self.assertIn("stale lapse", fail_msgs[0])

    def test_lapse_on_investable_fails_and_keeps_the_admission_findings(self):
        """A lapse on a verdict other than WATCH fails."""
        d = {"status": "FINAL", "verdict": "INVESTABLE", "admission_lapse": lapse()}
        fail_msgs, lapse_msgs = check_analyst.admission_lapse_outcome(d, ["boom"])
        self.assertEqual(lapse_msgs, [])
        self.assertTrue(any("only valid on verdict WATCH" in f for f in fail_msgs), fail_msgs)
        self.assertIn("boom", fail_msgs)

    def test_lapse_on_too_late_fails(self):
        d = {"status": "FINAL", "verdict": "TOO_LATE", "admission_lapse": lapse()}
        fail_msgs, lapse_msgs = check_analyst.admission_lapse_outcome(d, [])
        self.assertEqual(lapse_msgs, [])
        self.assertTrue(any("only valid on verdict WATCH" in f for f in fail_msgs), fail_msgs)

    def test_lapse_on_draft_fails(self):
        d = {"status": "DRAFT", "verdict": "WATCH", "admission_lapse": lapse()}
        fail_msgs, lapse_msgs = check_analyst.admission_lapse_outcome(d, ["boom"])
        self.assertEqual(lapse_msgs, [])
        self.assertTrue(any("requires status FINAL" in f for f in fail_msgs), fail_msgs)
        self.assertIn("boom", fail_msgs)

    def test_malformed_lapse_fails_and_keeps_the_admission_findings(self):
        d = {"status": "FINAL", "verdict": "WATCH",
             "admission_lapse": {"date": "2026-09-14"}}
        fail_msgs, lapse_msgs = check_analyst.admission_lapse_outcome(d, ["boom"])
        self.assertEqual(lapse_msgs, [])
        self.assertTrue(any("missing" in f for f in fail_msgs), fail_msgs)
        self.assertIn("boom", fail_msgs)


# ---------------------------------------------------------------------------
# 3. The real gate, end to end, over the WSP-GLOBAL shape: a FINAL dive whose mapping
# placement audit has just FAILed (the fixture's mapping starts COMPLETE+PASS in
# StockyAdmissionTree; this subclass breaks exactly that, and nothing else).
# ---------------------------------------------------------------------------
class AdmissionLapseTree(StockyAdmissionTree):
    def setUp(self):
        super().setUp()
        self.mapping["status"] = "ACTIVE"
        self.attach_audit(self.mapping, status="FAIL")
        self._write_upstream()

    def lapsed_stock(self, **over):
        d = dict(self.stock)
        d.update({
            "verdict": "WATCH",
            "status": "FINAL",
            "admission_lapse": lapse(),
        })
        d.update(over)
        return d


class TestAdmissionLapseGateEndToEnd(AdmissionLapseTree):
    def test_the_fixture_really_does_fail_admission_without_a_lapse(self):
        """Sanity: prove the break is real before trusting any test that rests on it."""
        findings = self.findings()
        self.assertTrue(
            any("mapping status COMPLETE" in f for f in findings), findings)

    def test_lapsed_watch_dive_passes(self):
        result = self.run_gate(self.lapsed_stock(), date="2026-08-31")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 lapse(s) reported", result.stdout)
        self.assertIn("LAPSE", result.stdout)
        # reported, not hidden: the excused admission finding still prints.
        self.assertIn("mapping status COMPLETE", result.stdout)

    def test_lapsed_investable_dive_fails(self):
        result = self.run_gate(self.lapsed_stock(verdict="INVESTABLE"), date="2026-08-31")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("only valid on verdict WATCH", result.stdout)

    def test_lapsed_draft_dive_fails(self):
        result = self.run_gate(self.lapsed_stock(status="DRAFT"), date="2026-08-31")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("requires status FINAL", result.stdout)

    def test_malformed_lapse_fails(self):
        stock = self.lapsed_stock()
        del stock["admission_lapse"]["restore_when"]
        stock["admission_lapse"]["date"] = "not-a-date"
        result = self.run_gate(stock, date="2026-08-31")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("admission_lapse missing restore_when", result.stdout)
        self.assertIn("admission_lapse.date", result.stdout)

    def test_stale_lapse_on_a_now_passing_admission_fails(self):
        """The placement audit gets re-run and PASSes; the lapse was never cleared."""
        self.mapping["status"] = "COMPLETE"
        self.attach_audit(self.mapping, status="PASS")
        self._write_upstream()
        result = self.run_gate(self.lapsed_stock(), date="2026-08-31")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("stale lapse", result.stdout)


# ---------------------------------------------------------------------------
# 4. The committed dives keep their outcomes. When the mechanism landed no dive carried
# admission_lapse, and these tests pinned that absence. On 2026-09-14 Ron downgraded
# WSP.TO__data-center-moratoria to WATCH through it, the first real lapse, which turned CI
# red on a corpus that was behaving exactly as designed. They now pin the rule on both
# kinds of dive instead: a no-op for every dive without a lapse, and for every dive with
# one a well-formed lapse on a FINAL WATCH dive that turns its admission findings into
# reported lapses.
# ---------------------------------------------------------------------------
class ExistingCorpusUnaffected(unittest.TestCase):
    @staticmethod
    def _dives():
        folder = ROOT / "data" / "stocks"
        return [p for p in sorted(folder.glob("*.json")) if not p.name.startswith("_")]

    def _real_dives(self):
        for p in self._dives():
            d = json.loads(p.read_text())
            if not d.get("fixture"):
                yield p, d

    def test_at_least_sixteen_dives_on_disk(self):
        names = [p.name for p in self._dives()]
        self.assertGreaterEqual(len(names), 16, names)

    def test_every_committed_lapse_is_well_formed_on_a_final_watch_dive(self):
        for p, d in self._real_dives():
            if "admission_lapse" not in d:
                continue
            with self.subTest(dive=p.name):
                self.assertEqual(d.get("status"), "FINAL")
                self.assertEqual(d.get("verdict"), "WATCH")
                self.assertEqual(check_analyst.admission_lapse_failures(d["admission_lapse"]), [])

    def test_lapse_outcome_is_a_no_op_for_every_dive_without_a_lapse(self):
        for p, d in self._real_dives():
            if "admission_lapse" in d:
                continue
            with self.subTest(dive=p.name):
                admission_findings = check_analyst.stock_admission_failures(ROOT, d)
                fail_msgs, lapse_msgs = check_analyst.admission_lapse_outcome(
                    d, admission_findings)
                self.assertEqual(fail_msgs, admission_findings)
                self.assertEqual(lapse_msgs, [])

    def test_a_committed_lapse_reports_its_admission_findings_instead_of_failing(self):
        for p, d in self._real_dives():
            if "admission_lapse" not in d:
                continue
            with self.subTest(dive=p.name):
                admission_findings = check_analyst.stock_admission_failures(ROOT, d)
                fail_msgs, lapse_msgs = check_analyst.admission_lapse_outcome(
                    d, admission_findings)
                # Empty findings would mean the gap closed and the lapse is stale, which
                # the gate fails; a live lapse always has findings to report.
                self.assertTrue(admission_findings)
                self.assertEqual(fail_msgs, [])
                self.assertEqual(lapse_msgs, admission_findings)

    def test_gate_reports_one_lapse_per_lapsed_dive_over_the_real_tree(self):
        """A real subprocess run, date-independent (a far past date touches nothing), so
        this does not depend on today's ledger lines the way a same-day check would."""
        expected = sum(1 for _, d in self._real_dives() if "admission_lapse" in d)
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "check_analyst.py"),
             "--date", "1999-01-01"],
            cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertIn("%d lapse(s) reported" % expected, result.stdout,
                      result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# 5. check_campaign.py: no code change needed. A lapsed dive counts toward O1 FINAL
# coverage exactly like any other FINAL verdict UNTIL its profile is demoted from O1 to
# O2, which check_campaign.py already treats as removing it from every O1 count -- the
# lapse itself does nothing there, the demotion (named in the same landing, method section
# 7) does. Pinned against the real compute_campaign_completion, not re-derived.
# ---------------------------------------------------------------------------
class CheckCampaignToleratesTheLapsedShape(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for folder in ("companies", "mappings", "screens", "stocks", "chains"):
            (self.root / "data" / folder).mkdir(parents=True, exist_ok=True)
        self.mapping = {
            "chain_id": "theme-a",
            "issuers": [{"issuer_id": "ISS-A", "name": "Issuer A"}],
            "listings": [{
                "listing_id": "TSX-AAA", "issuer_id": "ISS-A", "ticker": "AAA",
                "exchange": "TSX", "market_ticker": "AAA.TO",
            }],
            "placements": [{
                "chain_id": "theme-a", "link_id": "L1", "issuer_id": "ISS-A",
                "status": "ACTIVE",
            }],
        }
        self.screen = {
            "id": "theme-a", "chain_id": "theme-a",
            "buckets": {"pure_play": [{
                "issuer_id": "ISS-A", "listing_id": "TSX-AAA", "link_id": "L1",
                "ticker": "AAA", "market_ticker": "AAA.TO",
            }]},
        }
        self.profile = {
            "issuer_id": "ISS-A", "status": "COMPLETE", "opportunity_tier": "O1",
            "listing_refs": ["TSX-AAA"],
            "placements": [{"chain_id": "theme-a", "link_id": "L1"}],
            "selection_basis": {"screen_handoff": {
                "screen_ref": "theme-a", "chain_id": "theme-a", "link_id": "L1",
                "listing_id": "TSX-AAA",
            }},
        }
        self.stock = {
            "status": "FINAL", "verdict": "WATCH", "issuer_id": "ISS-A",
            "listing_id": "TSX-AAA", "chain_id": "theme-a", "link_id": "L1",
            "ticker": "AAA.TO", "admission_lapse": lapse(),
        }
        self.campaign = {"themes": [{"theme_id": "T1", "chain_id": "theme-a"}]}
        self._write()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self):
        (self.root / "data" / "mappings" / "theme-a.json").write_text(
            json.dumps(self.mapping))
        (self.root / "data" / "screens" / "theme-a.json").write_text(
            json.dumps(self.screen))
        (self.root / "data" / "companies" / "ISS-A.json").write_text(
            json.dumps(self.profile))
        (self.root / "data" / "stocks" / "AAA.TO__theme-a.json").write_text(
            json.dumps(self.stock))

    def test_o1_lapsed_watch_dive_counts_as_o1_final(self):
        """A lapse alone does not remove coverage: a FINAL WATCH dive on a still-O1
        profile counts exactly as any other FINAL verdict would."""
        computed = check_campaign.compute_campaign_completion(self.root, self.campaign)
        self.assertEqual(computed["opportunity_tiers"]["O1"], 1)
        self.assertEqual(computed["o1_final"], 1)

    def test_demoting_to_o2_drops_the_lapsed_dive_from_o1_final(self):
        """The demotion CLAUDE.md's file-ownership contract expects in the same landing
        ('profile demoted from O1 to O2') is what removes campaign coverage. No change is
        needed in check_campaign.py: the O1 count and the O1-FINAL count are both computed
        from opportunity_tier alone, so a profile that is no longer O1 was never counted
        by them to begin with."""
        self.profile["opportunity_tier"] = "O2"
        self._write()
        computed = check_campaign.compute_campaign_completion(self.root, self.campaign)
        self.assertEqual(computed["opportunity_tiers"]["O1"], 0)
        self.assertEqual(computed["o1_final"], 0)
        self.assertEqual(computed["completed_profiles"], 1, "still counted as O2")

    def test_removing_the_screen_row_alone_also_drops_o1_final(self):
        """Independently sufficient: even if the profile stayed O1, a removed screen row
        breaks _profile_handoff (no resolving row), and the dive stops counting."""
        self.screen["buckets"]["pure_play"] = []
        self._write()
        computed = check_campaign.compute_campaign_completion(self.root, self.campaign)
        self.assertEqual(computed["opportunity_tiers"]["O1"], 1)
        self.assertEqual(computed["o1_final"], 0)


# ---------------------------------------------------------------------------
# 6. check_profile.py: no code change needed. The O1-only selection_basis.screen_handoff
# checks in validate_profile apply only while opportunity_tier is O1; once a profile is
# demoted to O2 a dangling screen_handoff left over from before the lapse is never
# inspected.
# ---------------------------------------------------------------------------
class CheckProfileToleratesO2DemotionWithADanglingHandoff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data" / "mappings").mkdir(parents=True)
        (self.root / "data" / "companies").mkdir(parents=True)
        # No data/screens/theme-a.json at all: the row the O1 handoff used to resolve
        # against is gone, exactly as it will be once the WSP-GLOBAL screen row is removed.
        (self.root / "data" / "mappings" / "theme-a.json").write_text(json.dumps({
            "chain_id": "theme-a",
            "issuers": [{"issuer_id": "ISS-A", "name": "Issuer A"}],
            "listings": [{
                "listing_id": "TSX-AAA", "issuer_id": "ISS-A", "ticker": "AAA",
                "exchange": "TSX",
            }],
            "placements": [
                {"chain_id": "theme-a", "link_id": "L1", "issuer_id": "ISS-A"}
            ],
        }))

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _metric(value):
        return {"value": value, "tag": "VERIFIED", "source_name": "Issuer annual report",
                "source_date": "2026-08-30", "url": "https://example.com/annual-report"}

    def _profile(self, **over):
        p = {
            "issuer_id": "ISS-A", "issuer_name": "Issuer A", "as_of": "2026-09-14",
            "status": "COMPLETE", "data_tier": "T3", "opportunity_tier": "O2",
            "listing_refs": ["TSX-AAA"],
            "placements": [{"chain_id": "theme-a", "link_id": "L1"}],
            "business_summary": "Specialist supplier", "exposure_summary": "Direct exposure",
            "metrics": {
                "revenue": {"latest_fy": self._metric(125.0)},
                "growth": {"revenue_cagr_3y": self._metric(0.2)},
                "margins": {"operating_margin": self._metric(0.15)},
                "cash_conversion": {"fcf_margin": self._metric(0.12)},
                "leverage": {"net_debt_to_ebitda": self._metric(1.1)},
                "quality": {"piotroski": self._metric(7),
                            "beneish_state": self._metric(-2.4)},
                "valuation": {"market_cap": self._metric(1_000.0),
                             "price_to_earnings": self._metric(20.0)},
                "reverse_dcf": {"implied_fcf_cagr": self._metric(0.18),
                                "horizon_spread": self._metric(0.05)},
            },
            "crowdedness_caveats": ["Coverage differs by listing"],
            "catalysts": ["Capacity commissioning"],
            "risks": ["Customer concentration"],
            "data_gaps": ["No quarterly segment margin"],
            "disposition": {"state": "RETAIN",
                            "basis": "Demoted after the placement audit lapsed"},
            # Stale, left over from when this was O1. It must not be what fails the
            # profile now: check_profile.py only inspects selection_basis for O1.
            "selection_basis": {"screen_handoff": {
                "screen_ref": "theme-a", "chain_id": "theme-a", "link_id": "L1",
                "listing_id": "TSX-AAA",
            }},
            "confidence_audit": {"verified": 9}, "changelog": [],
        }
        p.update(over)
        return p

    def test_o2_profile_with_a_dangling_selection_basis_still_validates(self):
        profile = self._profile()
        findings = check_profile.validate_profile(
            self.root, self.root / "data" / "companies" / "ISS-A.json", profile)
        self.assertEqual(findings, [], findings)

    def test_the_same_dangling_handoff_would_fail_while_still_o1(self):
        """Confirms the O2 pass above is the demotion doing the work, not a gate that
        never checked screen_handoff resolution to begin with."""
        profile = self._profile(opportunity_tier="O1")
        findings = check_profile.validate_profile(
            self.root, self.root / "data" / "companies" / "ISS-A.json", profile)
        self.assertTrue(
            any("screen_ref" in f and "does not resolve" in f for f in findings), findings)


# ---------------------------------------------------------------------------
# 7. check_screen.py: no code change needed. It carries no append-only or preservation
# rule over screen rows (unlike check_campaign.py and check_profile.py, which both guard
# other fields against regression versus git HEAD); it validates only what is currently on
# disk, so a row's removal is never itself a finding.
# ---------------------------------------------------------------------------
class CheckScreenToleratesARemovedRow(unittest.TestCase):
    def test_gate_passes_over_a_screen_whose_row_was_removed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for folder in ("mappings", "screens", "companies", "edgar/docs"):
            (root / "data" / folder).mkdir(parents=True, exist_ok=True)
        (root / "data" / "mappings" / "theme-a.json").write_text(
            json.dumps({"chain_id": "theme-a", "listings": [], "placements": []}))
        (root / "data" / "screens" / "theme-a.json").write_text(json.dumps({
            "id": "theme-a", "chain_id": "theme-a", "as_of": "2026-08-30",
            "identity_schema": "campaign-v1",
            "buckets": {"pure_play": []},
        }))
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "check_screen.py"),
             "--root", str(root), "--date", "1999-01-01"],
            cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
