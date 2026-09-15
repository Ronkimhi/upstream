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
 11. the explainer bar: a chain touched on or after EXPLAINER_GATE carries a plain-Hebrew
     explainer on every link and at chain level; any explainer present names no figure,
     no em or en dash, and every draws_on path resolves on its own object
 12. the price test (method §4 amendment, 2026-09-13): every HIGH/CHOKE_POINT link
     names the price it sets, with a unit, a publisher and dated evidence, or says in
     `basis` why it sets none; every such link also records its instrument search; every
     instrument on any link, required or not, is a real, identified, cited listing that
     holds what it claims to; and no `price_instruments` entry present at git HEAD may
     silently vanish. Strict for a chain created on or after 2026-09-13 or touched
     today; an older, unamended chain is reported as a warning, never a failure

Scope: checks 9 and 10 only bind on a day that actually wrote a chain. On a day with no
chain run the gate says NOT RUN TODAY for those, rather than reporting a clean pass over
nothing, and still runs the structural checks over the whole corpus. Check 12 uses its
own per-chain dated ratchet (PRICE_TEST_GATE), independent of whether a chain was
written today.

Run: python3 tools/check_chain.py [--date YYYY-MM-DD] [--root PATH] [--warn-citations]
Exit 0 clean, 1 on any failure.
"""
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

# check_screen.normalize is the one verbatim-matching rule in the repo (whitespace,
# case and smart-punctuation folded); imported rather than re-implemented a second
# time, per method section 1 and this repo's own rule against duplicated-logic drift.
# TICKER_RE is heat_score's single ticker shape, already shared by check_scenarios.
# Both check_screen.py and what it in turn imports (check_map.py, evidence_store.py)
# were read end to end for import-time side effects: none found, every top-level
# statement there is a constant, a compiled regex, or a def, nothing runs until
# main() is called under its own __main__ guard. The path insert mirrors check_map.py's
# own pattern for importing a sibling module out of tools/.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_screen import normalize, quote_in_text  # noqa: E402
from heat_score import TICKER_RE  # noqa: E402
from ledger_lines import command_lines  # noqa: E402

# Ledger-content rules bind runs from this date forward, never retroactively. Same pattern
# as OCCURRENCE_GATE in tools/validate.py: a rule invented today cannot fail yesterday's work.
LEDGER_RULE_GATE = "2026-08-30"

# The Hebrew explainer layer (Ron, 2026-09-04). Same dated ratchet as the citation bar: a chain
# whose updated_at is on or after this date must explain every link and itself; chains not
# yet touched are reported, not failed. The prose bar is mechanical on purpose: a digit run
# that is not part of a product code is refused outright, because an explainer that names no
# figure cannot invent one (method section 1), and every draws_on path must resolve to a real
# field on the link or chain it paraphrases.
EXPLAINER_GATE = "2026-09-04"
HEBREW = re.compile("[\u0590-\u05FF]")
DASHES = re.compile("[\u2013\u2014]")
# "40", "18%", "$143", "2026", "3-for-1" refused; "H100", "HBM3E", "2nm" allowed
NUMERIC_TOKEN = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)*(?![A-Za-z])")
LINK_KEYS = ("what", "players", "why", "bottleneck", "hands_to")
CHAIN_KEYS = ("shape", "thesis")
EXPLAINER_MIN_CHARS = 40

# The price test (method §4 amendment, 2026-09-13). Same dated ratchet as the citation
# and explainer bars above: a chain created on or after this date, or touched (a
# changelog entry dated) today, answers the price test in full; an older chain the
# amendment never reached is the seed corpus and its gaps are reported, not failed,
# until it is amended.
PRICE_TEST_GATE = "2026-09-14"  # the day after the gate landed: runs already in flight on 2026-09-13 warn
REQUIRED_CRITICALITY = ("HIGH", "CHOKE_POINT")
INSTRUMENT_KINDS = {"ETF", "ETN", "COMMODITY_POOL", "PHYSICAL_TRUST", "FUTURES", "INDEX_NOTE"}
SEARCH_RESULTS = {"FOUND", "NONE_FOUND", "NOT_APPLICABLE"}
SEARCHED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def resolve_path(obj, path: str):
    """Resolve a draws_on path such as `heat.capture.rationale`, `evidence[0].claim`,
    `links[power-equipment].role` or `scenarios[].narrative` against obj. None when it
    resolves to nothing; a list step keeps only non-empty elements."""
    cur = obj
    for tok in re.findall(r"[^.\[\]]+|\[[^\]]*\]", path or ""):
        if tok.startswith("["):
            key = tok[1:-1]
            if not isinstance(cur, list):
                return None
            if key == "":
                cur = [x for x in cur if x not in (None, "", [], {})]
            elif key.isdigit():
                cur = cur[int(key)] if int(key) < len(cur) else None
            else:
                cur = next((x for x in cur if isinstance(x, dict) and x.get("id") == key), None)
        elif isinstance(cur, dict):
            cur = cur.get(tok)
        elif isinstance(cur, list):
            cur = [x.get(tok) for x in cur if isinstance(x, dict) and x.get(tok) not in (None, "", [], {})]
        else:
            return None
        if cur in (None, "", [], {}):
            return None
    return cur


def explainer_failures(owner: dict, keys, ctx: str, field: str = "explainer") -> list[str]:
    """Every way a plain-Hebrew explainer block present on `owner` can be wrong. Pure; the
    tests call it. `field` names the key on `owner` that carries the block: "explainer" for
    chains and links, and reused as-is (Stocky dives) or with a different `field` (Sieve's
    `selection_note`) by every other stage that adopts the same prose rules rather than
    copying this function a second time."""
    x = owner.get(field)
    if not isinstance(x, dict):
        return [f"{ctx}: {field} is not an object"]
    fails = []
    if x.get("lang") != "he":
        fails.append(f"{ctx}: {field}.lang must be 'he'")
    for k in keys:
        v = x.get(k)
        if not isinstance(v, str) or len(v.strip()) < EXPLAINER_MIN_CHARS or not HEBREW.search(v):
            fails.append(f"{ctx}: {field}.{k} missing, short, or not Hebrew")
            continue
        m = NUMERIC_TOKEN.search(v)
        if m:
            fails.append(f"{ctx}: {field}.{k} carries the numeric token {m.group(0)!r}; the "
                         f"prose names no figure (method section 1: a number lives in a sourced field)")
        if DASHES.search(v):
            fails.append(f"{ctx}: {field}.{k} contains an em or en dash")
    for k in ("as_of", "by"):
        if not x.get(k):
            fails.append(f"{ctx}: {field}.{k} missing")
    for path in x.get("draws_on") or []:
        if resolve_path(owner, path) is None:
            fails.append(f"{ctx}: {field}.draws_on {path!r} resolves to nothing on this object")
    return fails


def _http_url(value) -> bool:
    return isinstance(value, str) and bool(re.match(r"^https?://", value))


def changelog_dated(chain: dict, day: str) -> bool:
    """True when `chain`'s changelog carries an entry timestamped on `day` (YYYY-MM-DD).
    Tries `ts`, then `date`, then `as_of`, the three spellings a changelog entry uses
    across this repo's stores."""
    for entry in chain.get("changelog") or []:
        if not isinstance(entry, dict):
            continue
        ts = str(entry.get("ts") or entry.get("date") or entry.get("as_of") or "")
        if ts[:10] == day:
            return True
    return False


