"""A synthetic data/ tree at the finished campaign's scale, for the page-weight test.

Not a mock: every object here is padded to the byte size of the equivalent real object in
this repo, RE-MEASURED on 2026-08-30 with json.dumps(separators=(",", ":")). A fixture whose
dives are 5 KB would prove that a 5 KB dive fits, which nobody doubted. The whole point of
tools/tests/test_page_scale.py is that a REAL dive is ~60 KB and a REAL chain ~200 KB and
sixty plus ten of those do not fit, so the sizes below are the load-bearing part of the
file and the assertions in TestFixtureIsRealisticallySized keep them honest as the real
objects move.

Measured, per object (2026-08-30, second pass):
    chain   200,005   mean of the 9 data/chains/*.json that carry full heat AND scenarios
                      (range 135,806 humanoid-actuators .. 239,104 glp1-fill-finish; the
                      mean over all 11 files on disk is 172,434, dragged down by
                      ai-infrastructure and tibet-mega-dam, which were mapped before the
                      evidence bar and are not what a campaign chain now looks like)
    dive     59,665   data/stocks/VRT__ai-infrastructure.json (FINAL, red-teamed)
    market   25,909   mean of data/market/*.json over 281 files (max 47,328, VRT.json)
    impact   21,257   mean of data/impact/*.json over 36 files
    screen   19,880   mean of data/screens/*.json measured 2026-08-30 first pass; today's
                      three files mean 16,518 (max 23,700) and the higher figure is kept,
                      because the fixture may overstate and must never understate
    signal   10,205   mean of data/signals/*.json over 12 files
    candidate 2,750   mean row of data/radar/candidates.json (with campaign_record)
    occurrence  359   mean row of data/themes/occurrences.json

The first pass of this file measured a chain at 39,955 bytes — data/chains/ai-infrastructure
.json as it stood BEFORE heat blocks and scenarios existed. That single stale number is why
the forward projection reported "1.9 MB at full campaign scale, 91 KB under budget" while
the real page was already 2,112,067 bytes with the campaign two-thirds built. A chain is
now the heaviest object in the repo by a factor of three, so it is built here field by
field at the real per-field sizes rather than padded in one lump: the elastic projection in
app/build.py cuts SPECIFIC fields, and a chain whose weight all sits in `map_limitation`
would make any cut look free.

Per-link and per-scenario field sizes, measured over those same 9 chains (115 links,
45 scenarios) and reproduced by the generators below:

    link.evidence          2,502   heat.<leg>.rationale     ~580 each
    link.capture_inputs      575   heat.<leg>.evidence     ~1,321/item, ~2 items/leg
    link.bottleneck          352   heat.repricing_check       473
    link.role                270   scen.leading_indicators  2,014
    scen.links_moved       1,441   scen.narrative             742
    scen.invalidation_signs  356   scen.evidence              297

Deterministic: pad() reseeds per call, so the same fixture is byte-identical every run.
"""
import json
import random
from pathlib import Path


def pad(seed, n):
    random.seed(seed)
    words = ["capacity", "offtake", "margin", "supplier", "contract", "backlog", "utility",
             "throughput", "cadence", "spread", "inventory", "guidance", "discount",
             "terminal", "orderbook", "shipment", "conversion", "installed", "attach"]
    out = []
    size = 0
    while size < n:
        w = random.choice(words)
        out.append(w); size += len(w) + 1
    return " ".join(out)[:max(n, 1)]

def sz(v):
    return len(json.dumps(v, separators=(",", ":")).encode())

def fit(doc, target, field):
    """Pad one prose field until the document serializes to at least `target` bytes.

    Only grows, and only ever REPLACES the field with something longer. A generator
    already over target keeps the field it built, because the field is real content the
    page renders (`map_limitation` is drawn in the cortex chain drawer) and blanking it to
    hit a byte target would make the fixture lie in the one direction that matters. The
    size assertions in the test then report the overshoot, which is the outcome we want:
    the fixture must never be SMALLER than the real thing, or the budget it proves is not
    the real one.
    """
    original = doc.get(field)
    doc[field] = ""
    gap = target - sz(doc)
    if gap <= 2:
        doc[field] = original
        return doc
    doc[field] = pad(field + str(target), gap - 2)
    return doc

