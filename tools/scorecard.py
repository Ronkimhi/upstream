#!/usr/bin/env python3
"""The scorecard: every label Upstream issues, graded against later stock returns.

Ron, 2026-11-19: "Yes, build it". Before this tool nothing graded the machine's "yes"
calls. The shadow book grades only TOO_LATE and OVER_CROWDED at +90 days. This grades
every heat verdict, every opportunity tier, every dive verdict and every impact band.

Pure computation over committed data: stdlib only, no network, no Anthropic calls, and it
never writes a price. Every close it uses is read from data/market/<T>.json; a ticker with
no usable series is excluded with a reason and counted, never filled.

TWO STORES, both under data/scorecard/ (Actions-owned; .github/workflows/fetch.yml runs
this after every fetch, best effort).

  calls.json      APPEND-ONLY. A call is one label on one object on one date, frozen at
                  first sight: origin, object ref, label, the raw scores where they exist,
                  label_date, the ticker set used and its start prices. When a later run
                  finds a different label or different scores on the same object it
                  appends a NEW call and leaves the old one untouched, so a re-score can
                  never replace a graded label after the fact. Each call's id is a hash of
                  its identity and each call carries a digest of its whole content; a run
                  that finds an edited, dropped-field or duplicated call REFUSES (exit 2)
                  and writes nothing. `--check` also compares the store against a git
                  revision and fails (exit 1) when a committed call was edited or dropped.

                  provenance BACKFILL: created on the first run from today's disk, for a
                  label issued before this tool existed. label_date is the label's date as
                  it stands today, with the source named (heat as_of, the changelog entry
                  that set the label, the profile's promotion, created_at).
                  provenance OBSERVED: a label first seen by a later run. label_date is the
                  run date (first sight), which is the hindsight-proof choice; the label's
                  own stated date is kept beside it.

  scorecard.json  REGENERATED WHOLE every run from calls plus prices.

Origins (never pooled, method section 8):
  HEAT    a chain link's heat verdict, its three scores and money_corner. Tickers: the
          link's ACTIVE mapping placements (one listing per issuer, the first with a market
          file), falling back to example_tickers, and the call says which.
  TIER    a company profile's opportunity tier (O1/O2/O3). Ticker: the issuer's listing.
  DIVE    a stock dive's verdict and its clock. Ticker: the dive's listing.
  IMPACT  an appraisal's band, only where occurrence -> signal -> chain -> mapping resolves
          tickers (all ACTIVE placements on that chain). Otherwise counted UNTIED.

Grading: horizons 30, 90, 180 and 365 calendar days after label_date. A horizon is graded
only once label_date + horizon is on or before the last SPY close on disk; until then the
call carries a `to_date` figure marked INTERIM. Two benchmarks over identical dates: SPY,
and the machine's own universe (equal-weighted mean return of every priced ticker on any
ACTIVE placement), which answers whether the label beat simply owning the whole map. Per
origin and label: N calls, N tickers, exclusions by reason, median and mean excess against
each benchmark, and hit rate (excess > 0), at call level (equal-weighted across the call's
tickers) and at ticker level. Medians and hit rates are primary: one penny stock swings a
mean. HEAT also gets Spearman correlations of forward excess against impact, crowdedness,
capture and impact*capture*(100-crowdedness).

Price basis: raw closes (yfinance auto_adjust=False). Returns are PRICE returns only, no
dividends, in each listing's own currency against a USD SPY.

Every run prints examined / graded / interim / excluded. A run over an empty tree prints
NOTHING GRADED and writes nothing.

Run: python3 tools/scorecard.py [--root PATH] [--date YYYY-MM-DD] [--dry-run]
     python3 tools/scorecard.py --check [--against REV]
Exit 0 ok, 1 --check found a violation, 2 refused (corrupt or edited store).
"""
import argparse
import bisect
import datetime
import hashlib
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from market_paths import resolve_market_stem, safe_name  # noqa: E402

HORIZONS = (30, 90, 180, 365)
BENCH = "SPY"
MAX_START_GAP = 5     # days between label_date and the first close used
MAX_STALE = 10        # days between a horizon's target date and the close used for it
CALLS_REL = "data/scorecard/calls.json"
CARD_REL = "data/scorecard/scorecard.json"
ORIGINS = ("HEAT", "TIER", "DIVE", "IMPACT")
RESOLVED_SCENARIO = {"RESOLVED", "CONFIRMED", "INVALIDATED", "PLAYED_OUT", "FAILED", "CLOSED"}
CALL_REQUIRED = ("id", "origin", "object_ref", "label", "scores", "facets", "label_date",
                 "date_source", "stated_date", "provenance", "first_seen", "ticker_source",
                 "tickers", "start_prices", "digest")