def price_test_strict(chain: dict, today: str) -> bool:
    """A chain answers the price test in full once it is created on or after
    PRICE_TEST_GATE, or touched (a changelog entry) on the run day. An older chain the
    amendment never reached is the seed corpus: its gaps are warnings, not failures,
    the same ratchet the citation and explainer bars used before their own backfills."""
    created = str(chain.get("created_at") or "")[:10]
    if created >= PRICE_TEST_GATE:
        return True
    return today >= PRICE_TEST_GATE and changelog_dated(chain, today)


def _scarce_price_failures(link_id, sp) -> list[str]:
    """Method §4: every HIGH/CHOKE_POINT link either names the price it sets, with a
    unit, a publisher and dated evidence, or says in `basis` why it sets none. Pure."""
    if not isinstance(sp, dict):
        return [f"{link_id}: scarce_price missing on a HIGH/CHOKE_POINT link"]
    name = sp.get("name")
    if name is None:
        basis = sp.get("basis")
        if not isinstance(basis, str) or not basis.strip():
            return [f"{link_id}: scarce_price.basis missing when name is null"]
        return []
    if not isinstance(name, str) or not name.strip():
        return [f"{link_id}: scarce_price.name is empty; use null with a basis when "
                f"this link sets no price"]
    fails = []
    if not str(sp.get("unit") or "").strip():
        fails.append(f"{link_id}: scarce_price.unit missing")
    if not str(sp.get("published_by") or "").strip():
        fails.append(f"{link_id}: scarce_price.published_by missing")
    ev = sp.get("evidence") if isinstance(sp.get("evidence"), list) else []
    if not any(isinstance(e, dict) and e.get("source_date") and _http_url(e.get("url")) for e in ev):
        fails.append(f"{link_id}: scarce_price.evidence has no item with a source_date "
                      f"and an http(s) url")
    return fails


