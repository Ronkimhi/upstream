"""A superseded placement audit is history; only the newest audit per placement must match content."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_map as cm  # noqa: E402

STALE = "record_digest is not current"


def placement(excerpt):
    return {"chain_id": "c", "link_id": "l", "issuer_id": "X", "role": "makes the part", "status": "ACTIVE",
            "evidence": [{"claim": "X makes the part.", "source_name": "10-K", "source_date": "2026-01-01",
                          "url": "https://example.com/10k", "tag": "VERIFIED", "source_excerpt": excerpt}]}


def entry(p, status, ts):
    return {"chain_id": "c", "link_id": "l", "issuer_id": "X", "audited_at": ts,
            "reviewed_by": cm.AUDIT_REVIEWER, "agent_id": cm.AUDIT_REVIEWER, "transcript_ref": "session transcript ref",
            "review_mode": sorted(cm.AUDIT_REVIEW_MODES)[0],
            "independence_limitation": "Repository state cannot prove the reviewer never saw the author's reasoning.",
            "status": status, "evidence_index": 0,
            "source_excerpt": "X manufactures the part at three plants for the named program.",
            "record_digest": cm.placement_claim_digest(p, p["evidence"][0])}


class PlacementAuditHistory(unittest.TestCase):
    def setUp(self):
        self.old = placement("spliced old excerpt of the filing")
        self.new = placement("contiguous new excerpt of the filing")

    def stale_failures(self, entries):
        m = {"placements": [self.new], "placement_audits": entries}
        return [f for f in cm.placement_audit_failures(m) if STALE in f]

    def test_superseded_stale_entry_is_history(self):
        entries = [entry(self.old, "FAIL", "2026-09-13T10:00:00Z"), entry(self.new, "PASS", "2026-09-14T02:00:00Z")]
        self.assertEqual(self.stale_failures(entries), [])

    def test_lone_stale_entry_still_fails(self):
        self.assertEqual(len(self.stale_failures([entry(self.old, "PASS", "2026-09-13T10:00:00Z")])), 1)

    def test_newest_entry_governs_even_when_listed_first(self):
        entries = [entry(self.old, "PASS", "2026-09-14T02:00:00Z"), entry(self.new, "FAIL", "2026-09-13T10:00:00Z")]
        self.assertEqual(len(self.stale_failures(entries)), 1)


if __name__ == "__main__":
    unittest.main()
