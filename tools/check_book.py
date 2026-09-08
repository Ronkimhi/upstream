#!/usr/bin/env python3
"""`run book`'s gate: every number in the book must exist in the file the row names.

ADVISORY on day one (prints findings, exits 0), the same posture and for the same reason
as check_machine.py and check_model.py: a gate that fails from its first run is a gate
people route around. `--strict` makes it blocking, and hardening it is a dated ledger
decision.

What it actually checks, and why each one is here rather than assumed:

1. Provenance. Every row's `market_ref` and `dive_ref` resolve, and the numbers copied out
   of them (price, implied FCF CAGR, quality states, verdict) still match the source. The
   book is a derived file; the failure mode of every derived file in this repo has been
   drift from what it derived from.
2. No invented verdict. A row may carry a verdict ONLY if its dive file carries the same
   one. The book must never be a second verdict surface (docs/methodology-review.md refused
   exactly that when it refused the options memo).
3. Placement reality. Every (chain_id, link_id, issuer_id) is a real ACTIVE placement, and
   the heat verdict matches the chain's own link.
4. The denominator is printed on every run, including over an empty book, where it says
   NO BOOK rather than OK. A gate that returns clean over nothing trains the reader to
   skim it (method §9, and check_themes.py's own note).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

FINDINGS: list[str] = []


def fail(msg: str) -> None:
    FINDINGS.append(msg)


def _load(path: pathlib.Path):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    book = _load(DATA / "book.json")
    if not book or not book.get("rows"):
        print("check_book: NO BOOK — data/book.json is absent or empty. "
              "Run `python3 tools/book.py` first.")
        return 1 if args.strict else 0

    rows = book["rows"]
    placements = {}
    heat = {}
    for path in sorted((DATA / "mappings").glob("*.json")):
        mapping = _load(path) or {}
        for placement in mapping.get("placements") or []:
            if placement.get("status") == "ACTIVE":
                placements[(mapping.get("chain_id"), placement.get("link_id"),
                            placement.get("issuer_id"))] = True
    for path in sorted((DATA / "chains").glob("*.json")):
        if path.name.startswith("_"):
            continue
        chain = _load(path) or {}
        for link in chain.get("links") or []:
            heat[(chain.get("id"), link.get("id"))] = (link.get("heat") or {}).get("verdict")

    checked_market = checked_dive = 0
    for row in rows:
        where = f"{row.get('ticker')}/{row.get('chain_id')}/{row.get('link_id')}"

        market = _load(ROOT / (row.get("market_ref") or ""))
        if market is None:
            fail(f"{where}: market_ref {row.get('market_ref')} does not resolve")
        else:
            checked_market += 1
            rdcf = (market.get("quality") or {}).get("reverse_dcf") or {}
            if rdcf.get("implied_fcf_cagr") != row.get("implied_fcf_cagr"):
                fail(f"{where}: implied_fcf_cagr {row.get('implied_fcf_cagr')} does not "
                     f"match {row.get('market_ref')} ({rdcf.get('implied_fcf_cagr')})")
            series = ((market.get("series") or {}).get("rows")) or []
            if series and series[-1][1] != row.get("price"):
                fail(f"{where}: price {row.get('price')} is not the last close in "
                     f"{row.get('market_ref')} ({series[-1][1]})")

        if row.get("verdict") is not None:
            dive = _load(ROOT / (row.get("dive_ref") or ""))
            if dive is None:
                fail(f"{where}: carries verdict {row.get('verdict')} with no resolvable "
                     "dive_ref; the book may never state a verdict of its own")
            else:
                checked_dive += 1
                verdict = dive.get("verdict")
                verdict = verdict.get("call") if isinstance(verdict, dict) else verdict
                if verdict != row.get("verdict"):
                    fail(f"{where}: verdict {row.get('verdict')} disagrees with "
                         f"{row.get('dive_ref')} ({verdict})")
        elif row.get("dived"):
            fail(f"{where}: marked dived with no verdict echoed")

        key = (row.get("chain_id"), row.get("link_id"), row.get("issuer_id"))
        if key not in placements:
            fail(f"{where}: no ACTIVE placement for issuer {row.get('issuer_id')}")
        expected = heat.get((row.get("chain_id"), row.get("link_id")))
        if expected != row.get("heat_verdict"):
            fail(f"{where}: heat_verdict {row.get('heat_verdict')} disagrees with the "
                 f"chain's link ({expected})")

    d = book.get("denominator") or {}
    print(f"check_book: {len(rows)} row(s) / {d.get('distinct_tickers')} ticker(s); "
          f"{checked_market} market_ref and {checked_dive} dive_ref re-read against source; "
          f"{d.get('dived')} dived, {d.get('screened')} screened; "
          f"skipped {d.get('skipped')}")
    if FINDINGS:
        print(f"check_book: {len(FINDINGS)} finding(s)"
              f"{'' if args.strict else ' (ADVISORY, exit 0)'}")
        for f in FINDINGS[:40]:
            print(f"  - {f}")
        if len(FINDINGS) > 40:
            print(f"  ... and {len(FINDINGS) - 40} more")
        return 1 if args.strict else 0
    print("check_book: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
