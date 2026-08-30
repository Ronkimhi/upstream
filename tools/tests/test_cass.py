#!/usr/bin/env python3
"""Exact tests for Cass review records and the `run devil` queue shape."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from queue_allowlist import is_allowed, reject_reason  # noqa: E402


def valid_review():
    return {
        "id": "REV-20260830-01",
        "as_of": "2026-08-30",
        "target": "tools/validate.py",
        "reviewed_by": "cass-adversary",
        "behavior_change": "Validation now rejects incomplete Cass review records.",
        "checks_touched": ["tools/validate.py:v_review"],
        "cost_if_wrong": "A malformed review could look complete.",
        "how_you_would_know": "A malformed fixture would pass tools/validate.py.",
        "challenges": [
            {
                "claim": "The required fields are sufficient.",
                "attack": "A required field can still be empty.",
                "survives": False,
            },
            {
                "claim": "Three challenges prove breadth.",
                "attack": "Three copies of one claim are only one challenge.",
                "survives": False,
            },
            {
                "claim": "Resolution needs ownership.",
                "attack": "Ownership would recreate the removed permission layer.",
                "survives": False,
            },
        ],
        "verdict": "ADOPT_NARROWED",
        "surviving_objection": "Fresh context is a process guarantee, not a JSON field.",
        "resolution": None,
        "changelog": [
            {"ts": "2026-08-30T03:20:00Z", "change": "Initial review."},
        ],
    }


class TestCassCommandAllowlist(unittest.TestCase):
    def test_safe_machine_paths_and_shas_are_allowed(self):
        for command in (
            "run devil CLAUDE.md",
            "run devil README.md",
            "run devil .claude/agents/cass-adversary.md",
            "run devil .claude/hooks/radar-gate.py",
            "run devil .github/workflows/ci.yml",
            "run devil docs/method.md",
            "run devil tools/validate.py",
            "run devil tools/tests/test_cass.py",
            "run devil app/templates/app.js",
            "run devil 609db56",
            "run devil 0123456789abcdef0123456789abcdef01234567",
        ):
            with self.subTest(command=command):
                self.assertTrue(is_allowed(command), f"safe target refused: {command}")

    def test_traversal_absolute_and_stock_paths_are_rejected(self):
        for command in (
            "run devil ../CLAUDE.md",
            "run devil tools/../CLAUDE.md",
            "run devil tools/../../etc/passwd",
            "run devil /etc/passwd",
            "run devil ~/secrets",
            "run devil data/stocks/VRT__ai-infrastructure.json",
            "run devil tools//validate.py",
            "run devil tools/./validate.py",
        ):
            with self.subTest(command=command):
                self.assertFalse(is_allowed(command), f"unsafe target accepted: {command}")

    def test_fullmatch_rejects_suffixes_and_malformed_shas(self):
        for command in (
            "run devil tools/validate.py && echo pwned",
            "run devil tools/validate.py; rm -rf data",
            "run devil tools/validate.py $(whoami)",
            "run devil 609db5",
            "run devil 609DB56",
            "run devil 0123456789abcdef0123456789abcdef012345678",
        ):
            with self.subTest(command=command):
                self.assertFalse(is_allowed(command), f"malformed command accepted: {command}")
        self.assertIn(
            "trailing text",
            reject_reason("run devil tools/validate.py && echo pwned"),
        )

    def test_every_ascii_control_character_is_rejected(self):
        for codepoint in (*range(32), 127):
            command = f"run devil tools/validate.py{chr(codepoint)}"
            with self.subTest(codepoint=codepoint):
                self.assertFalse(is_allowed(command))
                self.assertIn("control character", reject_reason(command))


class TestCassReviewValidation(unittest.TestCase):
    def run_validate(self, review, filename="REV-20260830-01.json"):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "data" / "reviews"
            folder.mkdir(parents=True)
            (folder / filename).write_text(json.dumps(review))
            return subprocess.run(
                [sys.executable, str(ROOT / "tools" / "validate.py"), "--root", str(root)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_complete_review_passes(self):
        result = self.run_validate(valid_review())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("reviews: 1", result.stdout)
        self.assertIn("validate: OK", result.stdout)

    def test_removed_ownership_and_rubber_stamp_shapes_fail(self):
        review = copy.deepcopy(valid_review())
        review.update({
            "actor": "ron",
            "ruling": {"by": "ron", "decision": "KEEP"},
            "reviewed_by": "someone-else",
            "checks_touched": [""],
            "verdict": "APPROVED",
            "resolution": {"by": "ron", "decision": "KEEP"},
        })
        review["challenges"][1]["claim"] = review["challenges"][0]["claim"]
        review["challenges"][2]["claim"] = review["challenges"][0]["claim"]
        result = self.run_validate(review)
        self.assertNotEqual(result.returncode, 0)
        for finding in (
            "actor is not part of an advisory review",
            "ruling is not part of an advisory review",
            "reviewed_by must be 'cass-adversary'",
            "checks_touched must be a list of non-empty strings",
            "verdict",
            "at least 3 distinct claims",
            "resolution must be null or a non-empty string",
        ):
            with self.subTest(finding=finding):
                self.assertIn(finding, result.stdout)

    def test_id_filename_date_and_required_fields_are_checked(self):
        review = valid_review()
        del review["cost_if_wrong"]
        result = self.run_validate(review)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing keys: cost_if_wrong", result.stdout)

        review = valid_review()
        result = self.run_validate(review, filename="REV-20260830-02.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("filename 'REV-20260830-02' must match id", result.stdout)

        review = valid_review()
        review["as_of"] = "2026-08-29"
        result = self.run_validate(review)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match id date 2026-08-30", result.stdout)


if __name__ == "__main__":
    unittest.main()
