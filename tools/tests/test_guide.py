"""The onboarding guide ships twice from one fragment (app/templates/guide.html): inlined
into app/index.html for the Guide tab, and wrapped into the standalone app/guide.html a
friend gets as a link. These tests hold the three things that would break silently:
a fragment that can terminate the template or script it is inlined into, a page that
lost the fragment, and a committed guide.html that no longer matches its template."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "app"))

import build  # noqa: E402


class GuideFragment(unittest.TestCase):
    def test_fragment_cannot_terminate_its_host(self):
        text = (ROOT / "app" / "templates" / "guide.html").read_text()
        for bad in ("</script", "</template", "{{"):
            self.assertNotIn(bad, text, f"guide.html must not contain {bad!r}")

    def test_read_guide_fragment_refuses_a_terminator(self):
        for bad in ("</script>", "</template>", "{{APP_JS}}"):
            with self.assertRaises(ValueError):
                build.read_guide_fragment(text=f"<div>{bad}</div>")

    def test_standalone_page_carries_the_fragment(self):
        page = build.assemble_guide_html()
        fragment = build.read_guide_fragment()
        self.assertIn(fragment, page)
        self.assertIn("<title>Upstream Guide</title>", page)
        self.assertNotIn("{{", page, "an unsubstituted placeholder reached the page")

    def test_committed_guide_matches_template(self):
        out = ROOT / "app" / "guide.html"
        self.assertTrue(out.exists(), "app/guide.html has not been built; run app/build.py")
        self.assertEqual(out.read_text(), build.assemble_guide_html(),
                         "app/guide.html is stale: run app/build.py and commit the result")

    def test_dashboard_shell_hosts_the_guide_template(self):
        shell = (ROOT / "app" / "templates" / "shell.html").read_text()
        self.assertIn('<template id="upstream-guide">{{GUIDE_HTML}}</template>', shell)
        js = (ROOT / "app" / "templates" / "app.js").read_text()
        self.assertIn('getElementById("upstream-guide")', js)
        self.assertIn('p[0] === "guide"', js)


if __name__ == "__main__":
    unittest.main()
