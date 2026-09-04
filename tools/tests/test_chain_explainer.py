#!/usr/bin/env python3
"""The explainer bar in `tools/check_chain.py` (check 11), watched refusing something.

Ron asked (2026-09-04) for a plain-Hebrew explanation on every link of every chain and a
Graph tab that draws the real graph. The prose lives in the chain file, so it rides the
same gate as the structure. The bar is mechanical on purpose: a numeric token that is not
a product code is refused outright, because an explainer that names no figure cannot
invent one; every draws_on path must resolve on the object it claims to paraphrase; and a
chain touched on or after EXPLAINER_GATE without explainers is refused while an untouched
chain is only reported. Each test here sees the gate refuse, which is what
`tools/check_machine.py` counts as falsifiability. The render half: an absent explainer
must reach the page as its honest absent state, never as `explainer || {}`.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_chain_citations import _link  # noqa: E402

_spec = importlib.util.spec_from_file_location("check_chain_mod", ROOT / "tools" / "check_chain.py")
check_chain = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_chain)

HE = "השלב הזה הופך חומר גלם למשהו שהשלב הבא צריך, ולכן הוא נמצא בתחילת השרשרת."
BOUND_DATE = "2026-09-05"      # on or after EXPLAINER_GATE, so the bar binds
RUN_DATE = "2026-09-06"        # a later day, so the ledger and preservation checks stand down
UNBOUND_DATE = "2026-08-01"


def explainer(**over):
    x = {"lang": "he", "what": HE, "players": HE, "why": HE, "bottleneck": HE, "hands_to": HE,
         "as_of": BOUND_DATE, "by": "test", "draws_on": ["role"]}
    x.update(over)
    return x


def chain_root(td: str, updated_at: str, with_links=True, with_chain=True, mutate=None) -> Path:
    root = Path(td)
    (root / "data" / "chains").mkdir(parents=True)
    (root / "data" / "signals").mkdir(parents=True)
    n = 8
    links = [_link(i, n, cited=True) for i in range(1, n + 1)]
    for l in links:
        l["role"] = f"Stage {l['id']} in one sentence."
        if with_links:
            l["explainer"] = explainer()
    chain = {"id": "test-chain", "created_at": UNBOUND_DATE, "updated_at": updated_at,
             "map_limitation": "This map cannot see privately held tier-3 suppliers.",
             "links": links, "scenarios": []}
    if with_chain:
        chain["explainer"] = {"lang": "he", "shape": HE, "thesis": HE, "as_of": BOUND_DATE,
                              "by": "test", "draws_on": ["map_limitation"]}
    if mutate:
        mutate(chain)
    (root / "data" / "chains" / "test-chain.json").write_text(json.dumps(chain, ensure_ascii=False))
    (root / "data" / "ledger.md").write_text("")
    return root


def run_gate(root: Path):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "check_chain.py"),
                           "--root", str(root), "--date", RUN_DATE],
                          capture_output=True, text=True)


class TestExplainerBar(unittest.TestCase):

    def test_a_complete_chain_passes_and_is_counted(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_gate(chain_root(td, BOUND_DATE))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("explainers 8 of 8 links, chain-level yes", r.stdout)

    def test_a_numeric_token_is_refused(self):
        def mut(c): c["links"][3]["explainer"]["what"] = HE + " והמחיר ירד 40 אחוז."
        with tempfile.TemporaryDirectory() as td:
            r = run_gate(chain_root(td, BOUND_DATE, mutate=mut))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("numeric token '40'", r.stdout)
        self.assertIn("test-chain/L4", r.stdout)

    def test_product_codes_are_not_figures(self):
        def mut(c): c["links"][0]["explainer"]["players"] = HE + " כמו H100 ו-HBM3E בתהליך 2nm."
        with tempfile.TemporaryDirectory() as td:
            r = run_gate(chain_root(td, BOUND_DATE, mutate=mut))
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_a_missing_subkey_is_refused(self):
        def mut(c): del c["links"][1]["explainer"]["hands_to"]
        with tempfile.TemporaryDirectory() as td:
            r = run_gate(chain_root(td, BOUND_DATE, mutate=mut))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("explainer.hands_to missing, short, or not Hebrew", r.stdout)

    def test_a_dash_is_refused(self):
        def mut(c): c["explainer"]["thesis"] = HE + " וזה — חשוב."
        with tempfile.TemporaryDirectory() as td:
            r = run_gate(chain_root(td, BOUND_DATE, mutate=mut))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("em or en dash", r.stdout)

    def test_draws_on_must_resolve(self):
        def mut(c): c["links"][2]["explainer"]["draws_on"] = ["heat.capture.rationale"]
        with tempfile.TemporaryDirectory() as td:
            r = run_gate(chain_root(td, BOUND_DATE, mutate=mut))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("draws_on 'heat.capture.rationale' resolves to nothing", r.stdout)

    def test_a_touched_chain_without_explainers_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_gate(chain_root(td, BOUND_DATE, with_links=False, with_chain=False))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("carries no explainer", r.stdout)
        self.assertIn("no chain-level explainer", r.stdout)

    def test_an_untouched_chain_is_reported_not_failed(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_gate(chain_root(td, UNBOUND_DATE, with_links=False, with_chain=False))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("explainers 0 of 8 links, chain-level no (not bound: updated_at 2026-08-01)", r.stdout)


class TestNumericToken(unittest.TestCase):

    def test_figures_refused_product_codes_allowed(self):
        rx = check_chain.NUMERIC_TOKEN
        for bad in ("40", "18%", "$143", "2026", "3-for-1", "ב-2026", "פי 2.5", "1,104"):
            self.assertIsNotNone(rx.search(bad), bad)
        for ok in ("H100", "HBM3E", "2nm", "GB200", "Gen3", "ארבעים אחוז"):
            self.assertIsNone(rx.search(ok), ok)

    def test_resolve_path_grammar(self):
        obj = {"role": "r", "evidence": [{"claim": "c1"}, {"claim": ""}],
               "links": [{"id": "a", "heat": {"capture": {"rationale": "why"}}}], "empty": ""}
        rp = check_chain.resolve_path
        self.assertEqual(rp(obj, "role"), "r")
        self.assertEqual(rp(obj, "evidence[0].claim"), "c1")
        self.assertEqual(rp(obj, "evidence[].claim"), ["c1"])
        self.assertEqual(rp(obj, "links[a].heat.capture.rationale"), "why")
        self.assertIsNone(rp(obj, "evidence[1].claim"))
        self.assertIsNone(rp(obj, "links[b].role"))
        self.assertIsNone(rp(obj, "empty"))
        self.assertIsNone(rp(obj, "nothing.here"))


class TestRenderAbsentState(unittest.TestCase):
    """check_render.py refuses `explainer || {}` in app.js and passes the real file."""

    def _root_with_js(self, td: str, extra_line: str) -> Path:
        root = Path(td)
        for rel in ("app/templates/app.js", "app/templates/shell.html", "app/build.py",
                    "tools/check_analyst.py"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / rel, root / rel)
        js = root / "app" / "templates" / "app.js"
        js.write_text(js.read_text() + "\n" + extra_line + "\n")
        return root

    def _run(self, root: Path):
        return subprocess.run([sys.executable, str(ROOT / "tools" / "check_render.py"),
                               "--root", str(root)], capture_output=True, text=True)

    def test_the_fallback_object_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run(self._root_with_js(td, "var bad = (l.explainer || {}).what;"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("absent explainer", r.stdout)

    def test_the_committed_renderer_passes(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._run(self._root_with_js(td, "// control: no change"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