PRICE_BASIS = ("raw closes (yfinance auto_adjust=False): price return only, dividends "
               "excluded; each listing in its own currency against a USD SPY")


class Refusal(Exception):
    """The store is not safe to append to. Nothing is written."""


# ---------------------------------------------------------------- small helpers

def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def json_files(folder: Path):
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.json") if not p.name.startswith("_"))


def d2(s):
    return datetime.date.fromisoformat(str(s)[:10])


def days(a, b):
    return (d2(b) - d2(a)).days


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(obj) -> str:
    return hashlib.sha256(canon(obj).encode()).hexdigest()


def call_identity(c) -> dict:
    return {k: c.get(k) for k in ("origin", "object_ref", "label", "scores", "facets", "label_date")}


def call_digest(c) -> str:
    return sha({k: v for k, v in c.items() if k != "digest"})


def signature(origin, label, scores, facets):
    return canon([origin, label, scores, facets])


# ---------------------------------------------------------------- prices

class Prices:
    """Read-only access to data/market/. Never fills a missing value."""

    def __init__(self, data: Path):
        self.dir = data / "market"
        self._cache = {}

    def stem_for(self, ticker=None, exchange=None, market_ticker=None):
        if not self.dir.is_dir():
            return None
        return resolve_market_stem(ticker, exchange, market_ticker, available=self.dir)

    def series(self, stem):
        if stem in self._cache:
            return self._cache[stem]
        out = None
        if stem:
            doc = load_json(self.dir / f"{stem}.json")
            rows = ((doc or {}).get("series") or {}).get("rows") or []
            clean = []
            for r in rows:
                try:
                    d, c = str(r[0])[:10], float(r[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if c > 0:
                    clean.append((d, c))
            clean.sort()
            if clean:
                out = ([d for d, _ in clean], [c for _, c in clean])
        self._cache[stem] = out
        return out

    @staticmethod
    def on_or_after(s, date):
        i = bisect.bisect_left(s[0], date)
        return (s[0][i], s[1][i]) if i < len(s[0]) else None

    @staticmethod
    def on_or_before(s, date):
        i = bisect.bisect_right(s[0], date) - 1
        return (s[0][i], s[1][i]) if i >= 0 else None


# ---------------------------------------------------------------- reading the labels

TRANSITION = r"(?:->|→|\bto)\s*{label}\b"
PROMOTED_O1 = re.compile(r"Promoted[^.]{0,60}\bO1\b", re.I)


def changelog_date(doc, label, extra=None):
    """Date of the LATEST changelog entry that moved the object to `label`, or None."""
    pat = re.compile(TRANSITION.format(label=re.escape(label)))
    hit = None
    for e in doc.get("changelog") or []:
        text = str(e.get("change") or "")
        if pat.search(text) or (extra is not None and extra.search(text)):
            ts = str(e.get("ts") or "")[:10]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ts):
                hit = ts if hit is None or ts > hit else hit
    return hit


def first_priced_listing(prices, listings):
    """(ticker, stem) for the first listing with a market file, else (first ticker, None)."""
    first = None
    for L in listings or []:
        t = L.get("market_ticker") or L.get("ticker")
        if not t:
            continue
        first = first or t
        stem = prices.stem_for(L.get("ticker"), L.get("exchange"), L.get("market_ticker"))
        if stem:
            return t, stem
    return first, None


class Disk:
    def __init__(self, root: Path, prices: Prices):
        self.root, self.data, self.prices = root, root / "data", prices
        self.mappings = {}
        for p in json_files(self.data / "mappings"):
            m = load_json(p)
            if isinstance(m, dict):
                self.mappings[m.get("chain_id") or p.stem] = m

    def listings_by_issuer(self, m):
        out = {}
        for L in m.get("listings") or []:
            out.setdefault(L.get("issuer_id"), []).append(L)
        return out

    def placement_tickers(self, m, link_id=None):
        """Tickers of ACTIVE placements (one listing per issuer), ordered by issuer id."""
        lst = self.listings_by_issuer(m)
        seen, out = set(), []
        for p in m.get("placements") or []:
            if p.get("status") != "ACTIVE":
                continue
            if link_id is not None and p.get("link_id") != link_id:
                continue
            iid = p.get("issuer_id")
            if iid in seen:
                continue
            seen.add(iid)
            t, stem = first_priced_listing(self.prices, lst.get(iid))
            if t:
                out.append({"ticker": t, "stem": stem, "issuer_id": iid})
        return sorted(out, key=lambda x: str(x["issuer_id"]))

    def universe(self):
        stems = set()
        for m in self.mappings.values():
            for x in self.placement_tickers(m):
                if x["stem"]:
                    stems.add(x["stem"])
        return sorted(stems)


def current_labels(disk: Disk, counts: dict):
    """Every label on disk today, as dicts ready to become calls."""
    out = []
    data, prices = disk.data, disk.prices

    # HEAT
    for p in json_files(data / "chains"):
        c = load_json(p)
        if not isinstance(c, dict):
            counts["unreadable"].append(str(p.relative_to(disk.root)))
            continue
        m = disk.mappings.get(c.get("id") or p.stem)
        for L in c.get("links") or []:
            counts["examined"]["HEAT"] += 1
            h = L.get("heat") or {}
            label = h.get("verdict")
            if not label:
                counts["unlabeled"]["HEAT"] += 1
                continue
            stated, src = h.get("as_of"), "heat.as_of"
            if not stated:
                stated, src = c.get("heat_as_of"), "chain.heat_as_of"
            if not stated:
                counts["undated"]["HEAT"] += 1
                continue
            scores = {k: (h.get(k) or {}).get("score") if isinstance(h.get(k), dict) else None
                      for k in ("impact", "crowdedness", "capture")}
            tickers, tsrc = [], "none"
            if m:
                tickers = disk.placement_tickers(m, L.get("id"))
                tsrc = "mapping_placements" if tickers else "none"
            if not tickers:
                for t in L.get("example_tickers") or []:
                    if isinstance(t, str) and t:
                        tickers.append({"ticker": t, "stem": prices.stem_for(t)})
                tsrc = "example_tickers" if tickers else "none"
            out.append(dict(origin="HEAT", object_ref=f"data/chains/{p.name}#{L.get('id')}",
                            label=label, scores=scores,
                            facets={"money_corner": bool(h.get("money_corner"))},
                            stated_date=str(stated)[:10], date_source=src,
                            tickers=tickers, ticker_source=tsrc))

    # TIER
    for p in json_files(data / "companies"):
        c = load_json(p)
        if not isinstance(c, dict):
            counts["unreadable"].append(str(p.relative_to(disk.root)))
            continue
        counts["examined"]["TIER"] += 1
        label = c.get("opportunity_tier")
        if not label:
            counts["unlabeled"]["TIER"] += 1
            continue
        stated = changelog_date(c, label, PROMOTED_O1 if label == "O1" else None)
        src = "changelog_transition"
        if not stated:
            stated, src = str(c.get("created_at") or "")[:10] or None, "created_at"
        if not stated:
            counts["undated"]["TIER"] += 1
            continue
        t, stem = first_priced_listing(prices, c.get("listings"))
        tickers = [{"ticker": t, "stem": stem, "issuer_id": c.get("issuer_id")}] if t else []
        out.append(dict(origin="TIER", object_ref=f"data/companies/{p.name}", label=label,
                        scores=None, facets={}, stated_date=stated, date_source=src,
                        tickers=tickers, ticker_source="issuer_listing" if t else "none"))

    # DIVE
    for p in json_files(data / "stocks"):
        s = load_json(p)
        if not isinstance(s, dict):
            counts["unreadable"].append(str(p.relative_to(disk.root)))
            continue
        counts["examined"]["DIVE"] += 1
        label = s.get("verdict")
        if not label:
            counts["unlabeled"]["DIVE"] += 1
            continue
        stated, src = changelog_date(s, label), "changelog_transition"
        if not stated:
            stated, src = str(s.get("created_at") or "")[:10] or None, "created_at"
        if not stated:
            counts["undated"]["DIVE"] += 1
            continue
        t = s.get("ticker")
        tickers = [{"ticker": t, "stem": prices.stem_for(t), "issuer_id": s.get("issuer_id")}] if t else []
        out.append(dict(origin="DIVE", object_ref=f"data/stocks/{p.name}", label=label,
                        scores=None, facets={"clock": s.get("clock")}, stated_date=stated,
                        date_source=src, tickers=tickers,
                        ticker_source="dive_listing" if t else "none"))

    # IMPACT
    signals = {}
    for p in json_files(data / "signals"):
        s = load_json(p)
        if isinstance(s, dict):
            signals[s.get("id") or p.stem] = s
    for p in json_files(data / "impact"):
        a = load_json(p)
        if not isinstance(a, dict):
            counts["unreadable"].append(str(p.relative_to(disk.root)))
            continue
        counts["examined"]["IMPACT"] += 1
        label = a.get("impact_band")
        if not label:
            counts["unlabeled"]["IMPACT"] += 1
            continue
        occ = str(a.get("occurrence_id") or "")
        why = None
        if not occ.startswith("SIG-"):
            why = "OCCURRENCE_NOT_A_SIGNAL"
        elif occ not in signals:
            why = "SIGNAL_NOT_ON_DISK"
        elif not signals[occ].get("chain_id"):
            why = "SIGNAL_NOT_CHAINED"
        elif signals[occ]["chain_id"] not in disk.mappings:
            why = "CHAIN_HAS_NO_MAPPING"
        tickers = []
        if why is None:
            tickers = disk.placement_tickers(disk.mappings[signals[occ]["chain_id"]])
            if not tickers:
                why = "NO_ACTIVE_PLACEMENTS"
        if why:
            counts["untied"][why] = counts["untied"].get(why, 0) + 1
            continue
        stated = str(a.get("as_of") or "")[:10]
        if not stated:
            counts["undated"]["IMPACT"] += 1
            continue
        out.append(dict(origin="IMPACT", object_ref=f"data/impact/{p.name}", label=label,
                        scores={"impact_score": a.get("impact_score")},
                        facets={"chain_id": signals[occ]["chain_id"]}, stated_date=stated,
                        date_source="impact.as_of", tickers=tickers,
                        ticker_source="chain_mapping_placements"))
    return out


# ---------------------------------------------------------------- the calls store

def load_calls(path: Path):
    """The append-only store, verified. Raises Refusal on anything unsafe to append to."""
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise Refusal(f"{CALLS_REL} does not parse ({e}); an append-only store is never overwritten")
    if not isinstance(doc, dict) or not isinstance(doc.get("calls"), list):
        raise Refusal(f"{CALLS_REL} has no 'calls' list")
    seen = set()
    for i, c in enumerate(doc["calls"]):
        if not isinstance(c, dict):
            raise Refusal(f"call #{i} is not an object")
        missing = [k for k in CALL_REQUIRED if k not in c]
        if missing:
            raise Refusal(f"call #{i} ({c.get('id')}) lacks {missing}")
        if c["origin"] not in ORIGINS:
            raise Refusal(f"call {c['id']} has unknown origin {c['origin']!r}")
        if c["id"] in seen:
            raise Refusal(f"duplicate call id {c['id']}")
        seen.add(c["id"])
        if c["id"] != make_id(c):
            raise Refusal(f"call {c['id']}: id does not match its identity fields (label, scores, "
                          f"facets or label_date edited after the fact)")
        if c["digest"] != call_digest(c):
            raise Refusal(f"call {c['id']}: digest mismatch (the call was edited after it was frozen)")
    return doc


def make_id(c) -> str:
    return f"CALL-{c['origin']}-{sha(call_identity(c))[:12]}"


def start_price(prices, stem, label_date, graded_through):
    if not stem:
        return None, "NO_SERIES"
    s = prices.series(stem)
    if not s:
        return None, "NO_SERIES"
    st = Prices.on_or_after(s, label_date)
    if st is None:
        if graded_through and label_date > graded_through:
            return None, "PENDING_NO_CLOSE_YET"
        return None, "SERIES_ENDS_BEFORE_LABEL"
    if days(label_date, st[0]) > MAX_START_GAP:
        return None, f"START_GAP>{MAX_START_GAP}d"
    return {"date": st[0], "close": st[1]}, None


def new_call(lab, provenance, run_date, prices, graded_through):
    label_date = lab["stated_date"] if provenance == "BACKFILL" else run_date
    date_source = lab["date_source"] if provenance == "BACKFILL" else "first_seen"
    starts = {}
    for t in lab["tickers"]:
        px, why = start_price(prices, t.get("stem"), label_date, graded_through)
        starts[t["ticker"]] = px if px else {"excluded": why}
    c = dict(origin=lab["origin"], object_ref=lab["object_ref"], label=lab["label"],
             scores=lab["scores"], facets=lab["facets"], label_date=label_date,
             date_source=date_source, stated_date=lab["stated_date"],
             stated_date_source=lab["date_source"], provenance=provenance,
             first_seen=run_date, ticker_source=lab["ticker_source"],
             tickers=lab["tickers"], start_prices=starts)
    c = {"id": make_id(c), **c}
    c["digest"] = call_digest(c)
    return c


def append_calls(store, labels, run_date, prices, graded_through):
    """Append a call for every label whose (label, scores, facets) differs from the latest
    call on the same object. Existing calls are never touched."""
    provenance = "BACKFILL" if store is None else "OBSERVED"
    calls = list(store["calls"]) if store else []
    latest = {}
    for c in calls:
        latest[(c["origin"], c["object_ref"])] = signature(c["origin"], c["label"], c["scores"], c["facets"])
    ids = {c["id"] for c in calls}
    added = []
    for lab in labels:
        key = (lab["origin"], lab["object_ref"])
        sig = signature(lab["origin"], lab["label"], lab["scores"], lab["facets"])
        if latest.get(key) == sig:
            continue
        c = new_call(lab, provenance, run_date, prices, graded_through)
        if c["id"] in ids:   # same label re-seen on the same first-sight date
            continue
        calls.append(c)
        ids.add(c["id"])
        latest[key] = sig
        added.append(c)
    return calls, added


def git_calls(root: Path, rev: str):
    """Calls committed at `rev`, or None when git or the file at that rev is unavailable."""
    try:
        r = subprocess.run(["git", "-C", str(root), "show", f"{rev}:{CALLS_REL}"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout).get("calls")
    except (json.JSONDecodeError, AttributeError):
        return None


def append_only_violations(committed, current):
    """Every committed call must still be present, unchanged, in its original order."""
    if committed is None:
        return []
    bad = []
    if len(current) < len(committed):
        bad.append(f"store shrank: {len(committed)} committed calls, {len(current)} on disk")
    for i, c in enumerate(committed):
        if i >= len(current):
            bad.append(f"committed call {c.get('id')} dropped")
            continue
        if canon(current[i]) != canon(c):
            bad.append(f"committed call {c.get('id')} edited or reordered (position {i})")
    return bad


# ---------------------------------------------------------------- grading

class Grader:
    def __init__(self, prices: Prices, universe_stems, graded_through):
        self.prices, self.universe, self.end = prices, universe_stems, graded_through
        self.bench = prices.series(BENCH)
        self._ucache = {}

    def bench_ret(self, d0, d1):
        if not self.bench:
            return None
        a, b = Prices.on_or_before(self.bench, d0), Prices.on_or_before(self.bench, d1)
        if not a or not b or days(a[0], d0) > MAX_STALE or days(b[0], d1) > MAX_STALE:
            return None
        return b[1] / a[1] - 1

    def universe_ret(self, d0, d1):
        key = (d0, d1)
        if key not in self._ucache:
            rets = []
            for stem in self.universe:
                s = self.prices.series(stem)
                if not s:
                    continue
                a, b = Prices.on_or_before(s, d0), Prices.on_or_before(s, d1)
                if not a or not b or a[0] >= b[0]:
                    continue
                if days(a[0], d0) > MAX_START_GAP or days(b[0], d1) > MAX_STALE:
                    continue
                rets.append(b[1] / a[1] - 1)
            self._ucache[key] = (statistics.mean(rets), len(rets)) if rets else (None, 0)
        return self._ucache[key]

    def ticker(self, stem, label_date, horizon):
        """('graded', row) | ('immature', None) | ('pending', reason) | ('excluded', reason)."""
        if self.end is None:
            return "excluded", "NO_BENCHMARK_SERIES"
        if horizon is not None:
            target = (d2(label_date) + datetime.timedelta(days=horizon)).isoformat()
            if target > self.end:
                return "immature", None
        else:
            target = self.end
        if label_date > self.end:
            return "pending", "PENDING_NO_CLOSE_YET"
        if not stem:
            return "excluded", "NO_SERIES"
        s = self.prices.series(stem)
        if not s:
            return "excluded", "NO_SERIES"
        st = Prices.on_or_after(s, label_date)
        if st is None:
            return "excluded", "SERIES_ENDS_BEFORE_LABEL"
        if days(label_date, st[0]) > MAX_START_GAP:
            return "excluded", f"START_GAP>{MAX_START_GAP}d"
        en = Prices.on_or_before(s, target)
        if en is None or en[0] <= st[0]:
            if horizon is None:
                return "pending", "NO_FORWARD_CLOSE_YET"
            return "excluded", "NO_FORWARD_DATA"
        if days(en[0], target) > MAX_STALE:
            return "excluded", f"STALE_END>{MAX_STALE}d"
        b = self.bench_ret(st[0], en[0])
        if b is None:
            return "excluded", "NO_BENCHMARK_ON_DATES"
        u, un = self.universe_ret(st[0], en[0])
        r = en[1] / st[1] - 1
        return "graded", {"start": st[0], "end": en[0], "ret": r, "spy": b,
                          "x_spy": r - b, "x_univ": (r - u) if u is not None else None,
                          "univ_n": un}


def pct_stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0, "median_pct": None, "mean_pct": None, "hit_rate_pct": None}
    return {"n": len(vals),
            "median_pct": round(statistics.median(vals) * 100, 2),
            "mean_pct": round(statistics.mean(vals) * 100, 2),
            "hit_rate_pct": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1)}


