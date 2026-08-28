"""PCS v2: the two-axis crowdedness standard (decision-standards.md 3.4).

Axis A (Retail Attention) is the GATE: search interest, WSB rank,
Stocktwits velocity/watchers, mainstream-media count. Renormalized over
non-NULL fields; >= 3 of 4 non-NULL required for machine admission
(else COVERAGE-THIN). Thresholds: >= 60 DARK, 40-59 EMERGING, < 40 CROWDED.

Axis B (Institutional Presence) is CONTEXT, never a blocker: analysts,
institutional ownership, index membership, short float. COHR and MRVL
were institutionally covered years before retail arrived; institutional
presence is information, not disqualification.

GATE ARMING: the gate may only BLOCK (Guy blocker #5) once the positive
controls are green. `gate_state()` evaluates the point-in-time fixtures in
tests/data/pcs_positive_controls.json itself: status must be "fetched"
and every control must pass. Until then every score prints ADVISORY.

Architecture: a PURE scoring core (score_axis_a / band_axis_b /
evaluate_positive_controls: deterministic, fixture-testable, no network)
plus best-effort fetchers that populate the field dicts. Every fetcher
failure is a NULL, never a guess; every run prints a Rule-21 health line.

CLI: python3 -m acis.crowdedness TICKER [TICKER...]
"""
import json
import logging
import os
import sys
from datetime import datetime

import requests

logger = logging.getLogger("acis.crowdedness")

FIXTURES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "tests", "data", "pcs_positive_controls.json")

DARK_MIN = 60
EMERGING_MIN = 40
MACHINE_ADMISSION_MIN_FIELDS = 3

NON_US_SUFFIX_TELLS = (".PA", ".SW", ".ST", ".SS", ".SZ", ".L", ".DE", ".MI",
                       ".AS", ".BR", ".LS", ".T", ".HK", ".TO", ".AX")


# ---------------------------------------------------------------------------
# Pure scoring core (no network; unit-tested offline)
# ---------------------------------------------------------------------------

def _score_trends(field):
    """field: {multiple: float, no_history: bool} or None."""
    if field is None:
        return None, 30
    mult = field.get("multiple")
    if field.get("no_history"):
        # All-zero search history: scored as the quiet band, tagged NO-HISTORY.
        return 30, 30
    if mult is None:
        return None, 30
    if mult < 1.5:
        return 30, 30
    if mult <= 3.0:
        return 12, 30
    return 0, 30  # > 3x also raises the CROWDED flag upstream


def _score_wsb(field):
    """field: {rank: int|None (None = not top-100), mentions_30d: int} or None."""
    if field is None:
        return None, 25
    rank = field.get("rank")
    mentions = field.get("mentions_30d", 0) or 0
    if rank is not None and rank <= 20:
        return -10, 25
    if rank is not None and rank <= 100:
        return 0, 25
    if mentions < 5:
        return 25, 25
    if mentions <= 20:
        return 10, 25
    return 0, 25


def _score_stocktwits(field):
    """field: {velocity: float|None, watchers: int|None} or None.
    Velocity is NULL until per-ticker history accumulates; a None velocity
    with real watchers scores on watchers alone at reduced availability."""
    if field is None:
        return None, 25
    velocity = field.get("velocity")
    watchers = field.get("watchers")
    if velocity is None and watchers is None:
        return None, 25
    if velocity is None:
        # Watchers-only read: half-weight availability so an unaccumulated
        # velocity cannot mint full darkness points.
        if watchers < 10_000:
            return 13, 13
        if watchers <= 50_000:
            return 5, 13
        return 0, 13
    if velocity > 3.0 or (watchers or 0) > 50_000:
        return 0, 25
    if velocity >= 1.5 or (watchers or 0) >= 10_000:
        return 10, 25
    return 25, 25


def _score_media(field):
    """field: {article_count_90d: int} or None."""
    if field is None:
        return None, 20
    count = field.get("article_count_90d")
    if count is None:
        return None, 20
    if count == 0:
        return 20, 20
    if count <= 3:
        return 8, 20
    return 0, 20