def _instrument_search_failures(link_id, isr, n_instruments: int) -> list[str]:
    """Method §4: every HIGH/CHOKE_POINT link records that it searched for a tradeable
    instrument, what it searched, and what it found. Pure."""
    if not isinstance(isr, dict):
        return [f"{link_id}: instrument_search missing on a HIGH/CHOKE_POINT link"]
    fails = []
    searched_at = isr.get("searched_at")
    if not isinstance(searched_at, str) or not SEARCHED_AT_RE.match(searched_at):
        fails.append(f"{link_id}: instrument_search.searched_at is not a date string")
    result = isr.get("result")
    if result not in SEARCH_RESULTS:
        fails.append(f"{link_id}: instrument_search.result {result!r} is not one of "
                      f"{sorted(SEARCH_RESULTS)}")
    queries = isr.get("queries")
    if result != "NOT_APPLICABLE" and not (isinstance(queries, list) and queries):
        fails.append(f"{link_id}: instrument_search.queries must be a non-empty list "
                      f"unless result is NOT_APPLICABLE")
    boundary = isr.get("boundary")
    if not isinstance(boundary, str):
        fails.append(f"{link_id}: instrument_search.boundary must be a string")
    elif result != "FOUND" and not boundary.strip():
        fails.append(f"{link_id}: instrument_search.boundary is empty; only a FOUND "
                      f"result may leave it blank")
    if result == "FOUND" and n_instruments == 0:
        fails.append(f"{link_id}: instrument_search.result FOUND with an empty "
                      f"price_instruments list")
    if result in ("NONE_FOUND", "NOT_APPLICABLE") and n_instruments != 0:
        fails.append(f"{link_id}: instrument_search.result {result} but price_instruments "
                      f"is non-empty")
    return fails


def _instrument_failures(link_id, instr, index) -> list[str]:
    """Every instrument on any link, required or not: a real, identified, cited
    listing that actually holds what it claims to. Pure."""
    if not isinstance(instr, dict):
        return [f"{link_id}: price_instruments[{index}] is not an object"]
    ticker = instr.get("ticker")
    where = f"{link_id}: price_instruments[{ticker if isinstance(ticker, str) and ticker else index}]"
    fails = []
    if not isinstance(ticker, str) or not TICKER_RE.fullmatch(ticker):
        fails.append(f"{where}.ticker {ticker!r} does not match TICKER_RE")
    if not str(instr.get("exchange") or "").strip():
        fails.append(f"{where}.exchange is empty")
    if instr.get("kind") not in INSTRUMENT_KINDS:
        fails.append(f"{where}.kind {instr.get('kind')!r} is not one of "
                      f"{sorted(INSTRUMENT_KINDS)}")
    holds = instr.get("holds")
    if not isinstance(holds, str) or not holds.strip():
        fails.append(f"{where}.holds is empty")
    ev = instr.get("identity_evidence") if isinstance(instr.get("identity_evidence"), list) else []
    verified = [e for e in ev if isinstance(e, dict) and e.get("tag") == "VERIFIED"
                and e.get("source_date") and _http_url(e.get("url"))
                and isinstance(e.get("source_excerpt"), str) and e.get("source_excerpt").strip()]
    if not verified:
        fails.append(f"{where}.identity_evidence has no VERIFIED item with a source_date, "
                      f"an http(s) url and a non-empty source_excerpt")
    elif isinstance(holds, str) and holds.strip():
        if not any(quote_in_text(holds, e["source_excerpt"]) for e in verified):
            fails.append(f"{where}.holds does not appear in any identity_evidence source_excerpt")
    return fails


