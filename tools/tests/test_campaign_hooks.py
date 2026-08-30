#!/usr/bin/env python3
"""Focused tests for campaign, universe, and profile Stop hooks."""
import contextlib
import datetime
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS = ROOT / ".claude" / "hooks"


def load_hook(name):
    path = HOOKS / f"{name}-gate.py"
    spec = importlib.util.spec_from_file_location(f"{name}_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def transcript(path):
    event = {
        "message": {
            "content": [{
                "type": "tool_use",
                "name": "Write",
                "input": {"file_path": path},
            }]
        }
    }
    return json.dumps(event)


class TestCampaignStopHooks(unittest.TestCase):
    HOOK_CASES = {
        "campaign": {
            "target": "data/campaigns/CAMP-20260829-01.json",
            "command": "run campaign init",
        },
        "universe": {
            "target": "data/mappings/ai-infrastructure.json",
            "command": "run universe ai-infrastructure",
        },
        "profile": {
            "target": "data/companies/ACME.json",
            "command": "run profile ACME",
        },
    }

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(HOOKS))
        cls.campaign = load_hook("campaign")
        cls.universe = load_hook("universe")
        cls.profile = load_hook("profile")

    @staticmethod
    def _write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def _decision(
        self,
        hook_name,
        *,
        today,
        ledger_timestamp,
        calibration_timestamp,
        command=None,
        mapping=None,
    ):
        hook = getattr(self, hook_name)
        case = self.HOOK_CASES[hook_name]
        command = command or case["command"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript_path = root / "transcript.jsonl"
            transcript_path.write_text(transcript(case["target"]))

            ledger_path = root / "data" / "ledger.md"
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            if ledger_timestamp is None:
                ledger_path.write_text("")
            else:
                ledger_path.write_text(
                    f"{ledger_timestamp} | RUN | {command} | by: ron\n"
                )

            target_path = root / case["target"]
            if hook_name == "campaign":
                changelog = (
                    []
                    if calibration_timestamp is None
                    else [{
                        "ts": calibration_timestamp,
                        "by": "campaign_calibrate",
                    }]
                )
                self._write_json(target_path, {"changelog": changelog})
            elif hook_name == "universe":
                self._write_json(target_path, mapping or {})
                if calibration_timestamp is not None:
                    self._write_json(
                        root / "data" / "chains" / "_map-log.json",
                        {"calibration": {"generated_at": calibration_timestamp}},
                    )
            else:
                self._write_json(target_path, {})
                if calibration_timestamp is not None:
                    self._write_json(
                        root / "data" / "companies" / "_profile-log.json",
                        {"calibration": {"generated_at": calibration_timestamp}},
                    )

            payload = json.dumps({"transcript_path": str(transcript_path)})
            output = io.StringIO()
            with (
                mock.patch.object(hook, "ROOT", root),
                mock.patch.object(hook, "utc_today", return_value=today),
                mock.patch.object(sys, "stdin", io.StringIO(payload)),
                contextlib.redirect_stdout(output),
            ):
                hook.main()
            return json.loads(output.getvalue())["decision"]

    def _audit_mapping(self, *, status="PASS"):
        mapping = {
            "id": "MAP-ai-infrastructure",
            "chain_id": "ai-infrastructure",
            "status": "COMPLETE" if status == "PASS" else "ACTIVE",
            "target_issuers_per_link": 10,
            "issuers": [],
            "listings": [],
            "placements": [],
            "link_coverage": [],
            "changelog": [],
        }
        mapping["audit"] = {
            "audited_at": "2026-08-30T00:00:00Z",
            "reviewed_by": "atlas-fresh-context",
            "agent_id": "atlas-fresh-context",
            "transcript_ref": "audit-transcript-20260830-hook",
            "review_mode": "FRESH_CONTEXT",
            "independence_limitation": (
                "Repository declarations cannot prove fresh-context independence."
            ),
            "status": status,
            "mapping_fingerprint": self.universe.mapping_fingerprint(mapping),
            "links_examined": 0,
            "listings_examined": 0,
            "placements_examined": 0,
            "searches_examined": 0,
            "target_met_links_examined": 0,
            "sampled_checks": [],
            "identity_conflicts": (
                [] if status == "PASS" else ["Issuer identity needs correction"]
            ),
            "role_conflicts": [],
            "source_date_conflicts": [],
            "exhausted_links_examined": 0,
            "amendments_required": (
                [] if status == "PASS" else ["Correct the issuer identity"]
            ),
            "surviving_limitation": "No private-company disclosure is available.",
        }
        return mapping

    def test_each_hook_triggers_only_for_its_live_store(self):
        self.assertEqual(
            self.campaign.written_campaigns(
                transcript("data/campaigns/CAMP-20260829-01.json")),
            {"CAMP-20260829-01.json"},
        )
        self.assertEqual(
            self.universe.written_mappings(
                transcript("data/mappings/ai-infrastructure.json")),
            {"ai-infrastructure"},
        )
        self.assertEqual(
            self.profile.written_profiles(
                transcript("data/companies/ISSUER-1.json")),
            {"ISSUER-1"},
        )

    def test_calibration_logs_and_unrelated_files_do_not_trigger(self):
        self.assertEqual(
            self.campaign.written_campaigns(
                transcript("data/campaigns/_campaign-log.json")),
            set(),
        )
        self.assertEqual(
            self.universe.written_mappings(
                transcript("data/mappings/_map-log.json")),
            set(),
        )
        self.assertEqual(
            self.profile.written_profiles(
                transcript("data/companies/_profile-log.json")),
            set(),
        )

    def test_malformed_hook_input_fails_open(self):
        for name in ("campaign", "universe", "profile"):
            result = subprocess.run(
                [sys.executable, str(HOOKS / f"{name}-gate.py")],
                input="not json",
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(json.loads(result.stdout), {"decision": "approve"})

    def test_reentrant_stop_fails_open(self):
        payload = json.dumps({"stop_hook_active": True})
        for name in ("campaign", "universe", "profile"):
            result = subprocess.run(
                [sys.executable, str(HOOKS / f"{name}-gate.py")],
                input=payload,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(json.loads(result.stdout), {"decision": "approve"})

    def test_timestamps_normalize_across_both_sides_of_utc_midnight(self):
        cases = (
            (
                "2026-08-30T13:59:59+14:00",
                datetime.date(2026, 8, 29),
                datetime.date(2026, 8, 30),
            ),
            (
                "2026-08-29T17:00:00-07:00",
                datetime.date(2026, 8, 30),
                datetime.date(2026, 8, 29),
            ),
        )
        for name in self.HOOK_CASES:
            hook = getattr(self, name)
            for timestamp, utc_day, local_day in cases:
                with self.subTest(hook=name, timestamp=timestamp):
                    self.assertNotEqual(utc_day, local_day)
                    self.assertEqual(hook.utc_date(timestamp), utc_day)
                    self.assertEqual(
                        self._decision(
                            name,
                            today=utc_day,
                            ledger_timestamp=timestamp,
                            calibration_timestamp=timestamp,
                        ),
                        "approve",
                    )

    def test_stale_utc_ledger_or_calibration_blocks(self):
        before_midnight = "2026-08-30T13:59:59+14:00"
        after_midnight = "2026-08-29T17:00:00-07:00"
        today = datetime.date(2026, 8, 30)
        for name in self.HOOK_CASES:
            with self.subTest(hook=name, stale="ledger"):
                self.assertEqual(
                    self._decision(
                        name,
                        today=today,
                        ledger_timestamp=before_midnight,
                        calibration_timestamp=after_midnight,
                    ),
                    "block",
                )
            with self.subTest(hook=name, stale="calibration"):
                self.assertEqual(
                    self._decision(
                        name,
                        today=today,
                        ledger_timestamp=after_midnight,
                        calibration_timestamp=before_midnight,
                    ),
                    "block",
                )

    def test_missing_ledger_or_calibration_blocks(self):
        timestamp = "2026-08-30T00:00:00Z"
        today = datetime.date(2026, 8, 30)
        for name in self.HOOK_CASES:
            for missing in (None, ""):
                with self.subTest(
                    hook=name, missing="ledger", value=missing
                ):
                    self.assertEqual(
                        self._decision(
                            name,
                            today=today,
                            ledger_timestamp=missing,
                            calibration_timestamp=timestamp,
                        ),
                        "block",
                    )
                with self.subTest(
                    hook=name, missing="calibration", value=missing
                ):
                    self.assertEqual(
                        self._decision(
                            name,
                            today=today,
                            ledger_timestamp=timestamp,
                            calibration_timestamp=missing,
                        ),
                        "block",
                    )

    def test_universe_audit_uses_current_audit_instead_of_map_calibration(self):
        timestamp = "2026-08-30T00:00:00Z"
        today = datetime.date(2026, 8, 30)
        for status in ("PASS", "FAIL"):
            with self.subTest(status=status):
                self.assertEqual(
                    self._decision(
                        "universe",
                        today=today,
                        ledger_timestamp=timestamp,
                        calibration_timestamp=None,
                        command="run universe-audit ai-infrastructure",
                        mapping=self._audit_mapping(status=status),
                    ),
                    "approve",
                )

    def test_universe_audit_blocks_stale_fingerprint_or_wrong_reviewer(self):
        timestamp = "2026-08-30T00:00:00Z"
        today = datetime.date(2026, 8, 30)
        for field, value in (
            ("mapping_fingerprint", "0" * 64),
            ("reviewed_by", "atlas-cartographer"),
        ):
            mapping = self._audit_mapping()
            mapping["audit"][field] = value
            with self.subTest(field=field):
                self.assertEqual(
                    self._decision(
                        "universe",
                        today=today,
                        ledger_timestamp=timestamp,
                        calibration_timestamp=None,
                        command="run universe-audit ai-infrastructure",
                        mapping=mapping,
                    ),
                    "block",
                )

    def test_malformed_ledger_or_calibration_timestamp_fails_open(self):
        timestamp = "2026-08-30T00:00:00Z"
        today = datetime.date(2026, 8, 30)
        for name in self.HOOK_CASES:
            for malformed in ("not-a-timestamp", "2026-08-30T00:00:00"):
                with self.subTest(
                    hook=name, malformed="ledger", value=malformed
                ):
                    self.assertEqual(
                        self._decision(
                            name,
                            today=today,
                            ledger_timestamp=malformed,
                            calibration_timestamp=timestamp,
                        ),
                        "approve",
                    )
                with self.subTest(
                    hook=name, malformed="calibration", value=malformed
                ):
                    self.assertEqual(
                        self._decision(
                            name,
                            today=today,
                            ledger_timestamp=timestamp,
                            calibration_timestamp=malformed,
                        ),
                        "approve",
                    )


if __name__ == "__main__":
    unittest.main()