def ev(seed, n, chars=260):
    return [{"claim": pad(f"{seed}-{i}", chars), "tag": "VERIFIED",
             "source_name": f"source {i}", "source_date": "2026-08-20",
             "url": f"https://example.invalid/{seed}/{i}"} for i in range(n)]

def cited(seed, n, claim=446, excerpt=466, name=166, url=201):
    """Evidence at the 2026-08-30 bar: a verbatim `source_excerpt` beside every claim.

    A heat evidence item measured 1,321 bytes across the nine mature chains, of which the
    excerpt is 466 and the claim 446. `ev()` above predates the excerpt rule and is kept
    for the stores that never carried one; every chain generator below uses this.
    """
    return [{"claim": pad(f"{seed}-c{i}", claim),
             "source_excerpt": pad(f"{seed}-x{i}", excerpt),
             "tag": "VERIFIED",
             "source_name": pad(f"{seed}-n{i}", name),
             "source_date": "2026-08-20",
             "url": "https://example.invalid/" + pad(f"{seed}-u{i}", url).replace(" ", "/")}
            for i in range(n)]

def series_rows(n=559):
    rows, price = [], 40.0
    for i in range(n):
        price = round(price * (1 + ((i * 7919) % 41 - 20) / 1000.0), 2)
        rows.append([f"20{23 + i // 260:02d}-{1 + (i // 22) % 12:02d}-{1 + i % 28:02d}", price])
    return rows

# Re-measured 2026-08-30 (see the module docstring for provenance and for why the first
# CHAIN_BYTES was wrong by a factor of five).
CHAIN_BYTES = 200_005
SIGNAL_BYTES = 10_205
SCREEN_BYTES = 19_880
DIVE_BYTES = 59_665
IMPACT_BYTES = 21_257
MARKET_BYTES = 25_909


def _market_doc(t, i, rows, insider_rows):
    """One data/market/<T>.json. `insider_rows` is the only size knob and it is a block
    project_market strips, so the raw store grows to its real mean and the page does not."""
    return {
        "ticker": t, "cik": f"{i:010d}", "fetched_at": "2026-08-30T00:00:00Z",
        "price_status": "SINGLE_SOURCE", "tier": "T1",
        "prints": [{"close": rows[-1][1], "date": rows[-1][0], "source": "yfinance"}],
        "week52": {"high": 300, "low": 30},
        "series": {"as_of": rows[-1][0], "interval": "1d(24mo)+1w(prior)",
                   "source": "yfinance", "rows": rows},
        "fundamentals": {k: [[f"20{18 + n}-12-31", 1000000 * (n + 1)] for n in range(8)]
                         for k in ("revenue_fy", "net_income_fy", "operating_income_fy",
                                   "cost_of_revenue_fy", "operating_cashflow_fy",
                                   "capex_fy", "depreciation_fy", "sga_fy",
                                   "total_assets_fy", "equity_fy", "shares_fy")},
        "insider": {"window": ["2025-08-29", "2026-08-29"],
                    "rows": [{"filing_date": "2026-06-26", "insider": f"Person {k}",
                              "code": "A", "transaction_type": "award",
                              "security_title": "Class A common"}
                             for k in range(insider_rows)]},
        "pcs": {"axis_a": {"machine_admissible": True, "flags": []}},
        "legs": {"a": pad(t + "legs", 300)},
        "quality": {"piotroski": {"score": 6, "state": "SCORED"},
                    "beneish": {"score": -2.4, "state": "CLEAN"},
                    "altman": {"score": 8.8, "state": "SAFE"},
                    "reverse_dcf": {"implied_fcf_cagr": 0.32, "state": "SOLVED",
                                    "assumptions": {"discount_rate": 0.09,
                                                    "terminal_growth": 0.025,
                                                    "horizon_years": 5}},
                    "health": {"statement_fields_found": 18,
                               "statement_fields_needed": 20,
                               "periods_available": ["2018-12-31"]},
                    "as_of": "2026-08-30", "formulas": "piotroski/beneish/altman"},
    }