def score_axis_a(fields, ticker=""):
    """
    fields: {trends, wsb, stocktwits, media} where each value is the field
    dict (attempted-and-measured), or None (errored/uncovered = NULL).
    An attempted-but-empty fetch from a covering source is a MEASURED
    quiet value (absence of chatter IS the signal), so callers pass e.g.
    {"article_count_90d": 0}, never None, for that case.

    Returns {score, state, non_null_fields, machine_admissible, flags,
             sub_scores}.
    """
    scorers = {
        "trends": _score_trends,
        "wsb": _score_wsb,
        "stocktwits": _score_stocktwits,
        "media": _score_media,
    }
    earned, available = 0, 0
    non_null = 0
    sub_scores = {}
    flags = []

    for name, scorer in scorers.items():
        pts, avail = scorer(fields.get(name))
        sub_scores[name] = pts
        if pts is None:
            continue
        non_null += 1
        earned += pts
        available += avail

    trends_field = fields.get("trends") or {}
    if trends_field.get("multiple") is not None and trends_field["multiple"] > 3.0:
        flags.append("CROWDED-SEARCH-SPIKE")
    if trends_field.get("no_history"):
        flags.append("NO-HISTORY")
    if fields.get("argos_crowded"):
        flags.append("ARGOS-CROWDED")

    # Non-US locals can never be machine-admitted (scope cut): force
    # COVERAGE-THIN regardless of what the fetchers returned.
    is_non_us = any(str(ticker).upper().endswith(sfx) for sfx in NON_US_SUFFIX_TELLS)

    score = round(100.0 * earned / available, 1) if available > 0 else None
    machine_admissible = (
        non_null >= MACHINE_ADMISSION_MIN_FIELDS
        and score is not None
        and not is_non_us
    )

    if score is None:
        state = "COVERAGE-THIN"
    elif not machine_admissible:
        state = "COVERAGE-THIN"
    elif "ARGOS-CROWDED" in flags or "CROWDED-SEARCH-SPIKE" in flags:
        state = "CROWDED"
    elif score >= DARK_MIN:
        state = "DARK"
    elif score >= EMERGING_MIN:
        state = "EMERGING"
    else:
        state = "CROWDED"

    return {
        "score": score,
        "state": state,
        "non_null_fields": non_null,
        "machine_admissible": machine_admissible and state == "DARK",
        "flags": flags,
        "sub_scores": sub_scores,
    }


def band_axis_b(fields):
    """fields: {analyst_count, held_pct_institutions, index_member,
    short_pct_float}, any may be None. Context only: never blocks."""
    analysts = fields.get("analyst_count")
    inst = fields.get("held_pct_institutions")
    index_member = fields.get("index_member")

    known = [v for v in (analysts, inst) if v is not None]
    if not known and index_member is None:
        return {"band": "UNKNOWN", "fields": fields}

    saturated = ((analysts or 0) >= 15 or (inst or 0) >= 0.85
                 or index_member is True)
    undiscovered = ((analysts is not None and analysts <= 5)
                    and (inst is None or inst < 0.60)
                    and index_member is not True)
    band = ("SATURATED" if saturated
            else "UNDISCOVERED" if undiscovered
            else "COVERED")
    return {"band": band, "fields": fields}


# ---------------------------------------------------------------------------
# Gate arming (positive controls)
# ---------------------------------------------------------------------------

