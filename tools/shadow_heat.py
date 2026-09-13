#!/usr/bin/env python3
"""Shadow the market's judgment on every OVER_CROWDED heat call. Stdlib only, offline.

Method section 8 amendment, 2026-09-13: a link that Ember scores OVER_CROWDED is a bet
that the crowd is wrong to keep piling in. That bet deserves the same grading every other
verdict gets. This tool appends one shadow row per representative ticker on every
OVER_CROWDED link, so `tools/fetch/fetch.py`'s `shadow_sweep()` grades it against SPY at
`review_at` (heat_as_of plus 90 days), the same mechanism `run deepdive` and `run redteam`
already feed. RIGHT means standing aside was correct.

Tickers per link, in this order, deduplicated against each other:
  (a) every link["price_instruments"][i]["ticker"] tagged expression "INSTRUMENT", never
      capped.
  (b) issuer tickers tagged expression "ISSUER": link["example_tickers"] in their given
      order first, then the listing tickers of ACTIVE placements on this chain and link
      from data/mappings/<slug>.json, alphabetically (a listing's market_ticker when it
      has one, else its ticker: tools/book.py's _placements() resolves this identical
      join the same way, because a bare local exchange code is not what any market file
      is keyed by). Only tickers with a market file on disk are kept; the surviving
      issuer list is then capped at 5.

A candidate with no market file, or whose market file has no row on or before heat_as_of,
is deferred and reported rather than silently dropped. A link that names no candidate at
all (empty example_tickers, no price_instruments, no matching ACTIVE placement) is
reported separately as no_tickers, because that is a mapping gap, not a data gap.

Writing to data/shadow/book.json preserves its indentation (detected from the file text)
and the order of existing rows; new rows are appended at the end. Ids already on disk are
skipped, so a re-run is idempotent.

CLI:
  python3 tools/shadow_heat.py <chain-slug> [--root PATH] [--date YYYY-MM-DD] [--request] [--dry-run]
  python3 tools/shadow_heat.py --all        [--root PATH] [--date YYYY-MM-DD] [--request] [--dry-run]

  --date        override today (UTC), for request id numbering and the "already queued
                today" check. Defaults to the real UTC date.
  --request     for every deferred ticker with no market file at all, queue one PENDING
                prices row in data/requests.json (unless a PENDING or FULFILLED prices row
                for that ticker already exists today). A ticker whose market file exists
                but simply has no row before heat_as_of is reported, not requeued: a
                refetch would not manufacture history that predates the file's own start.
  --dry-run     print what would happen; write nothing.

Prints its denominator on every run, dry or not:
  shadow_heat: <n> OVER_CROWDED link(s) across <c> chain(s); rows on disk <a>, added <b>,
  deferred <d> ticker(s): [T, ...]; no_tickers <k> link(s): [chain/link, ...]
and, with --request, a second line: requests queued <q>

Exit 0 always, except on unreadable required input (a named chain that does not exist or
does not parse, --all with no data/chains directory, or a corrupt book.json or
requests.json): exit 2 with a plain message.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path


def market_path(root: Path, ticker: str) -> Path:
    return root / "data" / "market" / f"{ticker.replace('.', '-')}.json"


def load_json(path: Path):
    """Parsed JSON, or None when the file is missing or does not parse. Silent by
    design: a missing market file or mapping is an ordinary, expected outcome here, not a
    fault to raise."""
    try:
        text = path.read_text()
    except (FileNotFoundError, IsADirectoryError, OSError):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def detect_indent(text: str) -> int:
    """The width of a JSON document's first indented line, defaulting to 1. Detecting
    this, rather than hardcoding a width, is what lets book.json's own convention survive
    a write here even if it ever changes."""
    for line in text.splitlines()[1:]:
        stripped = line.lstrip(" ")
        if stripped and len(stripped) != len(line):
            return len(line) - len(stripped)
    return 1


def close_on_or_before(rows, date: str):
    """The (close, row_date) pair for the last row at or before `date`, or (None, None).

    Mirrors tools/fetch/fetch.py's close_at() over series.rows, which are [date, close]
    pairs sorted ascending: walk forward, remember the last row that still qualifies, stop
    at the first that does not. Re-implemented here rather than imported because fetch.py
    pulls in yfinance and pandas, which this tool has no business needing.
    """
    best = None
    for row in rows or []:
        if row[0] <= date:
            best = row
        else:
            break
    if best is None:
        return None, None
    return best[1], best[0]


def gather_candidates(link: dict, mapping: dict):
    """(instrument_tickers, issuer_tickers), both deduplicated against each other: a
    ticker already used as an instrument is never repeated as an issuer. Order within
    each list is the order method section 8 fixes: instruments as given, then
    example_tickers as given, then ACTIVE placement listings alphabetically."""
    seen = set()
    instruments = []
    for pi in link.get("price_instruments") or []:
        # Every price_instruments entry is an instrument (method section 4). This loop used to
        # require an `expression` key no contract defines, and skipped BWET, the first real
        # record, on 2026-09-13.
        t = pi.get("ticker") if isinstance(pi, dict) else None
        if t and t not in seen:
            seen.add(t)
            instruments.append(t)

    issuers = []
    for t in link.get("example_tickers") or []:
        if t and t not in seen:
            seen.add(t)
            issuers.append(t)

    # tools/book.py's _placements() resolves the identical join (ACTIVE placement to a
    # fetchable ticker) the same way: market_ticker first, ticker as fallback. A bare
    # local exchange code (BAE Systems' listing ticker is "BA.", Rheinmetall's is "RHM")
    # is not what any market file on disk is keyed by, and "BA." risks reading as Boeing's
    # "BA" one character short; market_ticker is the suffixed, fetch-compatible symbol.
    listings = {}
    for l in (mapping.get("listings") or []):
        t = l.get("market_ticker") or l.get("ticker")
        if t:
            listings[l.get("issuer_id")] = t.upper()
    link_id = link.get("id")
    placed = sorted({
        listings[p.get("issuer_id")]
        for p in (mapping.get("placements") or [])
        if p.get("status") == "ACTIVE" and p.get("link_id") == link_id
        and listings.get(p.get("issuer_id"))
    })
    for t in placed:
        if t not in seen:
            seen.add(t)
            issuers.append(t)

    return instruments, issuers


def resolve_tickers(root: Path, tickers, heat_as_of: str, deferred: dict, chain_slug: str,
                    link_id: str):
    """For each candidate ticker: (ticker, close, row_date, series_source) when a close on
    or before heat_as_of is on disk, else the ticker is recorded once in `deferred` (first
    sighting wins) and dropped from the returned list."""
    out = []
    for t in tickers:
        m = load_json(market_path(root, t))
        if m is None:
            deferred.setdefault(t, {"kind": "no_file", "chain": chain_slug,
                                    "link": link_id, "date": heat_as_of})
            continue
        rows = ((m.get("series") or {}).get("rows")) or []
        value, row_date = close_on_or_before(rows, heat_as_of)
        if value is None:
            deferred.setdefault(t, {"kind": "no_close", "chain": chain_slug,
                                    "link": link_id, "date": heat_as_of})
            continue
        source = (m.get("series") or {}).get("source", "")
        out.append((t, value, row_date, source))
    return out


def build_rows(root: Path, chain_slug: str, link: dict, heat_as_of: str, mapping: dict,
              existing_ids: set, deferred: dict, written_at: str | None = None):
    """New shadow rows for one OVER_CROWDED link, plus whether it named zero candidates
    at all (the no_tickers case, distinct from every candidate being deferred)."""
    link_id = link.get("id")
    instruments, issuers = gather_candidates(link, mapping)
    if not instruments and not issuers:
        return [], True

    resolved_instruments = resolve_tickers(root, instruments, heat_as_of, deferred,
                                           chain_slug, link_id)
    resolved_issuers = resolve_tickers(root, issuers, heat_as_of, deferred, chain_slug,
                                       link_id)[:5]

    heat = link.get("heat") or {}
    crowdedness = (heat.get("crowdedness") or {}).get("score")
    review_at = (datetime.date.fromisoformat(heat_as_of) + datetime.timedelta(days=90)).isoformat()
    link_name = link.get("name") or link_id

    rows = []
    for expression, resolved in (("INSTRUMENT", resolved_instruments),
                                 ("ISSUER", resolved_issuers)):
        for ticker, value, row_date, source in resolved:
            rid = f"SHD-HEAT-{chain_slug}-{link_id}-{ticker}-{heat_as_of}"
            if rid in existing_ids:
                continue
            existing_ids.add(rid)
            rows.append({
                "id": rid,
                "ticker": ticker,
                "origin": "HEAT_OVER_CROWDED",
                "expression": expression,
                "chain_id": chain_slug,
                "link_id": link_id,
                "heat_ref": f"data/chains/{chain_slug}.json#{link_id}",
                "verdict_date": heat_as_of,
                "spot": {
                    "value": value,
                    "source": f"data/market/{ticker.replace('.', '-')}.json series.rows "
                              f"close on/before {heat_as_of} ({source})",
                    "as_of": row_date,
                },
                "review_at": review_at,
                "written_at": written_at or heat_as_of,
                "note": f"{link_name}: OVER_CROWDED (crowdedness {crowdedness}) at "
                        f"{heat_as_of}; RIGHT means standing aside was correct"
                        + (f"; row written {written_at}, after the call, so the move between "
                           f"the call and that day was already visible when it was added"
                           if written_at and written_at > heat_as_of else ""),
            })
    return rows, False


def queue_prices_requests(root: Path, deferred: dict, today: str, now_iso: str,
                          dry_run: bool):
    """Append one PENDING prices row per ticker in `deferred` that has no market file at
    all (kind "no_file"). A ticker whose file exists but lacks a row before heat_as_of
    (kind "no_close") is not requeued: fetching more prices cannot manufacture history
    that predates the series' own start. Returns (queued_tickers, error_message)."""
    candidates = [(t, info) for t, info in deferred.items() if info["kind"] == "no_file"]
    if not candidates:
        return [], None

    req_path = root / "data" / "requests.json"
    text = None
    try:
        text = req_path.read_text()
    except FileNotFoundError:
        pass
    if text is None:
        store = {"version": 1, "requests": []}
        indent = 1
    else:
        try:
            store = json.loads(text)
        except json.JSONDecodeError:
            return [], f"shadow_heat: {req_path} exists but is not valid JSON"
        indent = detect_indent(text)

    rows = store.get("requests")
    if rows is None:
        rows = store["requests"] = []

    already_today = {
        r.get("ticker") for r in rows
        if r.get("kind") == "prices" and r.get("status") in ("PENDING", "FULFILLED")
        and str(r.get("requested_at") or "").startswith(today)
    }
    prefix = f"REQ-{today.replace('-', '')}-"
    maxn = 0
    for r in rows:
        rid = str(r.get("id") or "")
        if rid.startswith(prefix) and rid[len(prefix):].isdigit():
            maxn = max(maxn, int(rid[len(prefix):]))

    queued = []
    for t, info in candidates:
        if t in already_today:
            continue
        maxn += 1
        rows.append({
            "id": f"{prefix}{maxn:02d}",
            "kind": "prices",
            "ticker": t,
            "query": None,
            "forms": None,
            "lookback_days": None,
            "requested_by": f"tools/shadow_heat.py {info['chain']}",
            "requested_at": now_iso,
            "by": "routine",
            "status": "PENDING",
            "note": f"{info['chain']}/{info['link']} OVER_CROWDED heat {info['date']}: "
                    f"no market file for {t}, queued for the shadow book",
        })
        queued.append(t)

    if queued and not dry_run:
        req_path.write_text(json.dumps(store, indent=indent, ensure_ascii=False) + "\n")
    return queued, None


