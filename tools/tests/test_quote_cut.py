"""A quote cut at a clause boundary may close with its own period (method section 1, 2026-09-13).

The span without that mark is already verbatim, so the mark adds no words. Every other
difference still refuses: a changed word, a mark moved into the middle, punctuation alone.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_screen  # noqa: E402
import evidence_store  # noqa: E402

SOURCE = ("Angus Blayney, marine divisional director at Gallagher, a major insurance broker, told Reuters "
          "that rates have increased and are changing daily depending on vessel type and individual "
          "circumstances, but he did not provide specific figures. \u201cThe hull war market has reacted "
          "more immediately,\u201d said Stephen Rudman.")


class TestQuoteCut(unittest.TestCase):
    def test_a_quote_closed_with_its_own_period_is_verbatim(self):
        quote = ("Angus Blayney, marine divisional director at Gallagher, a major insurance broker, told Reuters "
                 "that rates have increased and are changing daily depending on vessel type and individual "
                 "circumstances.")
        self.assertTrue(check_screen.quote_in_text(quote, SOURCE))
        self.assertTrue(evidence_store.excerpt_in_doc(quote, SOURCE))

    def test_straight_quotes_still_fold_to_the_page_curly_ones(self):
        quote = '"The hull war market has reacted more immediately," said Stephen Rudman.'
        self.assertTrue(evidence_store.excerpt_in_doc(quote, SOURCE))

    def test_a_changed_word_is_still_refused(self):
        quote = "told Reuters that rates have decreased and are changing daily."
        self.assertFalse(check_screen.quote_in_text(quote, SOURCE))
        self.assertFalse(evidence_store.excerpt_in_doc(quote, SOURCE))

    def test_a_mark_moved_into_the_middle_is_still_refused(self):
        self.assertFalse(evidence_store.excerpt_in_doc("individual circumstances. but he did not", SOURCE))

    def test_punctuation_alone_is_not_a_quote(self):
        self.assertFalse(evidence_store.excerpt_in_doc(" . ", SOURCE))


if __name__ == "__main__":
    unittest.main()