def ranks(x):
    order = sorted(range(len(x)), key=lambda i: x[i])
    r = [0.0] * len(x)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x, y):
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    rx, ry = ranks(x), ranks(y)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / den, 3) if den else None


HKEYS = [f"h{h}" for h in HORIZONS] + ["to_date"]


def grade_all(calls, grader):
    """Per call, per horizon: status, call-level excess, ticker rows, exclusions."""
    graded = []
    for c in calls:
        per = {}
        for hk, h in zip(HKEYS, list(HORIZONS) + [None]):
            rows, excl, pending, immature = [], {}, 0, False
            for t in c["tickers"]:
                status, val = grader.ticker(t.get("stem"), c["label_date"], h)
                if status == "graded":
                    rows.append((t["ticker"], val))
                elif status == "immature":
                    immature = True
                    break
                elif status == "pending":
                    pending += 1
                else:
                    excl[val] = excl.get(val, 0) + 1
            if immature:
                per[hk] = {"status": "IMMATURE",
                           "matures_on": (d2(c["label_date"]) + datetime.timedelta(days=h)).isoformat()}
                continue
            xs = [v["x_spy"] for _, v in rows]
            xu = [v["x_univ"] for _, v in rows if v["x_univ"] is not None]
            per[hk] = {
                "status": ("INTERIM" if h is None else "GRADED") if rows else
                          ("PENDING" if pending and not excl else "NO_GRADABLE_TICKER"),
                "n_tickers": len(rows), "excluded": excl, "pending": pending,
                "x_spy_pct": round(statistics.mean(xs) * 100, 2) if xs else None,
                "x_univ_pct": round(statistics.mean(xu) * 100, 2) if xu else None,
                "_rows": rows,
            }
        graded.append((c, per))
    return graded