def evaluate_positive_controls(fixtures):
    """Pure evaluation of the point-in-time control rows. Returns
    (armed: bool, results: list of {name, expectation, passed, detail}).

    Controls (decision-standards 3.4):
    - dark controls (COHR/MRVL/AAOI/AXTI at their pre-crowd dates) must
      score DARK or >= 55
    - the COHR fixture must pass the repricing-lag lane (>= 3 legs)
    - VRT 2026-06 must score CROWDED
    - XFAB must be structurally unable to reach machine admission
    """
    from acis.repricing_lag import evaluate_legs

    results = []
    for row in fixtures.get("controls", []):
        name = row.get("name", "?")
        expectation = row.get("expect")
        kind = row.get("kind")
        passed = False
        detail = ""
        if kind == "axis_a":
            outcome = score_axis_a(row.get("fields", {}), ticker=row.get("ticker", ""))
            detail = "score=%s state=%s" % (outcome["score"], outcome["state"])
            if expectation == "dark":
                passed = (outcome["state"] == "DARK"
                          or (outcome["score"] is not None and outcome["score"] >= 55))
            elif expectation == "crowded":
                passed = outcome["state"] == "CROWDED"
            elif expectation == "never-machine-admissible":
                passed = not outcome["machine_admissible"]
        elif kind == "repricing_lag":
            outcome = evaluate_legs(row.get("fields", {}))
            detail = "legs=%d/4" % outcome["legs_met"]
            passed = outcome["legs_met"] >= 3 if expectation == "queued" else False
        results.append({"name": name, "expect": expectation,
                        "passed": passed, "detail": detail})

    armed = bool(results) and all(r["passed"] for r in results)
    return armed, results


def gate_state():
    """ARMED only when the fixtures file says status=fetched AND every
    control passes. Anything else (missing file, pending status, a failing
    control) = ADVISORY. Rule 21: state the reason next to the state."""
    try:
        with open(FIXTURES_PATH) as fh:
            fixtures = json.load(fh)
    except Exception as e:
        return "ADVISORY", "fixtures unreadable (%s)" % e
    if fixtures.get("status") != "fetched":
        return "ADVISORY", "positive-control fixtures status=%r (need 'fetched': a fetch-capable session must gather source+dated point-in-time rows)" % fixtures.get("status")
    armed, results = evaluate_positive_controls(fixtures)
    failed = [r["name"] for r in results if not r["passed"]]
    if not armed:
        return "ADVISORY", "positive controls failing: %s" % (failed or "no controls present")
    return "ARMED", "all %d positive controls pass" % len(results)


# ---------------------------------------------------------------------------
# Best-effort fetchers (each returns a field dict, or None = NULL)
# ---------------------------------------------------------------------------

USER_AGENT = {"User-Agent": "invest-lane crowdedness (ron-brain)"}


def fetch_trends(ticker):
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=0)
        pytrends.build_payload([ticker + " stock"], timeframe="today 3-m")
        df = pytrends.interest_over_time()
        if df is None or df.empty:
            return {"multiple": None, "no_history": True}
        series = df[ticker + " stock"]
        if series.sum() == 0:
            return {"multiple": None, "no_history": True}
        recent = series.tail(2).mean()   # weekly buckets: ~last 14d
        prior = series.head(max(len(series) - 2, 1)).mean()
        return {"multiple": round(float((recent + 1) / (prior + 1)), 2),
                "no_history": False}
    except Exception as e:
        logger.debug("trends fetch failed for %s: %s", ticker, e)
        return None


def fetch_wsb(ticker):
    try:
        resp = requests.get(
            "https://apewisdom.io/api/v1.0/filter/wallstreetbets/page/1",
            headers=USER_AGENT, timeout=20)
        if resp.status_code != 200:
            return None
        results = resp.json().get("results", [])
        for entry in results:
            if str(entry.get("ticker", "")).upper() == ticker.upper():
                return {"rank": int(entry.get("rank")),
                        "mentions_30d": int(entry.get("mentions", 0))}
        # Attempted, covered universe, ticker absent: MEASURED quiet.
        return {"rank": None, "mentions_30d": 0}
    except Exception as e:
        logger.debug("apewisdom fetch failed for %s: %s", ticker, e)
        return None


def fetch_stocktwits(ticker):
    try:
        resp = requests.get(
            "https://api.stocktwits.com/api/2/streams/symbol/{}.json".format(ticker),
            headers=USER_AGENT, timeout=20)
        if resp.status_code == 404:
            # No symbol page: measured absence of a retail crowd venue.
            return {"velocity": None, "watchers": 0}
        if resp.status_code != 200:
            return None
        data = resp.json()
        watchers = data.get("symbol", {}).get("watchlist_count")
        # Velocity needs accumulated history (state files); NULL until then.
        return {"velocity": None, "watchers": watchers}
    except Exception as e:
        logger.debug("stocktwits fetch failed for %s: %s", ticker, e)
        return None


