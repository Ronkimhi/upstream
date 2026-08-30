#!/usr/bin/env python3
"""Focused tests for the Ten-Theme Stop-hook trigger boundaries."""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


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
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(HOOKS))
        cls.campaign = load_hook("campaign")
        cls.universe = load_hook("universe")
        cls.profile = load_hook("profile")

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


if __name__ == "__main__":
    unittest.main()
