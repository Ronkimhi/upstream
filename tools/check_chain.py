#!/usr/bin/env python3
"""Chain postlude gate. Stdlib only, offline.

`CLAUDE.md`'s `run chain` row states what a chain build must verify before it commits, and
`docs/method.md` §4 states the structure it must have. Both were prose, so both could only
be honoured by memory. This is the same list as an exit code.

Checks, each reported with the denominator it examined (Rule 21):
  1. 8-15 links, unique ids, positions a permutation of 1..n
  2. upstream first: for every edge A upstream_of B, position(A) < position(B)
  3. no orphans, one connected component, acyclic
  4. edge reciprocity in BOTH directions
  5. map_limitation present, non-empty, and not shared verbatim with another chain
  6. example_tickers on every link; thin choke points named; non-US share with its denominator
  7. signal agreement: signal.chain_id == chain.id, signal.status == CHAINED, no two chains
     claiming one signal_id
  8. the citation bar: every link carries dated evidence (WARNING until the seed corpus is
     backfilled, then an error; see docs/method.md §4)
  9. PRESERVATION: against this file's own git HEAD version, no link lost its heat and no
     scenario vanished. This is the hazard the cartographer can cause and nothing else catches
 10. the ledger line names the archetypes applied and what the click queue held

Scope: checks 9 and 10 only bind on a day that actually wrote a chain. On a day with no
chain run the gate says NOT RUN TODAY for those, rather than reporting a clean pass over
nothing, and still runs the structural checks over the whole corpus.

Run: python3 tools/check_chain.py [--date YYYY-MM-DD] [--root PATH] [--warn-citations]
Exit 0 clean, 1 on any failure.
"""
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

# Ledger-content rules bind runs from this date forward, never retroactively. Same pattern
# as OCCURRENCE_GATE in tools/validate.py: a rule invented today cannot fail yesterday's work.
LEDGER_RULE_GATE = "2026-08-30"

failures: list[str] = []
lines: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def report(msg: str) -> None:
    lines.append(msg)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