def write(root, *, themes=10, links_per_chain=13, profiles=200, dives=60, tickers=400,
          signals=30, screens=20, impacts=200, candidates=200, occurrences=900,
          ledger_lines=200, requests=2400, scenarios_per_chain=5):
    data = Path(root) / "data"
    for d in ("signals", "impact", "chains", "screens", "stocks", "market", "campaigns",
              "mappings", "companies", "radar", "themes", "feeds", "health", "calendar",
              "digest", "shadow"):
        (data / d).mkdir(parents=True, exist_ok=True)

    tick = lambda i: "T" + f"{i:04d}"
    chain_ids = [f"theme-{i:02d}" for i in range(themes)]

    # --- chains: the heaviest object in the repo. 13 links (real mean 12.8) each carrying
    # three heat legs with a rationale and ~2 cited-and-excerpted evidence items, a
    # repricing check, capture inputs and a bottleneck note; then 5 scenarios (real mean
    # 5.0) each carrying leading indicators with their check_basis prose, links_moved with
    # a `why` per moved link, a narrative, invalidation signs and scenario evidence. Every
    # field size below is the measured per-object mean in the docstring. Built field by
    # field rather than padded in one lump BECAUSE app/build.py's elastic projection cuts
    # named fields: a chain whose bulk sat in one padded string would make every cut look
    # free and would prove nothing about the real store.
    for ci, cid in enumerate(chain_ids):
        links = []
        for li in range(links_per_chain):
            lid = f"link-{li:02d}"
            # 2 items/leg at ~1,321 bytes each is the measured shape (impact 2.14,
            # crowdedness 1.89, capture 1.80 items per leg).
            leg = lambda name, r, c=446, x=466: {
                "score": 40 + (li * 7 + ci) % 55,
                "rationale": pad(f"{cid}{lid}{name}", r),
                "evidence": cited(f"{cid}{lid}{name}", 2, claim=c, excerpt=x)}
            links.append({
                "id": lid, "position": li + 1, "name": pad(f"{cid}{lid}nm", 40),
                "role": pad(f"{cid}{lid}role", 270),
                "upstream_of": [f"link-{li + 1:02d}"] if li < links_per_chain - 1 else [],
                "downstream_of": [f"link-{li - 1:02d}"] if li else [],
                "investability": "PURE_PLAYS_EXIST",
                "bottleneck": {"criticality": "CHOKE_POINT" if li % 5 == 0 else "MODERATE",
                               "note": pad(f"{cid}{lid}bn", 320)},
                "example_tickers": [tick(ci * 20 + li), tick(ci * 20 + li + 1)],
                "evidence": cited(f"{cid}{lid}map", 2, claim=400, excerpt=420,
                                  name=120, url=150),
                "capture_inputs": {k: pad(f"{cid}{lid}{k}", 166) for k in
                                   ("supply_concentration", "substitutability",
                                    "who_posted_the_margin")},
                "heat": {"verdict": "UNDISCOVERED", "money_corner": li == 2,
                         "as_of": "2026-08-29",
                         "ticker_refs": [tick(ci * 20 + li)],
                         "repricing_check": {
                             "legs_met": 2, "note": pad(f"{cid}{lid}rcn", 90),
                             "legs": [{"leg": f"leg_{k}", "met": k % 2 == 0,
                                       "basis": pad(f"{cid}{lid}rc{k}", 55)}
                                      for k in range(4)]},
                         "impact": leg("i", 581),
                         "crowdedness": leg("c", 623, c=400, x=400),
                         "capture": leg("v", 544, c=380, x=370)},
            })
        scen = [{"id": f"S{n}", "title": pad(f"{cid}t{n}", 45), "status": "OPEN",
                 "clock": "STRUCTURAL", "as_of": "2026-08-29",
                 "probability_pct": 20, "narrative": pad(f"{cid}s{n}", 800),
                 "links_moved": [{"link_id": f"link-{k:02d}", "direction": "UP",
                                  "magnitude": "LARGE", "why": pad(f"{cid}w{n}{k}", 130)}
                                 for k in range(6)],
                 "leading_indicators": [{"signal": pad(f"{cid}li{n}{k}", 130),
                                         "where_to_watch": pad(f"{cid}lw{n}{k}", 39),
                                         "check": None, "armed": False,
                                         "check_basis": pad(f"{cid}lb{n}{k}", 300)}
                                        for k in range(4)],
                 "invalidation_signs": [pad(f"{cid}inv{n}{k}", 110) for k in range(3)],
                 "evidence": cited(f"{cid}se{n}", 1, claim=120, excerpt=100, name=30,
                                   url=30),
                 } for n in range(1, scenarios_per_chain + 1)]
        chain_doc = {
            "id": cid, "signal_id": f"SIG-20260801-{ci:02d}", "title": f"Theme {ci}",
            "clock": "STRUCTURAL", "heat_as_of": "2026-08-29",
            "scenarios_as_of": "2026-08-29", "map_limitation": pad(f"{cid}ml", 2_588),
            "status": "SCENARIOS",
            "heat_health": {"examined": links_per_chain, "scored": links_per_chain,
                            "pending": 0, "errors": 0},
            "scenario_health": {"written": scenarios_per_chain, "armed": 0},
            "confidence_audit": {"verified": 30, "inferred": 12, "speculative": 0},
            "links": links, "scenarios": scen,
            "changelog": [{"ts": "2026-08-2%dT00:00:00Z" % (k % 10), "by": "ron",
                           "change": pad(f"{cid}cl{k}", 180)} for k in range(10)],
            "notes": [{"ts": "2026-08-20T00:00:00Z", "by": "ron",
                       "text": pad(f"{cid}n{k}", 1_350)} for k in range(5)],
        }
        (data / "chains" / f"{cid}.json").write_text(
            json.dumps(fit(chain_doc, CHAIN_BYTES, "map_limitation")))

    # --- signals
    for i in range(signals):
        sid = f"SIG-20260801-{i:02d}"
        sig_doc = {
            "id": sid, "title": f"Signal {i}", "lane": "GEO",
            "suggested_clock": "STRUCTURAL", "status": "CHAINED" if i < themes else "NEW",
            "chain_id": chain_ids[i] if i < themes else None,
            "thesis": pad(f"{sid}th", 900), "why_now": pad(f"{sid}wn", 620),
            "retail_gap": pad(f"{sid}rg", 560), "horizon_years": [2, 5],
            "unmappedness": {"score": 70, "rationale": pad(f"{sid}un", 730)},
            "occurrence": {"kind": "ANNOUNCED", "anchor_date": "2026-08-01",
                           "label": pad(f"{sid}oc", 200)},
            "evidence": ev(sid, 5, 400), "created_at": "2026-08-01",
            "updated_at": "2026-08-20", "review_by": "2026-11-01",
            "changelog": [{"ts": "2026-08-20T00:00:00Z", "by": "ron",
                           "change": pad(f"{sid}c{k}", 200)} for k in range(14)],
            "notes": [{"ts": "2026-08-20T00:00:00Z", "by": "ron",
                       "text": pad(f"{sid}n{k}", 250)} for k in range(5)],
        }
        (data / "signals" / f"{sid}.json").write_text(
            json.dumps(fit(sig_doc, SIGNAL_BYTES, "thesis")))

    # --- impact appraisals
    for i in range(impacts):
        occ = f"SIG-20260801-{i:02d}" if i < signals else f"CAND-20260801-{i:02d}"
        iid = f"IMP-20260830-{i:03d}"
        leg = lambda n: {"score": 55, "rationale": pad(f"{iid}{n}", 1400),
                         "evidence": ev(f"{iid}{n}", 3, 700)}
        doc = {"id": iid, "occurrence_id": occ, "as_of": "2026-08-30",
               "anchor_date": "2026-08-01", "appraised_by": "tally",
               "review_by": "2026-11-28", "impact_score": 61, "impact_band": "STRONG",
               "money_at_stake": {"band": "B10_100", "rationale": pad(iid + "m", 1400),
                                  "evidence": ev(iid + "m", 3, 700)},
               "public_reach": leg("r"), "capture_odds": leg("c"), "timing_fit": leg("t"),
               "confidence_audit": {"verified": 3, "inferred": 5, "speculative": 0, "null": 0},
               "ticker_refs": [], "notes": [{"ts": "2026-08-30T00:00:00Z", "by": "tally",
                                             "text": pad(iid + "n", 1200)}],
               "changelog": [{"ts": "2026-08-30T00:00:00Z", "by": "tally",
                              "change": pad(f"{iid}c{k}", 400)} for k in range(8)]}
        doc["money_at_stake"]["basis"] = ""
        gap = IMPACT_BYTES - sz(doc)
        doc["money_at_stake"]["basis"] = pad(iid + "b", max(gap - 2, 1))
        (data / "impact" / f"{iid}.json").write_text(json.dumps(doc))

    # --- market: 400 tickers, ~559 rows each plus the blocks the page never reads.
    # The pad-to-MARKET_BYTES field is `insider`, deliberately: it is one of the blocks
    # project_market strips entirely, so growing the fixture to the real 25,909-byte mean
    # makes the RAW store honest without inflating the page by a single byte. Padding a
    # projected field instead would let the fixture flatter the projection.
    rows = series_rows()
    for i in range(tickers):
        t = tick(i)
        insider_rows = 40
        while True:
            doc = _market_doc(t, i, rows, insider_rows)
            if sz(doc) >= MARKET_BYTES or insider_rows > 400:
                break
            insider_rows += 10
        (data / "market" / f"{t}.json").write_text(json.dumps(doc))

    # --- screens
    for i in range(screens):
        cid = chain_ids[i % themes]
        sid = cid if i < themes else f"{cid}__S{(i % 4) + 1}"
        bucket = []
        for k in range(12):
            t = tick((i % themes) * 20 + k)
            bucket.append({
                "ticker": t, "name": f"Company {t}", "tier": ["T1", "T2", "T3"][k % 3],
                "exchange": "NASDAQ", "cik": f"{k:010d}",
                "link_id": f"link-{k % links_per_chain:02d}",
                "link_id_basis": pad(f"{sid}{t}lb", 100),
                "money_corner": k == 0, "status": "CANDIDATE",
                "thesis_1line": pad(f"{sid}{t}", 130),
                "theme_revenue_exposure": {"pct": 30, "basis": pad(f"{sid}{t}tre", 140)},
                "fundamentals": {"summary": pad(f"{sid}{t}f", 110)},
                "crowdedness": {"state": "DARK", "pcs_score": 20,
                                "basis": pad(f"{sid}{t}cr", 100)},
                "earnings_nuggets": [{"quote": pad(f"{sid}{t}q{n}", 150), "form": "8-K",
                                      "accession": "0001-26-1", "url": "https://x.invalid"}
                                     for n in range(2)],
            })
        screen_doc = {
            "id": sid, "chain_id": cid, "scenario_id": None, "as_of": "2026-08-29",
            "universe_note": pad(sid + "un", 800), "data_gaps": [pad(sid + "g", 300)],
            "health": {"tickers_examined": 12, "fully_scored": 10, "pending": 2, "errors": 0},
            "buckets": {"pure_play": bucket[:6], "picks_and_shovels": bucket[6:9],
                        "second_order": bucket[9:], "hedge": []},
            "changelog": [{"ts": "2026-08-29T00:00:00Z", "by": "sieve",
                           "change": pad(f"{sid}c{k}", 220)} for k in range(10)],
            "notes": [],
        }
        (data / "screens" / f"{sid}.json").write_text(
            json.dumps(fit(screen_doc, SCREEN_BYTES, "universe_note")))

    # --- dives
    for i in range(dives):
        ci = i % themes
        cid, t = chain_ids[ci], tick(ci * 20 + (i // themes))
        d = {
            "ticker": t, "name": f"Company {t}", "chain_id": cid,
            "link_id": f"link-{i % links_per_chain:02d}",
            "link_id_basis": pad(t + "lb", 560),
            "issuer_id": f"ISS-{i:03d}", "listing_id": f"NASDAQ:{t}",
            "screen_ref": cid, "verdict": "WATCH", "clock": "STRUCTURAL",
            "tier": "T1", "status": "FINAL", "as_of": "2026-08-29",
            "created_at": "2026-08-29", "updated_at": f"2026-08-{(i % 28) + 1:02d}",
            "review_by": "2026-11-29", "shadow_ref": f"SH-{i:03d}",
            "scenario_ids": ["S1"], "price_ref": {"value": 100.0, "source": "series",
                                                  "as_of": "2026-08-28"},
            "price_source_note": pad(t + "psn", 620),
            "watch_triggers": [{"metric": "growth", "direction": "above", "level": "20%",
                                "basis": pad(f"{t}wt{k}", 300)} for k in range(5)],
            "bull": [pad(f"{t}bu{k}", 420) for k in range(3)],
            "bear": [pad(f"{t}be{k}", 420) for k in range(3)],
            "crowdedness": {"state": "COVERED", "basis": pad(t + "cw", 600)},
            "events": [{"date": "2026-07-29", "label": "Q2 print"}],
            "data_gaps": [pad(f"{t}dg{k}", 380) for k in range(7)],
            "confidence_audit": {"verified": 9, "inferred": 6, "speculative": 1, "null": 0},
            "earnings_quality": {"grade": "B", "basis": pad(t + "eq", 2560),
                                 "inputs": {"beneish": -2.4, "piotroski": 6, "altman": 8.8}},
            "filing_evidence": [{"quote": pad(f"{t}fe{k}", 185), "accession": "0001-26-1",
                                 "url": "https://sec.invalid/x", "form": "8-K",
                                 "filing_date": "2026-07-29", "tag": "VERIFIED"}
                                for k in range(9)],
            "filing_evidence_note": pad(t + "fen", 460),
            "valuation_snapshot": {
                "price": {"value": 100.0, "source": "series", "as_of": "2026-08-28"},
                "market_cap": {"value": "$98.3B", "source": "quality", "as_of": "2026-08-29"},
                "lines": [{"name": f"Line {k}", "value": "$1.0B", "tag": "VERIFIED",
                           "as_of": "2026-08-29", "basis": pad(f"{t}vl{k}", 180)}
                          for k in range(9)]},
            "what_is_priced_in": [{"expectation": pad(f"{t}wp{k}", 690),
                                   "evidence": pad(f"{t}wpe{k}", 320), "tag": "INFERRED"}
                                  for k in range(5)],
            "priced_in_summary": pad(t + "pis", 1800),
            "expectations_gap": {
                "market_implied_source": pad(t + "mis", 200),
                "solve_reproduced": pad(t + "sr", 255),
                "horizon_sensitivity": {"why_this_is_here": pad(t + "hs", 2280)},
                "rows": [{"driver": f"driver_{k}", "market_implied": 0.32, "mine": 0.19,
                          "percentile": 85, "structural_reason": pad(f"{t}eg{k}", 700),
                          "verification": pad(f"{t}egv{k}", 330)} for k in range(5)]},
            "independence_test": {"largest_disagreement": pad(t + "id1", 400),
                                  "why_the_gap_exists": pad(t + "id2", 780),
                                  "falsification": pad(t + "id3", 1165),
                                  "amended_by_red_team": True},
            "red_team": {"attacked_at": "2026-08-29T23:05:00Z", "by": "stocky",
                         "context_read": pad(t + "rtc", 345),
                         "verdict_before": "TOO_LATE", "verdict_after": "WATCH",
                         "verdict_survived": False,
                         "challenges": [{"dimension": f"DIM_{k}",
                                         "attack": pad(f"{t}rta{k}", 700),
                                         "outcome": pad(f"{t}rto{k}", 570)} for k in range(4)],
                         "pre_mortem": {"prompt": pad(t + "pmp", 300),
                                        "reasons": [pad(f"{t}pm{k}", 940) for k in range(3)]},
                         "amendments": pad(t + "rtam", 1450),
                         "surviving_bear_case": pad(t + "rtsb", 1800)},
            "changelog": [{"ts": "2026-08-29T22:45:00Z", "by": "ron", "kind": "CREATE",
                           "change": pad(f"{t}cl{k}", 730), "prior": None} for k in range(4)],
            "notes": [{"ts": "2026-08-29T00:00:00Z", "by": "ron",
                       "text": pad(f"{t}n{k}", 950)} for k in range(2)],
        }
        (data / "stocks" / f"{t}__{cid}.json").write_text(
            json.dumps(fit(d, DIVE_BYTES, "priced_in_summary")))

    # --- candidates, requests, ledger, feeds, occurrences, campaign/mappings/profiles
    (data / "radar" / "candidates.json").write_text(json.dumps({
        "as_of": "2026-08-30",
        "candidates": [{
            "id": f"CAND-20260801-{i:02d}", "title": f"Candidate {i}", "status": "AMBIENT",
            "family": ["GEO", "TECH", "POLICY"][i % 3], "why": pad(f"cand{i}", 265),
            "date": "2026-08-20", "source_name": f"Source {i}",
            "first_feed_item_id": f"f{i}", "first_feed_ts": "2026-08-20", "added_by": "nell",
            "campaign_record": {"dimensions": {k: pad(f"cr{i}{k}", 300) for k in
                                               ("size", "unmappedness", "timing",
                                                "reachability", "taste")},
                                "disposition": "ALTERNATE", "reason": pad(f"crr{i}", 400)},
            "changelog": [{"ts": "2026-08-20T00:00:00Z", "by": "nell",
                           "change": pad(f"cc{i}", 140)}],
        } for i in range(candidates)]}))
    (data / "requests.json").write_text(json.dumps({"requests": [
        {"id": f"REQ-2026-{i:04d}", "kind": "prices", "ticker": tick(i % tickers),
         "requested_by": pad(f"req{i}", 160), "requested_at": "2026-08-30T00:00:00Z",
         "by": "ron", "status": "FULFILLED" if i % 20 else "PENDING",
         "wrote": [f"data/market/{tick(i % tickers)}.json"], "attempts": 1}
        for i in range(requests)]}))
    (Path(root) / "data" / "ledger.md").write_text("\n".join(
        f"2026-08-{(i % 28) + 1:02d} 12:00Z | RUN | {pad('lg' + str(i), 1400)}"
        for i in range(ledger_lines)))
    (data / "feeds" / "latest.json").write_text(json.dumps({
        "as_of": "2026-08-30", "items": [
            {"id": f"f{i}", "title": pad(f"ft{i}", 108), "source": f"src{i % 20}",
             "family": ["GEO", "TECH", "POLICY"][i % 3], "ts": "2026-08-29"}
            for i in range(500)]}))
    (data / "themes" / "themes.json").write_text(json.dumps({
        "as_of": "2026-08-30", "calibration": {"per_theme": {}},
        "themes": [{"id": f"TH-{i:02d}", "label": f"Theme {i}",
                    "definition": pad(f"td{i}", 300), "match": {"any": ["x"]},
                    "changelog": []} for i in range(14)]}))
    # --- the campaign's own stores: 10 issuer maps and 200 company profiles.
    # These are the evidence-heaviest things on disk and none of them reaches the page:
    # build_campaign_ix projects them down to denominators and the O1 queue, which is the
    # pattern every other projection in app/build.py follows. They are written at full,
    # ugly size on purpose, so the test can prove that.
    campaign_themes = []
    for ci, cid in enumerate(chain_ids):
        issuers, listings, placements, coverage = [], [], [], []
        for pi in range(profiles // themes):
            iid, tk = f"ISS-{ci:02d}-{pi:02d}", tick(ci * 20 + pi)
            issuers.append({"issuer_id": iid, "name": f"Company {tk}"})
            listings.append({
                "listing_id": f"NASDAQ:{tk}", "issuer_id": iid, "ticker": tk,
                "exchange": "NASDAQ",
                "identity_evidence": [{
                    "claim": f"Company {tk} is listed as NASDAQ:{tk}", "tag": "VERIFIED",
                    "source_name": "NASDAQ issuer directory", "source_date": "2026-08-29",
                    "url": "https://example.invalid/official", "source_type":
                    "OFFICIAL_EXCHANGE", "legal_issuer": f"Company {tk}",
                    "exchange": "NASDAQ", "ticker": tk}]})
        for li in range(links_per_chain):
            lid = f"link-{li:02d}"
            for k in range(10):
                pi = (li * 5 + k) % (profiles // themes)
                placements.append({
                    "chain_id": cid, "link_id": lid, "status": "ACTIVE",
                    "issuer_id": f"ISS-{ci:02d}-{pi:02d}",
                    "role": pad(f"{cid}{lid}{k}role", 300),
                    "evidence": ev(f"{cid}{lid}{k}", 2, 500)})
            coverage.append({"link_id": lid, "status": "TARGET_MET",
                             "distinct_issuer_count": 10})
        (data / "mappings" / f"{cid}.json").write_text(json.dumps({
            "id": f"MAP-{cid}", "chain_id": cid, "as_of": "2026-08-29",
            "status": "COMPLETE", "target_issuers_per_link": 10, "issuers": issuers,
            "listings": listings, "placements": placements,
            "link_coverage": coverage, "changelog": []}))
        campaign_themes.append({"theme_id": f"THEME-{ci:02d}", "chain_id": cid,
                                "signal_id": f"SIG-20260801-{ci:02d}",
                                "title": f"Theme {ci}", "stage": "SELECTED",
                                "as_of": "2026-08-29", "blockers": []})
        for pi in range(profiles // themes):
            iid, tk = f"ISS-{ci:02d}-{pi:02d}", tick(ci * 20 + pi)
            places = [{"chain_id": cid, "link_id": f"link-{li:02d}"}
                      for li in range(links_per_chain)
                      if pi in {(li * 5 + k) % (profiles // themes) for k in range(10)}]
            o1 = pi < 6
            profile = {
                "issuer_id": iid, "issuer_name": f"Company {tk}", "status": "COMPLETE",
                "data_tier": ("T1", "T2", "T3")[pi % 3], "as_of": "2026-08-29",
                "opportunity": {"tier": "O1" if o1 else "O2",
                                "rank": ci * 6 + pi + 1 if o1 else None,
                                "basis": pad(f"{iid}ob", 900)},
                "business_summary": pad(f"{iid}bs", 2000),
                "exposure_summary": pad(f"{iid}es", 900),
                "metrics": {k: {"value": pi, "source": "edgar",
                                "basis": pad(f"{iid}{k}", 240)}
                            for k in ("revenue", "margin", "capex", "backlog")},
                "listing_refs": [f"NASDAQ:{tk}"], "placements": places,
                "changelog": [], "notes": []}
            if o1 and places:
                profile["selection_basis"] = {"screen_handoff": {
                    "screen_ref": cid, "chain_id": cid,
                    "link_id": places[0]["link_id"], "listing_id": f"NASDAQ:{tk}"}}
            (data / "companies" / f"{iid}.json").write_text(json.dumps(profile))
    (data / "campaigns" / "CAMP-20260829-01.json").write_text(json.dumps({
        "id": "CAMP-20260829-01", "title": "Ten-theme campaign", "as_of": "2026-08-29",
        "status": "ACTIVE", "selection_basis": {"as_of": "2026-08-29"},
        "themes": campaign_themes,
        "targets": {"theme_count": themes, "issuers_per_link": 10,
                    "completed_profiles_min": profiles, "profiles_per_theme_min": 10,
                    "o1_min": 30, "o1_max": 60},
        "completion": {"themes_selected": themes, "themes_complete": 0,
                       "distinct_mapped_issuers": profiles,
                       "completed_profiles": profiles,
                       "opportunity_tiers": {"O1": themes * 6,
                                             "O2": profiles - themes * 6, "O3": 0},
                       "o1_complete": themes * 6, "o1_final": dives, "per_theme": []},
        "blockers": []}))
    (data / "themes" / "occurrences.json").write_text(json.dumps({
        "as_of": "2026-08-30", "occurrences": [
            {"id": f"OCC-{i:04d}", "title": pad(f"ot{i}", 130), "source": f"src{i % 20}",
             "url": f"https://example.invalid/{i}", "ts": "2026-08-29", "origin": "feed",
             "origin_ref": f"f{i}", "family": "GEO", "theme_id": f"TH-{i % 14:02d}",
             "theme_basis": pad(f"tb{i}", 120), "theme_by": "rule:x"}
            for i in range(occurrences)]}))
    return data
