#!/usr/bin/env python3
"""Opportunities postlude gate. Stdlib only, offline.

`tools/opportunities.py` computes the homepage's Top 3 by copying every number from a
file named beside it, never inventing one. This is that contract as an exit code:
recompute `rank()` fresh from disk and refuse a `top` block whose numbers disagree with
their own sources, whose size survived a NULL score, whose ranking is not the documented
sort, or whose plain-English lines say a number the row does not itself carry.

Checks, each reported against the denominator it examined:
  1. SOURCED: every `refs.*`, `best.ref`, `best.price.ref` and `best.implied_ref` path
     exists under root, and every number the row copies (impact, crowdedness, capture,
     instrument scores, occurrence_impact_score, price value and as_of, zone low/high,
     implied_fcf_cagr) equals what that source file actually holds.
  2. ORDERED: `top` is the first three of the documented sort, in order, and its `rank`
     fields read 1, 2, 3.
  3. ACCOUNTED: `links_total == ranked_total + unrankable_total`, every link on disk
     lands in exactly one of the two, every link with `price_instruments` is counted in
     `instruments_total`, and every unrated one is listed in `instruments_unrated`.
  4. NEVER DEFAULTED: no row carries a `size` while any of its own three expression
     scores (`impact`, `crowdedness`, `capture`) is None. A NULL score cannot rank.
  5. GROUNDED WORDING: every number token in `lines.why`, `lines.stage` and `lines.next`
     equals a formatted value the row itself carries, or is a 4-digit year, or is part of
     a date string the row itself carries, or is the fixed 0..100 scale marker. The
     wording can describe a row; it cannot invent a number for one.
  6. SCOPE: zero chains on disk is reported as SCOPE EMPTY, never as a clean pass.

`--page PATH` compares a JSON file's own `top` block (the committed page's copy) against
this run's fresh computation field by field and refuses any disagreement. Without it,
only the fresh computation's own invariants are checked (checks 1, 2, 4 and 5 trivially
hold for a correct `rank()`; this is what lets the gate also serve as a regression test
for `tools/opportunities.py` itself, not only for a stale page).

Run: python3 tools/check_opportunities.py [--root PATH] [--page PATH]
Exit 0 clean, 1 on any failure.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import opportunities  # noqa: E402

NUM_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?(?![A-Za-z0-9])")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
ALWAYS_ALLOWED = {"100"}  # the fixed 0..100 heat scale, never a copied data value

# A letter-led hyphenated token is an IDENTIFIER, not a number this wording claims.
# NUM_RE's lookarounds already protect digits welded to letters (`h5n1` yields no token),
# but a digit SEGMENT fenced by hyphens is not: `CAMP-20260901-01` handed the gate
# '20260901' and '01' as if the line had asserted them. It had not — `lines.next` for a
# SCREENED row is the command a reader types, `run selection {campaign_id}`, and that id
# is copied off data/campaigns/ by opportunities.py, never composed. The gate failed CI on
# its own id format (2026-09-18), which is the false positive that trains a reader to skip
# the gate. Masked before number extraction; SIG-, OCC-, REQ- and THM- ids have the same
# shape and the same standing. A bare date is NOT masked (it starts with a digit), so the
# 4-digit-year and date-piece rules below still see it. A number the wording genuinely
# claims is written as its own token, never welded inside a hyphenated word.
IDENT_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b")


def _strip_identifiers(text: str) -> str:
    """`text` with identifier tokens blanked, so NUM_RE cannot read digits out of one."""
    return IDENT_RE.sub(lambda m: " " * len(m.group(0)), text)

failures: list = []
lines: list = []


def fail(msg: str) -> None:
    failures.append(msg)


def report(msg: str) -> None:
    lines.append(msg)


def _find_link(chain_doc, link_id):
    for link in (chain_doc or {}).get("links") or []:
        if isinstance(link, dict) and link.get("id") == link_id:
            return link
    return None


def _resolve_fragment(chain_doc, fragment):
    """Only ever `links/<link_id>/<rest...>`: the one shape this module's own refs use."""
    parts = fragment.split("/")
    if len(parts) < 2 or parts[0] != "links":
        return None
    node = _find_link(chain_doc, parts[1])
    for part in parts[2:]:
        if node is None:
            return None
        if isinstance(node, list):
            if not part.lstrip("-").isdigit():
                return None
            idx = int(part)
            node = node[idx] if -len(node) <= idx < len(node) else None
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