def head_version(root: Path, rel: str):
    """The file as of git HEAD, or None when unavailable. Never fails the run on git trouble."""
    try:
        out = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{rel}"],
                             capture_output=True, text=True, timeout=15)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout)
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    argv = sys.argv[1:]
    root = Path(argv[argv.index("--root") + 1]).resolve() if "--root" in argv \
        else Path(__file__).resolve().parent.parent
    today = argv[argv.index("--date") + 1] if "--date" in argv \
        else datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    # The citation bar shipped as a warning because 36 of 36 seed links were uncited and a
    # gate that fails from its first run is a gate people route around. The backfill landed
    # 2026-08-30 and took the corpus to 0 uncited, so the bar is now an error by default:
    # a link is a claim that a stage exists and an edge is a claim that one stage feeds
    # another, and method section 4 requires each to be dated and sourced. `--warn-citations`
    # is the deliberate, dated escape hatch for a genuine backfill run, not a way past the bar.
    strict = "--warn-citations" not in argv
    data = root / "data"

    chains = []
    cdir = data / "chains"
    if cdir.exists():
        for f in sorted(cdir.glob("*.json")):
            if f.name.startswith("_"):
                continue
            c = read_json(f)
            if isinstance(c, dict):
                c["_rel"] = str(f.relative_to(root))
                chains.append(c)
    if not chains:
        print("check_chain: no chains on disk; nothing to gate.")
        return 0

    touched = [c for c in chains
               if today in (str(c.get("updated_at") or "") + str(c.get("created_at") or ""))]

    total_links = sum(len(c.get("links") or []) for c in chains)
    uncited_all, limitations, signal_claims = 0, {}, {}

    for c in chains:
        cid = c.get("id")
        links = c.get("links") or []
        n = len(links)
        pos = {l.get("id"): l.get("position") for l in links}

        # 1. size, ids, positions
        if not 8 <= n <= 15:
            fail(f"{cid}: {n} links, outside method §4's 8 to 15")
        if len(pos) != n:
            fail(f"{cid}: duplicate link ids")
        if sorted(v for v in pos.values() if isinstance(v, int)) != list(range(1, n + 1)):
            fail(f"{cid}: positions are not a permutation of 1..{n}")
        else:
            # 2. direction
            for l in links:
                for b in l.get("upstream_of", []):
                    if b in pos and pos[l["id"]] >= pos[b]:
                        fail(f"{cid}: {l['id']} (pos {pos[l['id']]}) is upstream_of {b} "
                             f"(pos {pos[b]}) but does not precede it (method §4, upstream first)")

        # 3. orphans, connectivity, cycles
        orphans = [l.get("id") for l in links
                   if not (l.get("upstream_of") or l.get("downstream_of"))]
        if orphans:
            fail(f"{cid}: {len(orphans)} of {n} links are orphans: {', '.join(map(str, orphans))}")
        adj = {l["id"]: set() for l in links}
        for l in links:
            for b in list(l.get("upstream_of", [])) + list(l.get("downstream_of", [])):
                if b in adj:
                    adj[l["id"]].add(b)
                    adj[b].add(l["id"])
        seen, stack = set(), [links[0]["id"]] if links else []
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adj.get(cur, set()) - seen)
        if len(seen) != n:
            fail(f"{cid}: not one connected component, {len(seen)} of {n} links reachable; "
                 f"stranded {sorted(set(adj) - seen)}")
        by_id = {l["id"]: l for l in links}
        colour: dict = {}

        def cyclic(node):
            colour[node] = 1
            for nxt in by_id.get(node, {}).get("upstream_of", []):
                if colour.get(nxt) == 1:
                    return nxt
                if nxt in by_id and colour.get(nxt) is None and cyclic(nxt):
                    return nxt
            colour[node] = 2
            return None
        for l in links:
            if colour.get(l["id"]) is None and cyclic(l["id"]):
                fail(f"{cid}: the chain has a cycle; a chain is a process, one thing leads to another")
                break

        # 4. reciprocity, both ways
        one_way = []
        for l in links:
            for b in l.get("upstream_of", []):
                if b in by_id and l["id"] not in (by_id[b].get("downstream_of") or []):
                    one_way.append(f"{l['id']} upstream_of {b}")
            for b in l.get("downstream_of", []):
                if b in by_id and l["id"] not in (by_id[b].get("upstream_of") or []):
                    one_way.append(f"{l['id']} downstream_of {b}")
        if one_way:
            fail(f"{cid}: {len(one_way)} edge(s) mirrored on one side only: {'; '.join(one_way)}")

        # 5. map_limitation
        ml = (c.get("map_limitation") or "").strip()
        if not ml:
            fail(f"{cid}: map_limitation is empty (method §4: every map states what it "
                 f"structurally cannot see)")
        elif ml in limitations:
            fail(f"{cid}: map_limitation is byte-identical to {limitations[ml]}'s. A limitation "
                 f"names what THIS map cannot see")
        else:
            limitations[ml] = cid

        # 6. ticker coverage
        for l in links:
            if "example_tickers" not in l:
                fail(f"{cid}: link {l.get('id')} has no example_tickers key (method §4; an "
                     f"empty list is allowed and is itself a finding, a missing key is not)")
        thin = [l.get("id") for l in links
                if (l.get("bottleneck") or {}).get("criticality") == "CHOKE_POINT"
                and len(l.get("example_tickers") or []) <= 1]
        tk = [t for l in links for t in (l.get("example_tickers") or [])]
        non_us = [t for t in tk if "." in t]
        report(f"{cid}: {n} links, {len(tk)} tickers, non-US {len(non_us)}/{len(tk)}"
               + (f" · thin choke points: {', '.join(map(str, thin))}" if thin else ""))

        # 7. signal agreement
        sid = c.get("signal_id")
        if sid:
            if sid in signal_claims:
                fail(f"{cid}: signal {sid} is already claimed by {signal_claims[sid]}")
            signal_claims[sid] = cid
            sig = read_json(data / "signals" / f"{sid}.json")
            if sig is None:
                fail(f"{cid}: signal_id {sid} has no signal file")
            else:
                if sig.get("chain_id") != cid:
                    fail(f"{cid}: signal {sid}.chain_id is {sig.get('chain_id')!r}, not {cid!r}")
                if sig.get("status") != "CHAINED":
                    fail(f"{cid}: signal {sid} is {sig.get('status')!r}, not CHAINED. A chain "
                         f"exists for it, so the handoff did not complete")

        # 8. the citation bar
        uncited = [l.get("id") for l in links if not l.get("evidence")]
        uncited_all += len(uncited)
        if uncited:
            msg = (f"{cid}: {len(uncited)} of {n} links carry no dated evidence. A link is a "
                   f"claim that a stage exists and an edge is a claim that one stage feeds "
                   f"another: {', '.join(map(str, uncited))}")
            (fail if strict else report)(msg + ("" if strict else "  [WARNING: --warn-citations, backfill in progress]"))

        # 9. preservation, the hazard
        prev = head_version(root, c["_rel"])
        if prev is None:
            report(f"{cid}: no git HEAD version available, preservation check skipped (indirect)")
        else:
            lost_heat = [l.get("id") for l in (prev.get("links") or [])
                         if l.get("heat") and not (by_id.get(l.get("id")) or {}).get("heat")]
            if lost_heat:
                fail(f"{cid}: {len(lost_heat)} link(s) LOST their heat block versus git HEAD: "
                     f"{', '.join(map(str, lost_heat))}. A chain rebuild amends links in place; "
                     f"heat and scenarios belong to the analyst stage")
            prev_scen = {s.get("id") for s in (prev.get("scenarios") or [])}
            now_scen = {s.get("id") for s in (c.get("scenarios") or [])}
            gone = prev_scen - now_scen
            if gone:
                fail(f"{cid}: scenario(s) {sorted(gone)} vanished versus git HEAD")
            report(f"{cid}: preservation checked against HEAD "
                   f"({len(prev.get('links') or [])} prior links, {len(prev_scen)} prior scenarios)")

    report(f"corpus: {len(chains)} chains, {total_links} links examined, "
           f"{uncited_all} uncited ({'error' if strict else 'warning'} mode)")

    # 10. the ledger line
    ledger = (data / "ledger.md").read_text() if (data / "ledger.md").exists() else ""
    todays = [ln for ln in ledger.splitlines() if ln.startswith(today)]
    chain_lines = [ln for ln in todays if re.search(r"\|\s*(RUN|AMEND)\s*\|.*\b(run chain|refresh data/chains)", ln)]
    if not touched:
        report(f"no chain written today ({today}); the run-line checks did not apply")
    elif not chain_lines:
        fail(f"{len(touched)} chain(s) updated today with no matching ledger line for {today}")
    elif today < LEDGER_RULE_GATE:
        report(f"ledger: {len(chain_lines)} chain line(s) for {today}; content rules take "
               f"effect {LEDGER_RULE_GATE} and are not applied retroactively")
    else:
        for ln in chain_lines:
            if not re.search(r"archetype", ln, re.I):
                fail("chain ledger line does not name the archetypes applied (or that none "
                     "were): a run that learned something and wrote it nowhere is a "
                     "conversation, not a finding")
            if not re.search(r"(click )?queue", ln, re.I):
                fail("chain ledger line does not say what the click queue held. The postlude's "
                     "republish clears the queue, so an undrained entry is destroyed, not delayed")
        report(f"ledger: {len(chain_lines)} chain line(s) for {today}")

    print(f"check_chain: {today}")
    for ln in lines:
        print(f"  {ln}")
    if failures:
        for f_ in failures:
            print(f"  FAIL  {f_}")
        print(f"check_chain: FAILED with {len(failures)} finding(s)")
        return 1
    print("check_chain: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