def price_test_failures(chain: dict, head_chain) -> tuple[list[str], dict]:
    """Method §4's price-test amendment (2026-09-13), pure. `head_chain` is the chain's
    own git HEAD version (a dict), or None when it is unavailable, which fails the
    preservation half open exactly like the heat/scenario preservation check above.
    Returns every finding as a plain string plus the objective denominator; main()
    alone decides, per chain, whether a finding fails or warns, because that decision
    needs today's date and this function has no notion of it."""
    fails: list[str] = []
    stats = {"required": 0, "answered": 0, "instruments": 0, "none_found": 0}
    links = chain.get("links") or []

    for l in links:
        if not isinstance(l, dict):
            continue
        link_id = l.get("id")
        crit = (l.get("bottleneck") or {}).get("criticality")
        required = crit in REQUIRED_CRITICALITY

        pis = l.get("price_instruments")
        if pis is not None and not isinstance(pis, list):
            fails.append(f"{link_id}: price_instruments is not a list")
            pis = None
        n_instr = len(pis) if isinstance(pis, list) else 0
        stats["instruments"] += n_instr

        if required:
            stats["required"] += 1
            sp_fails = _scarce_price_failures(link_id, l.get("scarce_price"))
            if not sp_fails:
                stats["answered"] += 1
            fails.extend(sp_fails)

            isr = l.get("instrument_search")
            fails.extend(_instrument_search_failures(link_id, isr, n_instr))
            if isinstance(isr, dict) and isr.get("result") == "NONE_FOUND":
                stats["none_found"] += 1

        if isinstance(pis, list):
            for i, instr in enumerate(pis):
                fails.extend(_instrument_failures(link_id, instr, i))

    if isinstance(head_chain, dict):
        def _pairs(ch):
            out = set()
            for l in ch.get("links") or []:
                if not isinstance(l, dict):
                    continue
                for instr in l.get("price_instruments") or []:
                    if isinstance(instr, dict) and instr.get("ticker"):
                        out.add((l.get("id"), instr.get("ticker")))
            return out
        dropped = _pairs(head_chain) - _pairs(chain)
        for link_id, ticker in sorted(dropped):
            fails.append(f"{link_id}: price_instruments[{ticker}] present at git HEAD is "
                          f"missing now; a chain rebuild amends links in place")
    # head_chain is None (git HEAD unreadable): preservation is skipped, not failed,
    # the same idiom as the heat/scenario preservation check above.

    return fails, stats


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



def chain_run_lines(lines):
    """Ledger lines that record a chain run: kind RUN or AMEND, and a command field (the third
    `|` field) that starts with `run chain` or `refresh data/chains`. The gate used to match the
    whole line, so a `request data` line whose result only named the chain refresh it was
    preparing counted as a chain run with no archetypes and turned CI red on 2026-09-13
    (fc66a90). What a line mentions is not what it ran. Pure."""
    return command_lines(lines, ("run chain", "refresh data/chains"))

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
    price_required_all = price_answered_all = price_instruments_all = 0
    price_none_found_all = price_warnings_all = 0

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

        # 11. the explainer bar
        bound = str(c.get("updated_at") or "")[:10] >= EXPLAINER_GATE
        have = 0
        for l in links:
            if l.get("explainer") is not None:
                have += 1
                for m in explainer_failures(l, LINK_KEYS, f"{cid}/{l.get('id')}"):
                    fail(m)
            elif bound:
                fail(f"{cid}: link {l.get('id')} carries no explainer; a chain touched on or after "
                     f"{EXPLAINER_GATE} explains every link in plain Hebrew (what, players, why, "
                     f"bottleneck, hands_to)")
        chain_x = c.get("explainer")
        if chain_x is not None:
            for m in explainer_failures(c, CHAIN_KEYS, f"{cid} (chain)"):
                fail(m)
        elif bound:
            fail(f"{cid}: no chain-level explainer (shape, thesis); a chain touched on or after "
                 f"{EXPLAINER_GATE} explains its own shape")
        report(f"{cid}: explainers {have} of {n} links, chain-level "
               f"{'yes' if isinstance(chain_x, dict) else 'no'}"
               + ("" if bound else f" (not bound: updated_at {str(c.get('updated_at') or '')[:10] or 'unset'})"))

        # 12. the price test (method §4 amendment, 2026-09-13)
        pf, pstats = price_test_failures(c, prev)
        price_required_all += pstats["required"]
        price_answered_all += pstats["answered"]
        price_instruments_all += pstats["instruments"]
        price_none_found_all += pstats["none_found"]
        if pf:
            if price_test_strict(c, today):
                for m in pf:
                    fail(m)
            else:
                for m in pf:
                    report(m + "  [WARNING: price test amendment 2026-09-13, seed chain]")
                price_warnings_all += len(pf)

    report(f"corpus: {len(chains)} chains, {total_links} links examined, "
           f"{uncited_all} uncited ({'error' if strict else 'warning'} mode)")
    report(f"price test: {price_required_all} links required (HIGH/CHOKE_POINT), "
           f"{price_answered_all} answered, {price_instruments_all} instrument(s), "
           f"{price_none_found_all} NONE_FOUND, {price_warnings_all} warning(s) on seed chains")

    # 10. the ledger line
    ledger = (data / "ledger.md").read_text() if (data / "ledger.md").exists() else ""
    todays = [ln for ln in ledger.splitlines() if ln.startswith(today)]
    chain_lines = chain_run_lines(todays)
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