def aggregate(group):
    """group: list of (call, per). Returns per-horizon stats."""
    out = {}
    for hk in HKEYS:
        matured = [(c, p[hk]) for c, p in group if p[hk]["status"] != "IMMATURE"]
        immature = len(group) - len(matured)
        call_spy, call_univ, tk_spy, tk_univ, tickers, excl = [], [], [], [], set(), {}
        n_graded_calls = 0
        for c, h in matured:
            for k, v in h.get("excluded", {}).items():
                excl[k] = excl.get(k, 0) + v
            rows = h.get("_rows") or []
            if not rows:
                continue
            n_graded_calls += 1
            xs = [v["x_spy"] for _, v in rows]
            xu = [v["x_univ"] for _, v in rows if v["x_univ"] is not None]
            call_spy.append(statistics.mean(xs))
            if xu:
                call_univ.append(statistics.mean(xu))
            tk_spy.extend(xs)
            tk_univ.extend(xu)
            tickers.update(t for t, _ in rows)
        status = "INTERIM" if hk == "to_date" else ("GRADED" if n_graded_calls else "NOT_MATURED")
        out[hk] = {
            "status": status, "n_calls": len(group), "n_matured": len(matured),
            "n_immature": immature, "n_graded_calls": n_graded_calls,
            "n_tickers": len(tickers), "excluded_by_reason": dict(sorted(excl.items())),
            "call_level": {"vs_spy": pct_stats(call_spy), "vs_universe": pct_stats(call_univ)},
            "ticker_level": {"vs_spy": pct_stats(tk_spy), "vs_universe": pct_stats(tk_univ)},
        }
    return out


