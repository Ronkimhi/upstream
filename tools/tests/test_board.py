#!/usr/bin/env python3
"""Pressure tests for the campaign board, the orchestrator's resume point.

The board's whole value is that a session which lost its context can trust it. Three
ways that trust breaks, one test class each:

  * it reports a stage the gate disagrees with, so a session runs a stage
    `tools/check_campaign.py` then refuses;
  * it prints a command the click queue would reject, so a Run button is a dead end;
  * it reports a clean board over an empty tree, which is the failure mode
    `tools/check_machine.py` calls SCOPE EMPTY and refuses to call a pass.

Plus the read-only contract: nothing on disk changes unless `--write` is passed.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import campaign_board  # noqa: E402
import check_campaign  # noqa: E402
import check_map  # noqa: E402
from queue_allowlist import is_allowed  # noqa: E402


class Tree:
    """A minimal but real Upstream tree: the board reads disk, so the test writes disk."""

    def __init__(self, root):
        self.root = Path(root)
        self.data = self.root / "data"
        for folder in ("campaigns", "chains", "mappings", "companies",
                       "screens", "stocks", "market", "signals", "health"):
            (self.data / folder).mkdir(parents=True, exist_ok=True)
        (self.root / "CLAUDE.md").write_text("# fixture\n")
        (self.root / "docs").mkdir(exist_ok=True)
        (self.root / "docs" / "method.md").write_text("# fixture\n")
        (self.data / "requests.json").write_text(
            json.dumps({"version": 1, "requests": []}))

    # -- writers ---------------------------------------------------------------
    def write(self, rel, obj):
        path = self.data / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj))
        return path

    def signal(self, signal_id="SIG-20260830-01"):
        self.write(f"signals/{signal_id}.json", {"id": signal_id, "title": "Fixture"})
        return signal_id

    def chain(self, chain_id, *, links=("l-one", "l-two"), heat=False,
              scenarios=False, signal_id="SIG-20260830-01"):
        self.write(f"chains/{chain_id}.json", {
            "id": chain_id,
            "signal_id": signal_id,
            "title": f"Chain {chain_id}",
            "links": [
                {
                    "id": link_id, "name": link_id, "position": i + 1,
                    "bottleneck": {"criticality": "HIGH"},
                    **({"heat": {"verdict": "UNDISCOVERED", "money_corner": i == 0}}
                       if heat else {}),
                }
                for i, link_id in enumerate(links)
            ],
            **({"scenarios": [{"id": "S1", "title": "S1"}]} if scenarios else {}),
        })

    @staticmethod
    def listing(issuer_id, listing_id, ticker, exchange="XNYS"):
        return {
            "listing_id": listing_id, "issuer_id": issuer_id,
            "ticker": ticker, "exchange": exchange,
            "identity_evidence": [{
                "claim": f"{issuer_id} lists as {exchange}:{ticker}",
                "tag": "VERIFIED", "source_name": f"{exchange} directory",
                "source_date": "2026-08-30",
                "url": "https://example.invalid/listing",
                "source_type": "OFFICIAL_EXCHANGE",
                "legal_issuer": issuer_id, "exchange": exchange, "ticker": ticker,
            }],
        }

    def mapping(self, chain_id, *, issuers, status="COMPLETE", audit="PASS",
                fingerprint="current", link_id="l-one"):
        doc = {
            "id": f"MAP-{chain_id}", "chain_id": chain_id, "as_of": "2026-08-30",
            "status": status, "target_issuers_per_link": 10,
            "issuers": [{"issuer_id": iid, "name": iid} for iid, _ in issuers],
            "listings": [self.listing(iid, f"XNYS:{tk}", tk) for iid, tk in issuers],
            "placements": [
                {"chain_id": chain_id, "link_id": link_id, "issuer_id": iid,
                 "status": "ACTIVE", "evidence": [{"claim": "role"}]}
                for iid, _ in issuers
            ],
            "link_coverage": [{"link_id": link_id, "status": "TARGET_MET"}],
        }
        if audit:
            digest = (check_map.mapping_fingerprint(doc) if fingerprint == "current"
                      else "0" * 64)
            doc["audit"] = {
                "status": audit, "mapping_fingerprint": digest,
                "reviewed_by": "atlas-fresh-context",
                "amendments_required": ["a placement lost its role evidence"]
                if audit == "FAIL" else [],
            }
        self.write(f"mappings/{chain_id}.json", doc)

    def profile(self, issuer_id, chain_id, link_id="l-one", *, tier="O2",
                status="COMPLETE", handoff=None, listing_id=None):
        doc = {
            "issuer_id": issuer_id, "issuer_name": issuer_id, "as_of": "2026-08-30",
            "status": status, "opportunity_tier": tier, "data_tier": "T1",
            "placements": [{"chain_id": chain_id, "link_id": link_id}],
            "listing_refs": [listing_id] if listing_id else [],
        }
        if handoff:
            doc["selection_basis"] = {"screen_handoff": handoff}
        self.write(f"companies/{issuer_id}.json", doc)

    def screen(self, chain_id, rows=()):
        self.write(f"screens/{chain_id}.json", {
            "id": chain_id, "chain_id": chain_id, "scenario_id": None,
            "as_of": "2026-08-30",
            "buckets": {"pure_play": list(rows)},
        })

    def stock(self, ticker, chain_id, *, issuer_id, listing_id, link_id="l-one",
              status="FINAL"):
        self.write(f"stocks/{ticker}__{chain_id}.json", {
            "ticker": ticker, "issuer_id": issuer_id, "listing_id": listing_id,
            "chain_id": chain_id, "link_id": link_id, "status": status,
        })

    def market(self, ticker, *, quality=True):
        doc = {"ticker": ticker, "series": {"close": [1, 2]}}
        if quality:
            doc["quality"] = {"piotroski": 6}
        self.write(f"market/{campaign_board.safe_name(ticker)}.json", doc)

    def campaign(self, chain_ids, *, campaign_id="CAMP-20260830-01", ranks=None):
        ranks = ranks or {}
        self.write(f"campaigns/{campaign_id}.json", {
            "id": campaign_id, "as_of": "2026-08-30", "status": "ACTIVE",
            "targets": dict(check_campaign.LOCKED_TARGETS),
            "themes": [
                {"theme_id": f"THEME-{cid}", "chain_id": cid, "signal_id": "SIG-20260830-01",
                 "title": f"Theme {cid}", "stage": "SELECTED",
                 "rank": ranks.get(cid, i + 1)}
                for i, cid in enumerate(chain_ids)
            ],
        })
        return campaign_id

    def board(self):
        return campaign_board.build_board(self.root)

    def row(self, theme_id=None):
        rows = self.board()["worklist"]
        if theme_id is None:
            return rows[0]
        return next(row for row in rows if row["theme_id"] == theme_id)


def blocker_kinds(row):
    return {b["kind"] for b in row["blockers"]}


def full_theme(tree, chain_id, *, issuer_count=10, o1=False, quality=True,
               dive_status=None):
    """A theme carried up the ladder to MAPPED, or past it when asked.

    The 10 issuers are not decoration: `_actual_theme_stage` will not leave MAPPED below
    `profiles_per_theme_min`, so a smaller fixture could never reach the later stages the
    board must produce commands for.
    """
    tree.signal()
    tree.chain(chain_id, heat=True, scenarios=True)
    issuers = [(f"ISS-{i:02d}", f"TK{i:02d}") for i in range(issuer_count)]
    tree.mapping(chain_id, issuers=issuers)
    return issuers


class TestStageLadderAgreesWithTheGate(unittest.TestCase):
    """The board must never report a stage check_campaign.py would not."""

    def test_current_tree_stages_match_check_campaign(self):
        board = campaign_board.build_board(ROOT)
        self.assertNotEqual(
            board["scope"], "SCOPE_EMPTY",
            "the repo's own tree must have campaign work on it for this test to mean "
            "anything; a SCOPE_EMPTY board here would be a vacuous pass")
        self.assertTrue(board["worklist"])
        # Same targets the board ran under: since 2026-09-01 a manifest names its mode
        # (BREADTH or DEPTH) and the stage ladder reads it, so the gate is asked under
        # the live manifest's targets, never under a hardcoded breadth set.
        campaign = {
            "themes": [{"theme_id": row["theme_id"], "chain_id": row["chain_id"]}
                       for row in board["worklist"]],
            "targets": dict(board["targets"] or check_campaign.LOCKED_TARGETS),
        }
        computed = check_campaign.compute_campaign_completion(ROOT, campaign)
        gate = {row["theme_id"]: row["stage_computed"] for row in computed["per_theme"]}
        self.assertEqual(
            {row["theme_id"]: row["stage"] for row in board["worklist"]}, gate)

    def test_fixture_stages_match_check_campaign(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            tree.signal()
            tree.chain("chain-bare")
            tree.chain("chain-hot", heat=True)
            tree.chain("chain-scen", heat=True, scenarios=True)
            full_theme(tree, "chain-mapped")
            campaign_id = tree.campaign(
                ["chain-bare", "chain-hot", "chain-scen", "chain-mapped"])
            board = tree.board()
            manifest = json.loads(
                (tree.data / "campaigns" / f"{campaign_id}.json").read_text())
            computed = check_campaign.compute_campaign_completion(tree.root, manifest)
        gate = {row["theme_id"]: row["stage_computed"] for row in computed["per_theme"]}
        self.assertEqual(
            {row["theme_id"]: row["stage"] for row in board["worklist"]}, gate)
        self.assertEqual(
            sorted(gate.values()),
            sorted(["CHAINED", "HEATED", "SCENARIOS", "MAPPED"]))


class TestEveryEmittedCommandIsRunnable(unittest.TestCase):
    """A command the queue would refuse is a dead end printed as an instruction."""

    def _assert_runnable(self, board):
        emitted = [row["next_command"] for row in board["worklist"]
                   if row["next_command"]]
        if board["next_command"]:
            emitted.append(board["next_command"])
        self.assertTrue(emitted, "a board with no runnable command proves nothing")
        for cmd in emitted:
            self.assertTrue(is_allowed(cmd),
                            f"campaign_board emitted {cmd!r}, which "
                            f"queue_allowlist.is_allowed refuses")
        return emitted

    def test_current_tree(self):
        self._assert_runnable(campaign_board.build_board(ROOT))

    def test_every_stage_emits_an_allowed_command(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            tree.signal()
            tree.chain("chain-bare")
            tree.chain("chain-hot", heat=True)
            tree.chain("chain-scen", heat=True, scenarios=True)
            full_theme(tree, "chain-mapped")
            tree.campaign(["chain-bare", "chain-hot", "chain-scen", "chain-mapped"])
            board = tree.board()
            emitted = self._assert_runnable(board)
        self.assertIn("run heat chain-bare", emitted)
        self.assertIn("run scenarios chain-hot", emitted)
        self.assertIn("run universe chain-scen", emitted)
        self.assertIn("run profile --campaign CAMP-20260830-01", emitted)

    def test_provisional_tree_without_a_campaign_still_emits_commands(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            full_theme(tree, "chain-mapped")
            board = tree.board()
            emitted = self._assert_runnable(board)
        self.assertEqual(board["scope"], "PROVISIONAL")
        self.assertEqual(board["next_command"], "run campaign init")
        # No manifest means no `--campaign` batch, so the board falls back to the
        # one-ticker shape rather than printing a command that cannot be formed.
        self.assertIn("run profile TK00", emitted)

    def test_a_ticker_the_queue_would_refuse_becomes_a_blocker_not_a_command(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            tree.signal()
            tree.chain("chain-bad", heat=True, scenarios=True)
            tree.mapping("chain-bad",
                         issuers=[("ISS-00", "this-is-not-a-ticker-at-all")])
            row = tree.row()
        self.assertIsNone(row["next_command"])
        self.assertIn("UNRUNNABLE_TICKER", blocker_kinds(row))


class TestBlockersComeFromDisk(unittest.TestCase):
    def test_mapping_active_awaits_a_fresh_context_audit(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            tree.signal()
            tree.chain("chain-a", heat=True, scenarios=True)
            tree.mapping("chain-a", issuers=[("ISS-00", "TK00")],
                         status="ACTIVE", audit=None)
            row = tree.row()
        self.assertEqual(row["stage"], "SCENARIOS")
        self.assertEqual(row["next_command"], "run universe-audit chain-a")
        self.assertIn("MAPPING_AWAITING_AUDIT", blocker_kinds(row))

    def test_mapping_with_an_untouched_link_recommends_universe_not_audit(self):
        """A link_coverage row with zero placements and no EXHAUSTED search can never be
        sampled by a fresh-context audit (check_map.py requires sampled_checks to cover
        every link_coverage row exactly). Recommending `run universe-audit` for such a
        mapping reproduces that failure deterministically instead of naming the actual
        prerequisite. Regression for the 2026-08-31 munitions-replenishment deadlock,
        where four consecutive routine fires re-hit this exact wall."""
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            tree.signal()
            tree.chain("chain-a", links=("l-one", "l-two"), heat=True, scenarios=True)
            tree.mapping("chain-a", issuers=[("ISS-00", "TK00")],
                         status="ACTIVE", audit=None)
            doc = json.loads((tree.data / "mappings/chain-a.json").read_text())
            doc["link_coverage"].append(
                {"link_id": "l-two", "status": "OPEN", "distinct_issuer_count": 0})
            tree.write("mappings/chain-a.json", doc)
            row = tree.row()
        self.assertEqual(row["stage"], "SCENARIOS")
        self.assertEqual(row["next_command"], "run universe chain-a")
        self.assertIn("MAPPING_LINK_UNTOUCHED", blocker_kinds(row))
        self.assertNotIn("MAPPING_AWAITING_AUDIT", blocker_kinds(row))
        untouched = next(b for b in row["blockers"]
                          if b["kind"] == "MAPPING_LINK_UNTOUCHED")
        self.assertIn("l-two", untouched["detail"])

    def test_mapping_audit_fail_routes_back_to_the_author(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            tree.signal()
            tree.chain("chain-a", heat=True, scenarios=True)
            tree.mapping("chain-a", issuers=[("ISS-00", "TK00")],
                         status="ACTIVE", audit="FAIL")
            row = tree.row()
        self.assertIn("MAPPING_AUDIT_FAIL", blocker_kinds(row))
        self.assertEqual(row["next_command"], "run universe chain-a")

    def test_stale_mapping_fingerprint_is_reported_under_a_complete_map(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            tree.signal()
            tree.chain("chain-a", heat=True, scenarios=True)
            tree.mapping("chain-a", issuers=[("ISS-00", "TK00")],
                         status="COMPLETE", audit="PASS", fingerprint="stale")
            tree.screen("chain-a")
            row = tree.row()
        self.assertIn("MAPPING_FINGERPRINT_STALE", blocker_kinds(row))
        self.assertIn("SCREEN_MAPPING_STALE", blocker_kinds(row))

    def test_fewer_than_ten_complete_profiles_is_named_with_its_denominator(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            issuers = full_theme(tree, "chain-a")
            for issuer_id, _ in issuers[:3]:
                tree.profile(issuer_id, "chain-a")
            row = tree.row()
        self.assertEqual(row["stage"], "MAPPED")
        detail = next(b["detail"] for b in row["blockers"]
                      if b["kind"] == "PROFILES_BELOW_MINIMUM")
        self.assertIn("3/10", detail)

    def test_a_profile_blocked_behind_a_pending_row_is_named_with_the_request_id(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            issuers = full_theme(tree, "chain-a")
            tree.profile("ISS-00", "chain-a", tier="O3", status="BLOCKED")
            for issuer_id, _ in issuers[1:]:
                tree.profile(issuer_id, "chain-a")
            tree.write("requests.json", {"version": 1, "requests": [
                {"id": "REQ-20260830-01", "ticker": "TK00", "status": "PENDING"},
                {"id": "REQ-20260830-02", "ticker": "TK01", "status": "FULFILLED"},
            ]})
            row = tree.row()
        detail = next(b["detail"] for b in row["blockers"]
                      if b["kind"] == "PROFILE_PENDING_DATA")
        self.assertIn("REQ-20260830-01", detail)
        self.assertNotIn("REQ-20260830-02", detail)

    def test_a_dive_ticker_without_a_quality_block_queues_the_fetch_instead(self):
        for quality, expected in ((True, "run deepdive TK00 chain-a"),
                                  (False, "request data TK00")):
            with self.subTest(quality=quality), tempfile.TemporaryDirectory() as td:
                tree = Tree(td)
                issuers = full_theme(tree, "chain-a")
                handoff = {"screen_ref": "chain-a", "chain_id": "chain-a",
                           "link_id": "l-one", "listing_id": "XNYS:TK00"}
                tree.profile("ISS-00", "chain-a", tier="O1", handoff=handoff,
                             listing_id="XNYS:TK00")
                for issuer_id, ticker in issuers[1:]:
                    tree.profile(issuer_id, "chain-a")
                tree.screen("chain-a", rows=[{
                    "ticker": "TK00", "issuer_id": "ISS-00",
                    "listing_id": "XNYS:TK00", "link_id": "l-one"}])
                tree.market("TK00", quality=quality)
                row = tree.row()
                self.assertEqual(row["stage"], "SCREENED")
                self.assertEqual(row["next_command"], expected)
                self.assertEqual("DIVE_DATA_MISSING" in blocker_kinds(row), not quality)

    def test_a_draft_dive_asks_for_its_red_team(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            issuers = full_theme(tree, "chain-a")
            handoff = {"screen_ref": "chain-a", "chain_id": "chain-a",
                       "link_id": "l-one", "listing_id": "XNYS:TK00"}
            tree.profile("ISS-00", "chain-a", tier="O1", handoff=handoff,
                         listing_id="XNYS:TK00")
            for issuer_id, _ in issuers[1:]:
                tree.profile(issuer_id, "chain-a")
            tree.screen("chain-a", rows=[{
                "ticker": "TK00", "issuer_id": "ISS-00",
                "listing_id": "XNYS:TK00", "link_id": "l-one"}])
            tree.market("TK00")
            tree.stock("TK00", "chain-a", issuer_id="ISS-00",
                       listing_id="XNYS:TK00", status="DRAFT")
            row = tree.row()
        self.assertEqual(row["stage"], "DIVED")
        self.assertEqual(row["next_command"], "run redteam TK00 chain-a")
        self.assertTrue(is_allowed(row["next_command"]))


class TestRanking(unittest.TestCase):
    def test_manifest_rank_leads_then_stage_then_id(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            tree.signal()
            # chain-late is further down the funnel but ranked last: rank wins.
            full_theme(tree, "chain-late")
            tree.chain("chain-early")
            tree.campaign(["chain-early", "chain-late"],
                          ranks={"chain-early": 1, "chain-late": 2})
            order = [row["chain_id"] for row in tree.board()["worklist"]]
        self.assertEqual(order, ["chain-early", "chain-late"])

    def test_unranked_provisional_themes_sort_by_stage_then_id(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            full_theme(tree, "zz-mapped")
            tree.chain("aa-bare")
            order = [row["chain_id"] for row in tree.board()["worklist"]]
        # aa-bare is CHAINED (earlier stage) so it leads despite the later id.
        self.assertEqual(order, ["aa-bare", "zz-mapped"])


class TestScopeEmpty(unittest.TestCase):
    def test_an_empty_tree_says_scope_empty_not_a_clean_board(self):
        with tempfile.TemporaryDirectory() as td:
            board = campaign_board.build_board(Path(td))
            text = campaign_board.render(board)
        self.assertEqual(board["scope"], "SCOPE_EMPTY")
        self.assertEqual(board["worklist"], [])
        self.assertIn("SCOPE EMPTY", text)
        self.assertNotIn("worklist (", text)

    def test_a_tree_with_no_chain_and_no_campaign_is_scope_empty(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)  # a real tree shape, but nothing analysed in it
            board = tree.board()
        self.assertEqual(board["scope"], "SCOPE_EMPTY")
        self.assertIn("no campaign manifest and no chain on disk", board["scope_note"])

    def test_strict_exits_one_on_scope_empty_and_zero_otherwise(self):
        with tempfile.TemporaryDirectory() as td:
            empty = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "campaign_board.py"),
                 "--root", td, "--strict"],
                capture_output=True, text=True)
        self.assertEqual(empty.returncode, 1)
        self.assertIn("SCOPE EMPTY", empty.stdout)
        live = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "campaign_board.py")],
            capture_output=True, text=True)
        self.assertEqual(live.returncode, 0, live.stderr)


class TestWriteIsTheOnlyPathThatWrites(unittest.TestCase):
    @staticmethod
    def _snapshot(root):
        return {str(p.relative_to(root)): p.stat().st_mtime_ns
                for p in Path(root).rglob("*") if p.is_file()}

    def _run(self, root, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "tools" / "campaign_board.py"),
             "--root", str(root), *args],
            capture_output=True, text=True)

    def test_default_and_json_invocations_change_nothing_on_disk(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            full_theme(tree, "chain-a")
            before = self._snapshot(td)
            for args in ((), ("--json",)):
                result = self._run(td, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self._snapshot(td), before,
                                 f"campaign_board.py {args} touched the tree")
            self.assertFalse((tree.data / "health" / "board.json").exists())

    def test_write_creates_exactly_one_file(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            full_theme(tree, "chain-a")
            before = set(self._snapshot(td))
            result = self._run(td, "--write")
            self.assertEqual(result.returncode, 0, result.stderr)
            after = set(self._snapshot(td))
            self.assertEqual(after - before, {"data/health/board.json"})
            written = json.loads((tree.data / "health" / "board.json").read_text())
        self.assertEqual(written["generated_by"], "tools/campaign_board.py")
        self.assertEqual([row["chain_id"] for row in written["worklist"]], ["chain-a"])

    def test_json_output_is_the_board_verbatim(self):
        with tempfile.TemporaryDirectory() as td:
            tree = Tree(td)
            full_theme(tree, "chain-a")
            result = self._run(td, "--json")
            printed, direct = json.loads(result.stdout), tree.board()
        # as_of is the only field that can differ between two runs (a midnight
        # crossing); everything else must be byte-identical or the CLI is not
        # printing the board it computed.
        printed.pop("as_of"), direct.pop("as_of")
        self.assertEqual(printed, direct)


if __name__ == "__main__":
    unittest.main()
