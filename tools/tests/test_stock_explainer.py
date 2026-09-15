#!/usr/bin/env python3
"""The Hebrew investment-report layer (Ron, 2026-09-15), watched refusing something.

Stocky's dive `explainer` and Sieve's profile `selection_note` reuse
check_chain.explainer_failures rather than a second copy of the prose rules: Hebrew, no
figure, no em or en dash, every required key present, same as the chain and link
explainers. This file watches both refuse English text, a figure, a dash and a missing
key, and accept a valid Hebrew block, plus the dated ratchet in check_analyst.py: a dive
FINAL on or after STOCK_EXPLAINER_GATE, on its latest changelog date, must carry one.
"""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_analyst  # noqa: E402
import check_profile  # noqa: E402
from check_chain import explainer_failures  # noqa: E402
from test_stocky_admission import StockyAdmissionTree  # noqa: E402

HE = "החברה הזאת מוכרת שירות שלקוחות צריכים כל שנה מחדש ולכן ההכנסה שלה יציבה."


def stock_explainer(**over):
    x = {"lang": "he", "verdict_line": HE, "what_they_do": HE, "why_now": HE,
         "why_market_misses": HE, "what_could_break": HE,
         "what_would_change_our_mind": HE, "as_of": "2026-09-15", "by": "test"}
    x.update(over)
    return x


def selection_note(**over):
    x = {"lang": "he", "why_not_now": HE, "what_would_promote": HE,
         "as_of": "2026-09-15", "by": "test"}
    x.update(over)
    return x


class TestStockExplainerPure(unittest.TestCase):
    """explainer_failures over a dive's `explainer` field with check_analyst.STOCK_KEYS."""

    def test_a_valid_hebrew_report_passes(self):
        d = {"explainer": stock_explainer()}
        self.assertEqual(explainer_failures(d, check_analyst.STOCK_KEYS, "AAA"), [])

    def test_english_text_is_refused(self):
        d = {"explainer": stock_explainer(
            why_now="The company is expanding into new markets this year with a plan.")}
        out = explainer_failures(d, check_analyst.STOCK_KEYS, "AAA")
        self.assertTrue(any("why_now missing, short, or not Hebrew" in f for f in out), out)

    def test_a_figure_is_refused(self):
        d = {"explainer": stock_explainer(what_they_do=HE + " ההכנסה גדלה 40 אחוז.")}
        out = explainer_failures(d, check_analyst.STOCK_KEYS, "AAA")
        self.assertTrue(any("numeric token '40'" in f for f in out), out)

    def test_a_dash_is_refused(self):
        d = {"explainer": stock_explainer(what_could_break=HE + " וזה — סיכון אמיתי.")}
        out = explainer_failures(d, check_analyst.STOCK_KEYS, "AAA")
        self.assertTrue(any("em or en dash" in f for f in out), out)

    def test_a_missing_key_is_refused(self):
        x = stock_explainer()
        del x["what_would_change_our_mind"]
        out = explainer_failures({"explainer": x}, check_analyst.STOCK_KEYS, "AAA")
        self.assertTrue(
            any("what_would_change_our_mind missing, short, or not Hebrew" in f for f in out), out)


class TestSelectionNotePure(unittest.TestCase):
    """The same prose rules on Sieve's selection_note, via check_profile.SELECTION_NOTE_KEYS."""

    def test_a_valid_hebrew_note_passes(self):
        p = {"selection_note": selection_note()}
        self.assertEqual(
            explainer_failures(p, check_profile.SELECTION_NOTE_KEYS, "ISS-A",
                                field="selection_note"), [])

    def test_english_text_is_refused(self):
        p = {"selection_note": selection_note(
            why_not_now="It did not clear the capture bar this round at all.")}
        out = explainer_failures(p, check_profile.SELECTION_NOTE_KEYS, "ISS-A",
                                  field="selection_note")
        self.assertTrue(any("why_not_now missing, short, or not Hebrew" in f for f in out), out)

    def test_a_figure_is_refused(self):
        p = {"selection_note": selection_note(
            what_would_promote=HE + " גידול של עשרים אחוז יעזור לה, נגיד 20.")}
        out = explainer_failures(p, check_profile.SELECTION_NOTE_KEYS, "ISS-A",
                                  field="selection_note")
        self.assertTrue(any("numeric token '20'" in f for f in out), out)

    def test_a_dash_is_refused(self):
        p = {"selection_note": selection_note(why_not_now=HE + " וזה — הסיבה המלאה.")}
        out = explainer_failures(p, check_profile.SELECTION_NOTE_KEYS, "ISS-A",
                                  field="selection_note")
        self.assertTrue(any("em or en dash" in f for f in out), out)

    def test_a_missing_key_is_refused(self):
        x = selection_note()
        del x["what_would_promote"]
        out = explainer_failures({"selection_note": x}, check_profile.SELECTION_NOTE_KEYS,
                                  "ISS-A", field="selection_note")
        self.assertTrue(
            any("what_would_promote missing, short, or not Hebrew" in f for f in out), out)

    def test_check_profile_checks_it_when_present(self):
        profile = {"selection_note": selection_note(
            why_not_now="Too small a position for this campaign round yet.")}
        out = check_profile.validate_profile(Path("/nonexistent"), Path("ISS-A.json"), profile)
        self.assertTrue(any("selection_note.why_not_now" in f for f in out), out)

    def test_check_profile_is_silent_when_absent(self):
        out = check_profile.validate_profile(Path("/nonexistent"), Path("ISS-A.json"), {})
        self.assertFalse(any("selection_note" in f for f in out), out)

    def test_check_profile_accepts_a_valid_note(self):
        profile = {"selection_note": selection_note()}
        out = check_profile.validate_profile(Path("/nonexistent"), Path("ISS-A.json"), profile)
        self.assertFalse(any("selection_note" in f for f in out), out)


class TestStockExplainerGate(StockyAdmissionTree):
    """The dated ratchet at the real gate: check_analyst.py's exit code, over the fixture
    StockyAdmissionTree already proves clears O1 admission on its own merits, so a red
    exit here can only be the explainer bar."""

    def test_final_dive_on_or_after_gate_without_explainer_is_refused(self):
        stock = copy.deepcopy(self.stock)
        stock.update({"status": "FINAL", "updated_at": "2026-09-15"})
        result = self.run_gate(stock, date="2026-08-31")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("carries no explainer", result.stdout)

    def test_final_dive_before_gate_without_explainer_passes(self):
        stock = copy.deepcopy(self.stock)
        stock.update({"status": "FINAL", "updated_at": "2026-09-10"})
        result = self.run_gate(stock, date="2026-08-31")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("carries no explainer", result.stdout)

    def test_a_valid_hebrew_explainer_on_a_post_gate_final_passes(self):
        stock = copy.deepcopy(self.stock)
        stock.update({"status": "FINAL", "updated_at": "2026-09-15",
                      "explainer": stock_explainer()})
        result = self.run_gate(stock, date="2026-08-31")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_an_invalid_explainer_present_pre_gate_is_still_refused(self):
        """Any explainer present, gated or not, is checked in full — the bar is never
        only a post-gate requirement, it is a standing rule the moment one is written."""
        stock = copy.deepcopy(self.stock)
        stock.update({"status": "DRAFT", "updated_at": "2026-09-01",
                      "explainer": stock_explainer(
                          why_now="English prose here, not Hebrew at all, on purpose.")})
        result = self.run_gate(stock, date="2026-08-31")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("why_now missing, short, or not Hebrew", result.stdout)


if __name__ == "__main__":
    unittest.main()