def heat_spearman(group):
    out = {}
    feats = {
        "impact": lambda s: s.get("impact"),
        "crowdedness": lambda s: s.get("crowdedness"),
        "capture": lambda s: s.get("capture"),
        "composite": lambda s: (s["impact"] * s["capture"] * (100 - s["crowdedness"])
                                if None not in (s.get("impact"), s.get("capture"), s.get("crowdedness"))
                                else None),
    }
    for hk in HKEYS:
        block = {}
        for bench, key in (("vs_spy", "x_spy_pct"), ("vs_universe", "x_univ_pct")):
            res = {}
            for name, f in feats.items():
                pairs = [(f(c["scores"] or {}), p[hk].get(key)) for c, p in group
                         if p[hk]["status"] not in ("IMMATURE",) and p[hk].get(key) is not None
                         and f(c["scores"] or {}) is not None]
                rho = spearman([a for a, _ in pairs], [b for _, b in pairs])
                res[name] = {"rho": rho, "n": len(pairs)}
            block[bench] = res
        block["status"] = "INTERIM" if hk == "to_date" else (
            "GRADED" if any(v["n"] for v in block["vs_spy"].values()) else "NOT_MATURED")
        out[hk] = block
    return out


def scenario_line(data: Path):
    with_p = resolved = total = 0
    for p in json_files(data / "chains"):
        c = load_json(p)
        for s in (c or {}).get("scenarios") or []:
            total += 1
            if s.get("probability_pct") is not None:
                with_p += 1
                if str(s.get("status") or "").upper() in RESOLVED_SCENARIO:
                    resolved += 1
    return {"scenarios": total, "with_probability": with_p, "resolved": resolved,
            "unscorable": with_p - resolved,
            "note": "no resolution is invented; a scenario is scorable only once its own file records it resolved"}