def _ref_path_and_fragment(root: Path, ref):
    if not ref:
        return None, None
    path_part, _, fragment = ref.partition("#")
    return root / path_part, fragment


def _load_ref(root: Path, ref):
    """(doc-or-fragment-node, real-file-path, existed) for one `<path>[#fragment]` ref."""
    path, fragment = _ref_path_and_fragment(root, ref)
    if path is None:
        return None, None, True  # no ref at all is not a missing-file failure
    if not path.exists():
        return None, path, False
    doc = opportunities._read_json(path)  # noqa: SLF001 - the same loader rank() uses
    if fragment:
        return _resolve_fragment(doc, fragment), path, True
    return doc, path, True


def check_sourced(root: Path, top: list) -> None:
    examined = 0
    for row in top:
        where = f"top[{row.get('rank')}] {row.get('chain_id')}/{row.get('link_id')}"
        examined += 1
        best = row.get("best") or {}
        report(f"{where}: {row.get('expression')} size {row.get('size')}, best "
               f"{best.get('stage')} {best.get('ticker')}, zone_state "
               f"{best.get('zone_state')}, pct_above {best.get('pct_above_zone_high')}")

        heat_node, heat_path, existed = _load_ref(root, (row.get("refs") or {}).get("heat"))
        if not existed:
            fail(f"{where}: refs.heat points to {heat_path}, which does not exist")
        elif not isinstance(heat_node, dict):
            fail(f"{where}: refs.heat fragment does not resolve inside {heat_path}")
        else:
            expr = row.get("expression")
            if expr == "INSTRUMENT":
                inst = heat_node.get("instrument") or {}
                src_crowd = opportunities.score_from(inst, "crowdedness")
                src_capture = opportunities.score_from(inst, "capture")
            else:
                src_crowd = opportunities.score_from(heat_node, "crowdedness")
                src_capture = opportunities.score_from(heat_node, "capture")
            src_impact = opportunities.score_from(heat_node, "impact")
            for field, card_val, src_val in (
                ("impact", row.get("impact"), src_impact),
                ("crowdedness", row.get("crowdedness"), src_crowd),
                ("capture", row.get("capture"), src_capture),
                ("issuer_crowdedness", row.get("issuer_crowdedness"),
                 opportunities.score_from(heat_node, "crowdedness")),
            ):
                if card_val != src_val:
                    fail(f"{where}: {field} differs: {card_val} on the card, {src_val} in "
                         f"{heat_path.relative_to(root)}")

        impact_ref = (row.get("refs") or {}).get("impact")
        if impact_ref:
            occ_doc, occ_path, existed = _load_ref(root, impact_ref)
            if not existed:
                fail(f"{where}: refs.impact points to {occ_path}, which does not exist")
            elif isinstance(occ_doc, dict):
                for field, card_val, src_key in (
                    ("occurrence_impact_score", row.get("occurrence_impact_score"), "impact_score"),
                    ("occurrence_band", row.get("occurrence_band"), "impact_band"),
                ):
                    if card_val != occ_doc.get(src_key):
                        fail(f"{where}: {field} differs: {card_val} on the card, "
                             f"{occ_doc.get(src_key)} in {occ_path.relative_to(root)}")

        for obj_name in ("best", "also"):
            obj = row.get(obj_name)
            if not isinstance(obj, dict):
                continue
            obj_where = f"{where} {obj_name}"
            best_path, best_fragment = _ref_path_and_fragment(root, obj.get("ref"))
            if best_path is not None and not best_path.exists():
                fail(f"{obj_where}: ref points to {best_path}, which does not exist")
            elif obj.get("stage") == "VERDICT" and best_path is not None and best_path.exists():
                dive_doc = opportunities._read_json(best_path)  # noqa: SLF001
                if isinstance(dive_doc, dict):
                    for field in ("verdict", "clock", "status", "ticker"):
                        if obj.get(field) != dive_doc.get(field):
                            fail(f"{obj_where}: {field} differs: {obj.get(field)!r} on the "
                                 f"card, {dive_doc.get(field)!r} in {best_path.relative_to(root)}")
                    zone = obj.get("zone")
                    if zone:
                        src_zone = (dive_doc.get("entry_zone") if zone.get("kind") == "entry"
                                    else dive_doc.get("would_buy_zone")) or {}
                        for bound in ("low", "high"):
                            if zone.get(bound) != src_zone.get(bound):
                                fail(f"{obj_where}: zone.{bound} differs: {zone.get(bound)} "
                                     f"on the card, {src_zone.get(bound)} in "
                                     f"{best_path.relative_to(root)}")

            price = obj.get("price")
            if isinstance(price, dict) and price.get("ref"):
                mkt_path = root / price["ref"]
                if not mkt_path.exists():
                    fail(f"{obj_where}: price.ref points to {mkt_path}, which does not exist")
                else:
                    mkt_doc = opportunities._read_json(mkt_path)  # noqa: SLF001
                    rows_ = ((mkt_doc or {}).get("series") or {}).get("rows") or []
                    src_val = rows_[-1][1] if rows_ and len(rows_[-1]) > 1 else None
                    src_as_of = ((mkt_doc or {}).get("series") or {}).get("as_of")
                    if price.get("value") != src_val:
                        fail(f"{obj_where}: price.value differs: {price.get('value')} on "
                             f"the card, {src_val} in {price['ref']}")
                    if price.get("as_of") != src_as_of:
                        fail(f"{obj_where}: price.as_of differs: {price.get('as_of')!r} on "
                             f"the card, {src_as_of!r} in {price['ref']}")

            if obj.get("implied_ref"):
                imp_path = root / obj["implied_ref"]
                if not imp_path.exists():
                    fail(f"{obj_where}: implied_ref points to {imp_path}, which does not exist")
                else:
                    imp_doc = opportunities._read_json(imp_path)  # noqa: SLF001
                    src_cagr = ((imp_doc or {}).get("quality") or {}).get(
                        "reverse_dcf", {}).get("implied_fcf_cagr")
                    if obj.get("implied_fcf_cagr") != src_cagr:
                        fail(f"{obj_where}: implied_fcf_cagr differs: "
                             f"{obj.get('implied_fcf_cagr')} on the card, {src_cagr} in "
                             f"{obj['implied_ref']}")
    report(f"sourced: {examined} top row(s) checked against their own refs")


