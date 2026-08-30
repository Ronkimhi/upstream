#!/usr/bin/env python3
"""Exact allowlist tests for Ten-Theme campaign commands.

These commands can arrive through the shared artifact queue. They are accepted only as
complete canonical strings; shell syntax, control characters, aliases, and partial ids are
all data to reject.
"""
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from queue_allowlist import is_allowed, reject_reason  # noqa: E402


class TestCampaignCommandAllowlist(unittest.TestCase):
    def test_all_canonical_commands_are_allowed(self):
        for command in (
            "run campaign init",
            "run universe ai-infrastructure",
            "run profile VRT",
            "run profile --campaign CAMP-20260829-01",
            "run selection CAMP-20260829-01",
        ):
            with self.subTest(command=command):
                self.assertTrue(is_allowed(command), f"canonical command refused: {command}")

    def test_shell_suffixes_are_rejected(self):
        for command in (
            "run campaign init && echo pwned",
            "run universe ai-infrastructure; rm -rf data",
            "run profile VRT $(whoami)",
            "run profile --campaign CAMP-20260829-01 | sh",
            "run selection CAMP-20260829-01 > /tmp/result",
        ):
            with self.subTest(command=command):
                self.assertFalse(is_allowed(command), f"injection accepted: {command}")

    def test_control_characters_and_whitespace_are_rejected(self):
        for command in (
            " run campaign init",
            "run campaign init ",
            "run universe ai-infrastructure\nrun profile VRT",
            "run profile\tVRT",
            "run selection CAMP-20260829-01\r",
            "run profile VRT\x00",
            "run campaign init\x1f",
            "run campaign init\x7f",
        ):
            with self.subTest(command=command):
                self.assertFalse(is_allowed(command), f"non-canonical whitespace accepted: {command!r}")

    def test_every_ascii_control_character_is_rejected(self):
        for codepoint in (*range(32), 127):
            command = f"run campaign init{chr(codepoint)}"
            with self.subTest(codepoint=codepoint):
                self.assertFalse(is_allowed(command), f"control character U+{codepoint:04X} accepted")
                self.assertIn("control character", reject_reason(command))

    def test_ids_and_case_are_exact(self):
        for command in (
            "run campaign",
            "run campaign initialize",
            "run universe AI-Infrastructure",
            "run universe ../secrets",
            "run profile vrt",
            "run profile --campaign camp-20260829-01",
            "run profile --campaign CAMP-20260829-1",
            "run selection CAMP-20260829-001",
            "run selection CAMP-20260829-01 extra",
        ):
            with self.subTest(command=command):
                self.assertFalse(is_allowed(command), f"malformed command accepted: {command}")

    def test_batch_and_single_profile_shapes_do_not_overlap(self):
        self.assertTrue(is_allowed("run profile BRK.B"))
        self.assertTrue(is_allowed("run profile HPS-A.TO"))
        self.assertFalse(is_allowed("run profile --campaign"))
        self.assertFalse(is_allowed("run profile --campaign CAMP-20260829-01 VRT"))
        self.assertIn(
            "trailing text",
            reject_reason("run selection CAMP-20260829-01 && echo pwned"),
        )


if __name__ == "__main__":
    unittest.main()