# ---------------------------------------------------------------- the run

def build_card(calls, graded, counts, run_date, graded_through, universe, data):
    superseded = {}
    last = {}
    for c in calls:
        key = (c["origin"], c["object_ref"])
        if key in last:
            superseded[last[key]] = c["id"]
        last[key] = c["id"]
    origins = {}
    for o in ORIGINS:
        group = [(c, p) for c, p in graded if c["origin"] == o]
        labels = {}
        for lab in sorted({c["label"] for c, _ in group}):
            labels[lab] = aggregate([(c, p) for c, p in group if c["label"] == lab])
        facets = {}
        fkeys = sorted({k for c, _ in group for k in (c["facets"] or {}) if k != "chain_id"})
        for fk in fkeys:
            for fv in sorted({str((c["facets"] or {}).get(fk)) for c, _ in group}):
                facets[f"{fk}={fv}"] = aggregate(
                    [(c, p) for c, p in group if str((c["facets"] or {}).get(fk)) == fv])
        block = {
            "examined": counts["examined"][o], "unlabeled": counts["unlabeled"][o],
            "undated": counts["undated"][o], "calls": len(group),
            "calls_current": sum(1 for c, _ in group if c["id"] not in superseded),
            "provenance": {k: sum(1 for c, _ in group if c["provenance"] == k)
                           for k in ("BACKFILL", "OBSERVED")},
            "labels": labels, "facets": facets,
        }
        if o == "IMPACT":
            block["untied"] = dict(sorted(counts["untied"].items()))
        if o == "HEAT":
            block["spearman"] = heat_spearman(group)
            block["ticker_source"] = {k: sum(1 for c, _ in group if c["ticker_source"] == k)
                                      for k in ("mapping_placements", "example_tickers", "none")}
        origins[o] = block
    per_call = []
    for c, p in graded:
        per_call.append({
            "id": c["id"], "origin": c["origin"], "object_ref": c["object_ref"],
            "label": c["label"], "label_date": c["label_date"], "provenance": c["provenance"],
            "superseded_by": superseded.get(c["id"]),
            "horizons": {hk: {k: v for k, v in h.items() if k != "_rows"} for hk, h in p.items()},
        })
    return {
        "v": 1,
        "generated_by": "tools/scorecard.py",
        "run_date": run_date,
        "graded_through": graded_through,
        "method": {
            "horizons_days": list(HORIZONS),
            "maturity": "a horizon is graded only when label_date + horizon <= graded_through; "
                        "to_date is always INTERIM",
            "benchmarks": {
                "spy": "SPY raw close over the same start and end dates as each ticker",
                "universe": "equal-weighted mean return, over the same dates, of every priced "
                            "ticker on any ACTIVE mapping placement on disk today",
            },
            "price_basis": PRICE_BASIS,
            "hit_rate": "share of values with excess > 0; for TOO_LATE, CROWDED and OVER_CROWDED a "
                        "LOW hit rate is the label being right",
            "call_level": "equal-weighted mean across the call's graded tickers",
            "freeze": "calls are frozen at first sight in calls.json; a changed label or score "
                      "appends a new call and never edits an old one",
            "backfill": "BACKFILL calls carry the label's date as it stands on disk at first run",
            "max_start_gap_days": MAX_START_GAP, "max_stale_days": MAX_STALE,
            "pooling": "never across origins (method section 8)",
        },
        "universe": {"tickers_priced": len(universe)},
        "origins": origins,
        "scenarios": scenario_line(data),
        "calls": per_call,
    }


