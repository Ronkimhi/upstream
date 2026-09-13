#!/usr/bin/env python3
"""The price test in `tools/check_chain.py` (check 12), watched refusing something.

Method section 4's 2026-09-13 amendment: every HIGH or CHOKE_POINT link either names the
price it sets, with a unit, a publisher and dated evidence, or says in `basis` why it
sets none; the same link records its instrument search (what was queried, what was
found); and every instrument anywhere on the chain, required link or not, is a real,
identified, cited listing that actually holds what it claims to. A chain the amendment
never reached (created before the gate, untouched today) is the seed corpus: its gaps
are warnings, not failures, the same ratchet the citation and explainer bars used before
their own backfills. These tests exercise `price_test_failures` only through the gate's
subprocess entry point, the same idiom `test_chain_citations.py` uses for the citation
bar, so each refusal test is proof the gate can actually refuse something.

Fixture note: the strict fixture's `created_at` is 2026-09-14 (on or after
PRICE_TEST_GATE, and the exact day `--date` names as today, so the price test binds and
the run-day ledger check applies) while its `updated_at` stays 2026-08-01, well before
the unrelated explainer bar's own gate (2026-09-04, check 11). That keeps this file
isolated to the one check it names, the same separation `test_chain_citations.py` and
`test_chain_explainer.py` already keep from each other; a chain touched today in the
explainer bar's sense would need a full Hebrew explainer block on every link purely to
clear a check this file has nothing to do with.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_chain_citations import _link  # noqa: E402

STRICT_CREATED = "2026-09-14"   # on or after PRICE_TEST_GATE: the price test binds
STRICT_UPDATED = "2026-08-01"   # well before EXPLAINER_GATE: check 11 stays out of this file
SEED_DATE = "2026-08-30"        # before PRICE_TEST_GATE, no changelog today: the seed corpus


def _verified_evidence(excerpt: str) -> list:
    return [{
        "source_name": "Fund fact sheet",
        "source_date": "2026-09-01",
        "url": "https://example.com/aetf-factsheet",
        "source_excerpt": excerpt,
        "tag": "VERIFIED",
    }]


def complete_instrument(*, holds="physical alpha inventory",
                         excerpt="The fund holds physical alpha inventory in segregated vaults.") -> dict:
    return {
        "ticker": "AETF",
        "exchange": "NYSEARCA",
        "kind": "ETF",
        "holds": holds,
        "tenor": None,
        "expense_ratio": 0.0095,
        "aum": {"value": 500000000, "as_of": "2026-09-01"},
        "identity_evidence": _verified_evidence(excerpt),
    }


def choke_fields() -> dict:
    """A complete, well-formed CHOKE_POINT link: a priced scarce_price, a FOUND
    instrument_search, and the one instrument that search found."""
    return {
        "scarce_price": {
            "name": "Alpha benchmark index",
            "unit": "USD per unit",
            "published_by": "Alpha Exchange",
            "evidence": [{
                "source_date": "2026-09-10",
                "url": "https://example.com/alpha-price",
                "claim": "The benchmark price for the alpha chokepoint is published daily.",
            }],
        },
        "instrument_search": {
            "searched_at": "2026-09-14",
            "queries": ["alpha benchmark etf", "alpha tracker fund"],
            "result": "FOUND",
            "boundary": "",
        },
        "price_instruments": [complete_instrument()],
    }


def high_fields() -> dict:
    """A complete, well-formed HIGH link that sets no price at all: scarce_price
    answers with a null name and a basis, and the search is NOT_APPLICABLE."""
    return {
        "scarce_price": {
            "name": None,
            "basis": "No public benchmark prices this specific bottleneck; it is a "
                     "private bilateral allocation.",
        },
        "instrument_search": {
            "searched_at": "2026-09-14",
            "queries": [],
            "result": "NOT_APPLICABLE",
            "boundary": "No instrument can express a bottleneck with no market price to track.",
        },
        "price_instruments": [],
    }


def _base_links(n: int = 8) -> list:
    """L1 CHOKE_POINT (complete), L2 HIGH (complete, prices nothing), L3 LOW (nothing,
    LOW is not required), L4..L8 ROUTABLE filler, cited exactly like
    `test_chain_citations._link` builds them."""
    links = [_link(i, n, True) for i in range(1, n + 1)]
    links[0]["bottleneck"] = {"criticality": "CHOKE_POINT"}
    links[0].update(choke_fields())
    links[1]["bottleneck"] = {"criticality": "HIGH"}
    links[1].update(high_fields())
    links[2]["bottleneck"] = {"criticality": "LOW"}
    return links


def chain_root(td: str, *, created: str = STRICT_CREATED, updated: str = None, mutate=None) -> Path:
    """A repo root holding one otherwise-clean, price-test-complete chain. `mutate`, when
    given, is called on the chain dict just before it is written, the same hook
    `test_chain_explainer.py` uses to introduce exactly one defect per test."""
    root = Path(td)
    (root / "data" / "chains").mkdir(parents=True)
    (root / "data" / "signals").mkdir(parents=True)
    chain = {
        "id": "alpha",
        "created_at": created,
        "updated_at": updated if updated is not None else created,
        "map_limitation": "This map cannot see privately held tier-3 suppliers.",
        "links": _base_links(),
        "scenarios": [],
    }
    if mutate:
        mutate(chain)
    (root / "data" / "chains" / "alpha.json").write_text(json.dumps(chain))
    # A run-day ledger line, content-complete (names the archetypes and the click
    # queue), so the strict fixture's created_at matching --date never fails check 10
    # for reasons that have nothing to do with the price test.
    (root / "data" / "ledger.md").write_text(
        f"{created} 12:00Z | RUN | run chain alpha | wrote: data/chains/alpha.json | "
        f"result: chain built for the price test fixture, archetypes: none applied, "
        f"click queue: empty | health: n/a | artifact: skipped(test) | model: test\n"
    )
    return root


def git_commit(root: Path) -> None:
    """One commit of the whole tree, so `git show HEAD:...` has something to answer."""
    g = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(g + ["init", "-q"], check=True, capture_output=True)
    subprocess.run(g + ["add", "-A"], check=True, capture_output=True)
    subprocess.run(g + ["commit", "-q", "-m", "seed"], check=True, capture_output=True)


def run_gate(root: Path, date: str = STRICT_CREATED):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_chain.py"),
         "--root", str(root), "--date", date],
        capture_output=True, text=True)


class TestPriceTest(unittest.TestCase):

    def test_a_complete_price_test_passes(self):
        """The other half of every refusal test below: the price test must not fail a
        chain that clears it, or the refusals would be satisfied by a gate that simply
        always refuses."""
        with tempfile.TemporaryDirectory() as td:
            root = chain_root(td, updated=STRICT_UPDATED)
            r = run_gate(root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(
            "price test: 2 links required (HIGH/CHOKE_POINT), 2 answered, "
            "1 instrument(s), 0 NONE_FOUND, 0 warning(s) on seed chains", r.stdout)

    def test_the_gate_refuses_a_required_link_without_scarce_price(self):
        def mut(c):
            del c["links"][0]["scarce_price"]
        with tempfile.TemporaryDirectory() as td:
            root = chain_root(td, updated=STRICT_UPDATED, mutate=mut)
            r = run_gate(root)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("L1: scarce_price missing on a HIGH/CHOKE_POINT link", r.stdout)

    def test_the_gate_refuses_found_with_an_empty_instrument_list(self):
        def mut(c):
            c["links"][0]["price_instruments"] = []
        with tempfile.TemporaryDirectory() as td:
            root = chain_root(td, updated=STRICT_UPDATED, mutate=mut)
            r = run_gate(root)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(
            "L1: instrument_search.result FOUND with an empty price_instruments list",
            r.stdout)

    def test_the_gate_refuses_holds_absent_from_the_excerpt(self):
        def mut(c):
            c["links"][0]["price_instruments"][0]["holds"] = "an entirely different holding"
        with tempfile.TemporaryDirectory() as td:
            root = chain_root(td, updated=STRICT_UPDATED, mutate=mut)
            r = run_gate(root)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(
            "L1: price_instruments[AETF].holds does not appear in any identity_evidence "
            "source_excerpt", r.stdout)

    def test_the_gate_refuses_a_dropped_instrument_against_head(self):
        """Commit the complete chain, then remove the instrument on disk without a
        second commit: git HEAD still holds it, so preservation must refuse the drop."""
        with tempfile.TemporaryDirectory() as td:
            root = chain_root(td, updated=STRICT_UPDATED)
            git_commit(root)
            path = root / "data" / "chains" / "alpha.json"
            chain = json.loads(path.read_text())
            chain["links"][0]["price_instruments"] = []
            path.write_text(json.dumps(chain))
            r = run_gate(root)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(
            "L1: price_instruments[AETF] present at git HEAD is missing now", r.stdout)

    def test_a_seed_chain_warns_and_exits_zero(self):
        """The same missing-scarce_price defect as the refusal test above, but on a
        chain the amendment never reached: reported as a warning, and the gate still
        exits clean."""
        def mut(c):
            del c["links"][0]["scarce_price"]
        with tempfile.TemporaryDirectory() as td:
            root = chain_root(td, created=SEED_DATE, mutate=mut)
            r = run_gate(root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("scarce_price missing on a HIGH/CHOKE_POINT link", r.stdout)
        self.assertIn("WARNING: price test amendment 2026-09-13, seed chain", r.stdout)
        self.assertIn("1 warning(s) on seed chains", r.stdout)
        self.assertNotIn("FAIL", r.stdout)

    def test_the_gate_always_prints_its_denominator(self):
        """The denominator line is printed every run, before any FAIL line, exactly
        like the corpus line above it. Proven here on a run that does fail."""
        def mut(c):
            del c["links"][0]["scarce_price"]
        with tempfile.TemporaryDirectory() as td:
            root = chain_root(td, updated=STRICT_UPDATED, mutate=mut)
            r = run_gate(root)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("price test:", r.stdout)
        self.assertIn("links required (HIGH/CHOKE_POINT)", r.stdout)
        self.assertLess(r.stdout.index("price test:"), r.stdout.index("FAIL"))


if __name__ == "__main__":
    unittest.main()