MEDIA_DOMAIN_WHITELIST = (
    "wsj.com OR bloomberg.com OR cnbc.com OR reuters.com OR ft.com "
    "OR barrons.com OR forbes.com OR businessinsider.com OR fool.com "
    "OR marketwatch.com OR finance.yahoo.com"
)


def fetch_media_count(ticker, company_name):
    try:
        query = '"{}" "{}" ({})'.format(company_name, ticker, MEDIA_DOMAIN_WHITELIST)
        resp = requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={"query": query, "mode": "artlist", "format": "json",
                    "timespan": "90d", "maxrecords": 50},
            headers=USER_AGENT, timeout=30)
        if resp.status_code != 200:
            return None
        articles = resp.json().get("articles", [])
        return {"article_count_90d": len(articles)}
    except Exception as e:
        logger.debug("gdelt fetch failed for %s: %s", ticker, e)
        return None


def fetch_argos_crowded_flag(ticker, argos_dir=None):
    """ARGOS chatter is a CROWDED-flag input ONLY (7-day buckets over two
    tracked accounts; no 90-day baseline). Coverage with zero mentions is
    a measured 0.0x, never NULL. Returns True/False/None(uncovered)."""
    # Upstream port: optional local mirror at data/argos (absent = uncovered).
    argos_dir = argos_dir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "data", "argos")
    try:
        scan_path = os.path.join(argos_dir, "latest-scan.md")
        if not os.path.exists(scan_path):
            return None
        content = open(scan_path, encoding="utf-8", errors="ignore").read()
        if ticker.upper() not in content.upper():
            return None  # uncovered by the engine
        # Covered: a crude >3x heuristic needs bucket history; without it,
        # coverage alone never sets the flag.
        return False
    except Exception:
        return None


def fetch_axis_b(ticker):
    fields = {"analyst_count": None, "held_pct_institutions": None,
              "index_member": None, "short_pct_float": None}
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        fields["analyst_count"] = info.get("numberOfAnalystOpinions")
        fields["held_pct_institutions"] = info.get("heldPercentInstitutions")
        fields["short_pct_float"] = info.get("shortPercentOfFloat")
    except Exception as e:
        logger.debug("axis-b yfinance fetch failed for %s: %s", ticker, e)
    return fields


def compute_pcs(ticker, company_name=None):
    """Full PCS v2 for one ticker: fetch all fields, score, band, health."""
    company_name = company_name or ticker
    attempted, fetched = 0, 0
    fields = {}
    for name, fetcher, args in (
        ("trends", fetch_trends, (ticker,)),
        ("wsb", fetch_wsb, (ticker,)),
        ("stocktwits", fetch_stocktwits, (ticker,)),
        ("media", fetch_media_count, (ticker, company_name)),
    ):
        attempted += 1
        value = fetcher(*args)
        if value is not None:
            fetched += 1
        fields[name] = value
    fields["argos_crowded"] = fetch_argos_crowded_flag(ticker)

    axis_a = score_axis_a(fields, ticker=ticker)
    axis_b = band_axis_b(fetch_axis_b(ticker))
    state, reason = gate_state()

    result = {
        "ticker": ticker,
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "axis_a": axis_a,
        "axis_b": axis_b,
        "gate": {"state": state, "reason": reason},
        "health": {"attempted": attempted, "fetched": fetched,
                   "null": attempted - fetched},
    }
    logger.info(
        "PCS %s: axis-A %s (%s), axis-B %s, gate %s | health: %d/%d fields fetched",
        ticker, axis_a["score"], axis_a["state"], axis_b["band"], state,
        fetched, attempted)
    if fetched == 0:
        logger.error("PCS %s: ZERO of %d field fetches succeeded. This is a "
                     "FAILED run (COVERAGE-THIN by failure, not by quietness).",
                     ticker, attempted)
    return result


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    tickers = [t for t in argv[1:] if not t.startswith("-")]
    if not tickers:
        print("usage: python3 -m acis.crowdedness TICKER [TICKER...]")
        return 2
    out = [compute_pcs(t) for t in tickers]
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