def denominators(graded):
    graded_h = interim = immature = excluded = pending = 0
    for c, p in graded:
        for hk in HKEYS[:-1]:
            if p[hk]["status"] == "GRADED":
                graded_h += 1
            elif p[hk]["status"] == "IMMATURE":
                immature += 1
        if p["to_date"]["status"] == "INTERIM":
            interim += 1
        excluded += sum(p["to_date"].get("excluded", {}).values())
        pending += p["to_date"].get("pending", 0)
    return graded_h, interim, immature, excluded, pending


def fmt(s):
    if not s["n"]:
        return "n 0"
    return f"n {s['n']} med {s['median_pct']:+.2f}% mean {s['mean_pct']:+.2f}% hit {s['hit_rate_pct']:.0f}%"


def run(root: Path, run_date: str, dry_run=False, out=print):
    data = root / "data"
    calls_path, card_path = root / CALLS_REL, root / CARD_REL
    prices = Prices(data)
    store = load_calls(calls_path)           # raises Refusal
    committed = git_calls(root, "HEAD")
    bad = append_only_violations(committed, store["calls"] if store else [])
    if committed is not None and store is None:
        bad = [f"{CALLS_REL} is committed at HEAD but missing on disk"]
    if bad:
        raise Refusal("append-only violated against HEAD: " + "; ".join(bad[:5]))
    disk = Disk(root, prices)
    counts = {"examined": {o: 0 for o in ORIGINS}, "unlabeled": {o: 0 for o in ORIGINS},
              "undated": {o: 0 for o in ORIGINS}, "untied": {}, "unreadable": []}
    labels = current_labels(disk, counts)
    bench = prices.series(BENCH)
    graded_through = bench[0][-1] if bench else None
    examined = sum(counts["examined"].values())
    if examined == 0 and not store:
        out("scorecard: NOTHING GRADED. examined 0 objects (no chains, profiles, dives or "
            "appraisals on disk) and no calls store; nothing written")
        return 0
    calls, added = append_calls(store, labels, run_date, prices, graded_through)
    universe = disk.universe()
    grader = Grader(prices, universe, graded_through)
    graded = grade_all(calls, grader)
    card = build_card(calls, graded, counts, run_date, graded_through, universe, data)
    g, interim, immature, excluded, pending = denominators(graded)

    ex = counts["examined"]
    out(f"scorecard: examined {examined} objects (HEAT {ex['HEAT']} links, TIER {ex['TIER']} "
        f"profiles, DIVE {ex['DIVE']} dives, IMPACT {ex['IMPACT']} appraisals); untied IMPACT "
        f"{sum(counts['untied'].values())} {counts['untied']}; unlabeled "
        f"{sum(counts['unlabeled'].values())}; unreadable {len(counts['unreadable'])}")
    nb = sum(1 for c in added if c["provenance"] == "BACKFILL")
    out(f"scorecard: calls {len(calls)} on disk, +{len(added)} new ({nb} BACKFILL, "
        f"{len(added) - nb} OBSERVED); graded {g} call-horizons, interim {interim} calls, "
        f"immature {immature} call-horizons, excluded {excluded} ticker-rows (to_date), pending "
        f"{pending}; graded through {graded_through or 'NONE (no SPY series)'}; universe "
        f"{len(universe)} priced tickers")
    if g == 0 and interim == 0:
        out("scorecard: NOTHING GRADED: no call has a matured horizon or a to_date figure")
    for o in ORIGINS:
        for lab, a in card["origins"][o]["labels"].items():
            for hk in ("h30", "to_date"):
                h = a[hk]
                if h["n_graded_calls"]:
                    out(f"  {o:6} {lab:13} {hk:7} {h['status']:8} calls {h['n_graded_calls']:3}/"
                        f"{h['n_calls']:<3} vs SPY {fmt(h['call_level']['vs_spy'])} | vs universe "
                        f"{fmt(h['call_level']['vs_universe'])}")
    sp = card["origins"]["HEAT"]["spearman"]
    for hk in ("h30", "to_date"):
        line = ", ".join(f"{k} {v['rho']} (n {v['n']})" for k, v in sp[hk]["vs_spy"].items())
        out(f"  HEAT spearman {hk} {sp[hk]['status']} vs SPY: {line}")
    s = card["scenarios"]
    out(f"scenarios: {s['with_probability']} carry probabilities / {s['resolved']} resolved / "
        f"{s['unscorable']} therefore unscorable")
    out(f"caveat: {PRICE_BASIS}")
    if dry_run:
        out("scorecard: --dry-run, nothing written")
        return 0
    calls_path.parent.mkdir(parents=True, exist_ok=True)
    if added or not calls_path.exists():
        doc = {"v": 1, "generated_by": "tools/scorecard.py",
               "rule": "append-only: a call is frozen at first sight and never edited; a changed "
                       "label or score appends a new call",
               "calls": calls}
        calls_path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    card_path.write_text(json.dumps(card, indent=1, ensure_ascii=False) + "\n")
    return 0


def check(root: Path, against: str, out=print):
    try:
        store = load_calls(root / CALLS_REL)
    except Refusal as e:
        out(f"scorecard --check: FAIL {e}")
        return 1
    committed = git_calls(root, against)
    n = len(store["calls"]) if store else 0
    if committed is None:
        out(f"scorecard --check: {n} calls verified (ids and digests); append-only against "
            f"{against} NOT CHECKED (no git or no committed store at that revision)")
        return 0
    bad = append_only_violations(committed, store["calls"] if store else [])
    if bad:
        for b in bad[:20]:
            out(f"scorecard --check: FAIL {b}")
        return 1
    out(f"scorecard --check: OK {n} calls verified; all {len(committed)} calls committed at "
        f"{against} present and unchanged")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--date", default=None, help="override the UTC run date (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--against", default="HEAD")
    a = ap.parse_args(argv)
    root = Path(a.root)
    if a.check:
        return check(root, a.against)
    run_date = a.date or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    try:
        return run(root, run_date, a.dry_run)
    except Refusal as e:
        print(f"scorecard: REFUSED, nothing written: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