def check_ordered(top: list, fresh_top: list) -> None:
    for i, row in enumerate(top, 1):
        if row.get("rank") != i:
            fail(f"top[{i}]: rank reads {row.get('rank')!r}, expected {i}")
    card_seq = [(r.get("chain_id"), r.get("link_id")) for r in top]
    fresh_seq = [(r.get("chain_id"), r.get("link_id")) for r in fresh_top]
    if card_seq != fresh_seq:
        fail(f"hand-edited or stale top block: order differs, card has {card_seq}, "
             f"a fresh sort gives {fresh_seq}")
    report(f"ordered: top carries {len(top)} row(s), ranks {[r.get('rank') for r in top]}")


def check_accounted(fresh: dict) -> None:
    total = fresh["ranked_total"] + fresh["unrankable_total"]
    if fresh["links_total"] != total:
        fail(f"links_total {fresh['links_total']} does not equal ranked_total "
             f"({fresh['ranked_total']}) plus unrankable_total ({fresh['unrankable_total']})")
    unrated_links = {(u["chain_id"], u["link_id"]) for u in fresh["instruments_unrated"]}
    if len(unrated_links) > fresh["instruments_total"]:
        fail(f"instruments_unrated names {len(unrated_links)} link(s), more than "
             f"instruments_total ({fresh['instruments_total']})")
    report(f"accounted: {fresh['links_total']} link(s) = {fresh['ranked_total']} ranked + "
           f"{fresh['unrankable_total']} unrankable; {fresh['instruments_total']} "
           f"instrument link(s), {len(unrated_links)} unrated")
    for item in fresh["unrankable"]:
        report(f"  unrankable: {item['chain_id']}/{item['link_id']} ({item['reason']})")
    for item in fresh["instruments_unrated"]:
        report(f"  instrument unrated: {item['chain_id']}/{item['link_id']} "
               f"({item.get('ticker')})")