def load_targets(root: Path, chain_arg: str, sweep_all: bool):
    """[(chain_slug, chain_dict)] for the run, or (None, error_message) on unreadable
    required input."""
    chains_dir = root / "data" / "chains"
    if sweep_all:
        if not chains_dir.is_dir():
            return None, f"shadow_heat: {chains_dir} not found"
        targets = []
        for p in sorted(chains_dir.glob("*.json")):
            if p.name.startswith("_"):
                continue
            d = load_json(p)
            if d is None:
                print(f"shadow_heat: {p} is not valid JSON, skipped")
                continue
            targets.append((p.stem, d))
        return targets, None
    p = chains_dir / f"{chain_arg}.json"
    d = load_json(p)
    if d is None:
        return None, f"shadow_heat: {p} not found or not valid JSON"
    return [(chain_arg, d)], None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("chain", nargs="?", default=None, help="chain slug, e.g. ai-infrastructure")
    ap.add_argument("--all", action="store_true", dest="all_chains",
                    help="sweep every chain on disk")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--date", default=None, help="override today (UTC), mainly for tests")
    ap.add_argument("--request", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if bool(a.chain) == bool(a.all_chains):
        print("shadow_heat: pass exactly one of a chain slug or --all")
        return 2

    root = Path(a.root).resolve()
    targets, err = load_targets(root, a.chain, a.all_chains)
    if err:
        print(err)
        return 2

    if a.date:
        today = a.date
        now_iso = f"{a.date}T00:00:00+00:00"
    else:
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        today = now_dt.date().isoformat()
        now_iso = now_dt.isoformat()

    book_path = root / "data" / "shadow" / "book.json"
    book_text = None
    try:
        book_text = book_path.read_text()
    except FileNotFoundError:
        pass
    if book_text is None:
        book = {"rows": []}
        indent = 1
    else:
        try:
            book = json.loads(book_text)
        except json.JSONDecodeError:
            print(f"shadow_heat: {book_path} exists but is not valid JSON")
            return 2
        indent = detect_indent(book_text)

    existing_rows = book.get("rows") or []
    existing_ids = {r.get("id") for r in existing_rows}
    rows_on_disk = len(existing_rows)

    new_rows = []
    deferred: dict = {}
    no_tickers_links = []
    n_links = 0
    chains_seen = set()

    for chain_slug, chain in targets:
        heat_as_of = chain.get("heat_as_of")
        if not heat_as_of:
            continue
        mapping = load_json(root / "data" / "mappings" / f"{chain_slug}.json") or {}
        for link in chain.get("links") or []:
            heat = link.get("heat") or {}
            if heat.get("verdict") != "OVER_CROWDED":
                continue
            n_links += 1
            chains_seen.add(chain_slug)
            try:
                rows, no_tix = build_rows(root, chain_slug, link, heat_as_of, mapping,
                                          existing_ids, deferred, written_at=today)
            except ValueError:
                # A malformed heat_as_of on an otherwise valid chain: report and move on
                # rather than let one bad date abort a whole --all sweep.
                print(f"shadow_heat: {chain_slug}/{link.get('id')}: heat_as_of "
                      f"{heat_as_of!r} does not parse as a date, skipped")
                continue
            if no_tix:
                no_tickers_links.append(f"{chain_slug}/{link.get('id')}")
            new_rows.extend(rows)

    if new_rows and not a.dry_run:
        book["rows"] = existing_rows + new_rows
        book_path.parent.mkdir(parents=True, exist_ok=True)
        book_path.write_text(json.dumps(book, indent=indent, ensure_ascii=False) + "\n")

    queued = []
    if a.request:
        queued, req_err = queue_prices_requests(root, deferred, today, now_iso, a.dry_run)
        if req_err:
            print(req_err)
            return 2

    deferred_list = list(deferred.keys())
    print(f"shadow_heat: {n_links} OVER_CROWDED link(s) across {len(chains_seen)} chain(s); "
          f"rows on disk {rows_on_disk}, added {len(new_rows)}, deferred "
          f"{len(deferred_list)} ticker(s): [{', '.join(deferred_list)}]; no_tickers "
          f"{len(no_tickers_links)} link(s): [{', '.join(no_tickers_links)}]")
    if a.request:
        print(f"requests queued {len(queued)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
