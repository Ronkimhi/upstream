"""The Hebrew page gate (`tools/check_hebrew.py`), watched refusing something.

Ron, 2026-09-13: the whole platform speaks plain Hebrew. A gate that only ever reports
OK over a Hebrew page proves nothing, so these tests hand it English and expect a
refusal, hand it the tokenizer's known traps (a regex literal carrying quote characters,
a literal that starts or ends inside a tag) and expect the right reading, and hand it a
tree missing each declaration and expect the named failure.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_hebrew as ch  # noqa: E402


class Tokenizer(unittest.TestCase):
    def test_regex_with_quotes_does_not_open_a_string(self):
        src = 'var a = s.replace(/[&<>"\']/g, function (c) { return "x"; });\nvar b = "לוח";'
        lits = [t for _, t in ch.js_string_literals(src)]
        self.assertEqual(lits, ["x", "לוח"])

    def test_comments_are_skipped_and_lines_counted(self):
        src = '/* "not a literal" */\n// "nor this"\nvar a = "Board";'
        self.assertEqual(ch.js_string_literals(src), [(3, "Board")])

    def test_unicode_escapes_decode(self):
        self.assertEqual(ch.js_string_literals('x = "\\u05D0";'), [(1, "א")])


class Segments(unittest.TestCase):
    def test_open_tag_fragment_has_no_visible_text(self):
        self.assertEqual(ch.visible_segments('<span class="chip '), [])

    def test_tag_head_is_stripped_and_the_text_kept(self):
        segs = ch.visible_segments('" title="copy command">copy</button></span>')
        self.assertIn(("copy command", True), segs)
        self.assertIn(("copy", True), segs)

    def test_english_phrase_fails_and_hebrew_passes(self):
        self.assertTrue(ch.english_failures("Money corner"))
        self.assertTrue(ch.english_failures("<h3>Value capture</h3>"))
        self.assertEqual(ch.english_failures("<h3>לכידת ערך</h3>"), [])

    def test_latin_between_tags_fails_but_a_tagless_lowercase_word_is_a_key(self):
        self.assertTrue(ch.english_failures("<span class='muted'>none</span>"))
        self.assertEqual(ch.english_failures("neutral"), [])

    def test_machine_tokens_commands_and_paths_are_allowed_anywhere(self):
        for lit in ("<span>FINAL</span>", "run radar", "data/chains/x.json", "<b>O1</b>",
                    "https://example.com/a", "#/chain/ai-infrastructure", "REQ-20260913-01"):
            self.assertEqual(ch.english_failures(lit), [], lit)

    def test_a_capitalised_word_alone_is_a_label(self):
        self.assertTrue(ch.english_failures("Board"))
        self.assertTrue(ch.english_failures("<h1>Signals</h1>"))


def run_gate(root: Path, *args):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "check_hebrew.py"), "--root", str(root), *args],
                          capture_output=True, text=True)


class GateOnATree(unittest.TestCase):
    def _tree(self, js: str, guide: str = "<div>" + "א" * 2100 + "</div>"):
        d = Path(tempfile.mkdtemp())
        (d / "app" / "templates").mkdir(parents=True)
        (d / "tools").mkdir()
        (d / "app" / "templates" / "app.js").write_text(js, encoding="utf-8")
        (d / "app" / "templates" / "shell.html").write_text("family=Heebo", encoding="utf-8")
        (d / "app" / "templates" / "guide-shell.html").write_text('family=Heebo dir="rtl"', encoding="utf-8")
        (d / "app" / "templates" / "app.css").write_text(
            "body { font: 400 14px/1.6 Heebo, Inter; } svg { direction: ltr; } .num { unicode-bidi: isolate; }",
            encoding="utf-8")
        (d / "app" / "templates" / "guide.html").write_text("<style></style>" + guide, encoding="utf-8")
        (d / "tools" / "opportunities.py").write_text("x = 1\n", encoding="utf-8")
        (d / "tools" / "campaign_board.py").write_text("x = 1\n", encoding="utf-8")
        return d

    BOOT = ('document.documentElement.setAttribute("dir", "rtl");\n'
            'document.documentElement.setAttribute("lang", "he");\nfunction he(t) { return t; }\n')

    def test_clean_hebrew_tree_passes_and_reports_its_denominator(self):
        d = self._tree(self.BOOT + 'var a = "<h1>לוח</h1>";')
        r = run_gate(d)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("string literals", r.stdout)
        self.assertIn("check_hebrew: OK", r.stdout)

    def test_one_english_label_refuses(self):
        d = self._tree(self.BOOT + 'var a = "<h1>Board</h1>";')
        r = run_gate(d)
        self.assertEqual(r.returncode, 1)
        self.assertIn("English label 'Board'", r.stdout)

    def test_missing_boot_declaration_refuses(self):
        d = self._tree('function he(t) { return t; }\nvar a = "<h1>לוח</h1>";')
        r = run_gate(d)
        self.assertEqual(r.returncode, 1)
        self.assertIn("lost the root dir=rtl declaration", r.stdout)

    def test_english_guide_refuses(self):
        d = self._tree(self.BOOT + 'var a = "לוח";',
                       guide="<p>This guide is still written in English and that is the defect.</p>")
        r = run_gate(d)
        self.assertEqual(r.returncode, 1)
        self.assertIn("guide.html: only", r.stdout)
        self.assertIn("English sentence outside", r.stdout)

    def test_english_generator_template_refuses(self):
        d = self._tree(self.BOOT + 'var a = "לוח";')
        (d / "tools" / "opportunities.py").write_text('x = "Moves hard (1 of 100)"\n', encoding="utf-8")
        r = run_gate(d)
        self.assertEqual(r.returncode, 1)
        self.assertIn("English sentence template still present", r.stdout)

    def test_js_flag_lints_one_file_only(self):
        d = self._tree(self.BOOT + 'var a = "לוח";')
        part = d / "part.js"
        part.write_text('var a = "Money corner";', encoding="utf-8")
        r = run_gate(d, "--js", str(part))
        self.assertEqual(r.returncode, 1)
        self.assertIn("English phrase 'Money corner'", r.stdout)


class RealTree(unittest.TestCase):
    """The committed renderer must pass its own gate: this is the test that turns red the
    day a session lands one English label."""

    def test_repo_passes(self):
        r = run_gate(ROOT)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])


if __name__ == "__main__":
    unittest.main()