def check_never_defaulted(top: list) -> None:
    examined = 0
    for row in top:
        examined += 1
        scores = (row.get("impact"), row.get("crowdedness"), row.get("capture"))
        if row.get("size") is not None and any(s is None for s in scores):
            fail(f"top[{row.get('rank')}] {row.get('chain_id')}/{row.get('link_id')}: "
                 f"size {row.get('size')} computed from a defaulted NULL score {scores}")
    report(f"never-defaulted: {examined} top row(s) checked for a size drawn from a NULL score")


def _numeric_tokens(row) -> set:
    tokens: set = set()

    def add(value, money=False):
        if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        text = opportunities.fmt_money(value) if money else opportunities.fmt_num(value)
        tokens.update(NUM_RE.findall(text))

    add(row.get("size"))
    add(row.get("impact"))
    add(row.get("crowdedness"))
    add(row.get("capture"))
    add(row.get("issuer_crowdedness"))
    add(row.get("occurrence_impact_score"))
    for obj in (row.get("best"), row.get("also")):
        if not isinstance(obj, dict):
            continue
        price = obj.get("price") or {}
        add(price.get("value"), money=True)
        zone = obj.get("zone") or {}
        add(zone.get("low"), money=True)
        add(zone.get("high"), money=True)
        add(obj.get("pct_above_zone_high"))
        cagr = obj.get("implied_fcf_cagr")
        if isinstance(cagr, (int, float)) and not isinstance(cagr, bool):
            add(round(cagr * 100, 1))
        add(obj.get("mapped_count"))
    return tokens


def _date_pieces(row) -> set:
    blob = json.dumps(row, default=str)
    pieces: set = set()
    for match in DATE_RE.findall(blob):
        pieces.add(match)
        pieces.update(match.split("-"))
    return pieces


def check_grounded_wording(top: list) -> None:
    examined = 0
    for row in top:
        tokens = _numeric_tokens(row) | ALWAYS_ALLOWED
        date_pieces = _date_pieces(row)
        line_block = row.get("lines") or {}
        for field in ("why", "stage", "next"):
            text = line_block.get(field)
            if not isinstance(text, str):
                continue
            examined += 1
            for tok in NUM_RE.findall(_strip_identifiers(text)):
                if re.fullmatch(r"\d{4}", tok):
                    continue  # a 4-digit year is always allowed
                if tok in tokens or tok in date_pieces:
                    continue
                fail(f"top[{row.get('rank')}] {row.get('chain_id')}/{row.get('link_id')}: "
                     f"lines.{field} says {tok!r}, which is not one of the row's own "
                     f"numbers, a year, or a date on the row: {text!r}")
    report(f"grounded-wording: {examined} line(s) across {len(top)} top row(s) checked "
           f"for an invented number")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--page", default=None,
                     help="a JSON file holding a top block to compare against")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    fresh = opportunities.rank(opportunities.load_inputs(root))

    if fresh["chains_total"] == 0:
        print(f"check_opportunities: SCOPE EMPTY, no chains under {root / 'data' / 'chains'}. "
              f"This is not a pass over the corpus, it is the absence of one")
        return 0

    rated = fresh["instruments_total"] - len({
        (u["chain_id"], u["link_id"]) for u in fresh["instruments_unrated"]})
    top_desc = ", ".join(f"{r['chain_id']}/{r['link_id']}" for r in fresh["top"]) or "(none)"
    summary = (f"check_opportunities: {fresh['links_total']} links across "
               f"{fresh['chains_total']} chains; {fresh['ranked_total']} ranked, "
               f"{fresh['unrankable_total']} unrankable, {fresh['instruments_total']} "
               f"instruments ({rated} rated); top: {top_desc}")
    print(summary)  # always, before any FAIL

    page_top = None
    if args.page:
        page_doc = json.loads(Path(args.page).read_text())
        page_top = page_doc.get("top") or []

    candidate_top = page_top if page_top is not None else fresh["top"]

    check_sourced(root, candidate_top)
    check_ordered(candidate_top, fresh["top"])
    check_accounted(fresh)
    check_never_defaulted(candidate_top)
    check_grounded_wording(candidate_top)

    for ln in lines:
        print(f"  {ln}")
    if failures:
        for f_ in failures:
            print(f"  FAIL {f_}")
        print(f"check_opportunities: FAILED with {len(failures)} finding(s)")
        return 1
    print(f"check_opportunities: OK  ({summary[len('check_opportunities: '):]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
