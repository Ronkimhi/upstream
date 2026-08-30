#!/usr/bin/env python3
"""The impact appraisal's computed fields, in one place. Stdlib only, offline.

Also hosts the venue-rule scanner and schema helpers shared by `check_impact.py` and
`validate.py`, so the gate and the validator walk ticker-level numbers the same way.

`docs/method.md` §0.2 defines four legs, a composite score, and a band that partitions the
space. The band is COMPUTED from the legs and never written by hand, which is the 2026-08-29
§3 amendment applied to a new stage on its first day rather than after the fact: three
`tibet-mega-dam` links shipped as UNDISCOVERED while their own scores said otherwise, because
the validator only checked that the word was in the enum.

That rule only holds if the writer and the gate compute the band the SAME way. If
`check_impact.py` re-derived the arithmetic from the prose in method.md, the two
implementations would agree until the day someone edited one of them, and the disagreement
would surface as a passing gate over a wrong band. So the arithmetic lives here, and
`impact_calibrate.py`, `check_impact.py` and `validate.py` all import it.

    from impact_score import compute, MONEY_BANDS, BANDS
"""
import datetime
import re

# Money is a band, never a point (method §0.2). The mapped value is what the composite uses;
# the band string is what a human reads and what the gate compares.
MONEY_BANDS = {
    "LT_1B": 15,      # under $1B
    "B1_10": 40,      # $1B to $10B
    "B10_100": 70,    # $10B to $100B
    "GT_100B": 90,    # over $100B
}
BIG_MONEY = {"B10_100", "GT_100B"}

BANDS = ("UNRANKED", "THIN", "LEAKY", "COMPETED", "REACHABLE", "PRIME")
LEGS = ("money_at_stake", "public_reach", "capture_odds", "timing_fit")
SCORED_LEGS = ("public_reach", "capture_odds", "timing_fit")

# method section 0.2: review_by is as_of + 90 calendar days on the COMPOUNDER clock.
REVIEW_BY_OFFSET_DAYS = 90

# Top-level keys an impact appraisal may carry. Anything else is undeclared schema.
IMPACT_TOP_KEYS = frozenset({
    "id", "occurrence_id", "as_of", "anchor_date", "appraised_by",
    "money_at_stake", "public_reach", "capture_odds", "timing_fit",
    "impact_score", "impact_band", "ticker_refs", "review_by",
    "confidence_audit", "unranked_reason", "notes", "changelog",
})

TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")

# Structured market facts keyed by ticker. Prose in evidence.claim is not scanned.
TICKER_METRIC_KEYS = frozenset({
    "share_price", "price", "close", "last", "open", "high", "low",
    "market_cap", "enterprise_value", "ev", "pe", "pe_ratio", "eps",
    "revenue", "dividend_yield", "beta", "shares_outstanding",
})

# Keys that are never ticker-level market facts, even beside a ticker field.
METADATA_KEYS = frozenset({
    "ticker", "id", "occurrence_id", "as_of", "anchor_date", "review_by",
    "impact_band", "impact_score", "tag", "source_name", "source_date", "url",
    "claim", "rationale", "basis", "evidence", "band", "ts", "by", "change",
    "prior", "text", "title", "kind", "status", "verified", "inferred",
    "speculative", "null", "unmappedness", "null_legs", "chain_id",
    "appraisal_id", "unranked_reason", "appraised_by", "source_type",
    "generated_by", "owner", "note", "official_source", "state", "listing_id",
    "issuer_id", "link_id", "screen_ref", "mapping_ref", "profile_ref",
    "market_ticker", "data_tier", "fetched_at", "tier", "cik", "price_status",
    "interval", "date", "source", "row_count", "forms", "accession",
})

# Numeric leaves on these paths are leg scores or audit counts, not market facts.
CLOSED_NUMERIC_PATH_RE = re.compile(
    r"^(?:impact_score|confidence_audit\.(?:verified|inferred|speculative|null)"
    r"|(?:money_at_stake|public_reach|capture_odds|timing_fit)\.score)$"
)

# Container keys that carry ticker-level numbers and are not part of the schema.
UNDECLARED_TICKER_CONTAINERS = frozenset({
    "ticker_facts", "ticker_data", "market_facts", "price_facts",
})


def _leg_score(appraisal, leg):
    """The 0-100 value of one leg, or None when the leg is NULL.

    A leg is an object, never a bare number, because §1 requires a rationale and a dated
    citation next to every score. `money_at_stake` carries `band` instead of `score`.
    """
    obj = (appraisal or {}).get(leg)
    if not isinstance(obj, dict):
        return None
    if leg == "money_at_stake":
        return MONEY_BANDS.get(obj.get("band"))
    v = obj.get("score")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100 else None


def leg_scores(appraisal):
    """{leg: score-or-None} for all four legs."""
    return {leg: _leg_score(appraisal, leg) for leg in LEGS}


def null_legs(appraisal):
    """The legs that could not be scored, in method order. This is the UNRANKED reason."""
    scores = leg_scores(appraisal)
    return [leg for leg in LEGS if scores[leg] is None]


def compute(appraisal):
    """Return {impact_score, impact_band, null_legs, legs} for one appraisal.

    `impact_score` is the geometric mean of the four legs, rounded to one decimal. Geometric
    rather than arithmetic so one weak leg drags the result without zeroing it: an occurrence
    moving $100B that no listed issuer touches is not four fifths of an opportunity.

    `impact_score` is None when any leg is NULL. A partial average would be a number, and a
    number gets sorted, compared and eventually quoted, which is exactly how an occurrence
    nobody has sized ends up ranked above one somebody did.
    """
    scores = leg_scores(appraisal)
    missing = [leg for leg in LEGS if scores[leg] is None]
    if missing:
        return {"impact_score": None, "impact_band": "UNRANKED",
                "null_legs": missing, "legs": scores}

    product = 1.0
    for leg in LEGS:
        product *= max(float(scores[leg]), 0.0)
    score = round(product ** 0.25, 1)

    band_str = (appraisal.get("money_at_stake") or {}).get("band")
    reach = scores["public_reach"]
    capture = scores["capture_odds"]

    # Order of evaluation, first match wins (method §0.2). THIN comes before LEAKY because a
    # sub-$1B pool is not worth arguing about reach for; LEAKY comes before REACHABLE because
    # it is the finding this whole stage exists to produce.
    if band_str == "LT_1B":
        band = "THIN"
    elif reach < 60:
        band = "LEAKY"
    elif band_str in BIG_MONEY and capture >= 60:
        band = "PRIME"
    elif capture >= 40:
        band = "REACHABLE"
    else:
        # reach is fine, capture is not: the money arrives at listed issuers and does not stay
        # as profit. Its own band, because the four-band draft left this case falling through
        # to whatever came last, which is the exact defect the 2026-08-29 §3 amendment fixed
        # (crowdedness <= 40 with impact < 60 was undefined, and three links shipped as
        # opportunities on the strength of a gap in a table).
        band = "COMPETED"
    return {"impact_score": score, "impact_band": band,
            "null_legs": [], "legs": scores}


def expected_review_by(as_of: str) -> str:
    """Return the sole valid review_by date for an appraisal as_of (method section 0.2)."""
    return (datetime.date.fromisoformat(str(as_of)[:10])
            + datetime.timedelta(days=REVIEW_BY_OFFSET_DAYS)).isoformat()


def _is_numeric(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_ticker(s) -> bool:
    return isinstance(s, str) and bool(TICKER_RE.match(s))


def _closed_numeric_path(path: str) -> bool:
    return bool(CLOSED_NUMERIC_PATH_RE.match(path))


def _values_close(a: float, b: float, rel: float = 0.01, abs_tol: float = 0.05) -> bool:
    return abs(a - b) <= max(abs(a) * rel, abs_tol)


def _market_numeric_paths(market: dict) -> list[tuple[str, float]]:
    """Canonical numeric paths in a market file that may source a ticker-level fact."""
    out: list[tuple[str, float]] = []
    for i, p in enumerate(market.get("prints") or []):
        if isinstance(p, dict) and _is_numeric(p.get("close")):
            out.append((f"prints[{i}].close", float(p["close"])))
    for i, row in enumerate((market.get("series") or {}).get("rows") or []):
        if isinstance(row, list) and len(row) >= 2 and _is_numeric(row[1]):
            out.append((f"series.rows[{i}][1]", float(row[1])))
    fund = market.get("fundamentals") or {}
    if isinstance(fund, dict):
        for key in ("market_cap", "pe_ratio", "eps", "revenue_fy", "beta", "shares_outstanding"):
            val = fund.get(key)
            if _is_numeric(val):
                out.append((f"fundamentals.{key}", float(val)))
    return out


def resolve_market_value(ticker: str, value: float, market: dict) -> str | None:
    """Return the market JSON path that carries this value, or None if unmatched."""
    for path, found in _market_numeric_paths(market):
        if _values_close(float(value), found):
            return path
    return None


def _dict_ticker(obj: dict) -> str | None:
    """Return a ticker symbol carried on ticker or market_ticker."""
    if not isinstance(obj, dict):
        return None
    for key in ("ticker", "market_ticker"):
        val = obj.get(key)
        if _is_ticker(val):
            return val
def scan_ticker_venue(appraisal: dict) -> dict:
    """Walk an appraisal tree for ticker-level numbers and undeclared ticker containers.

    Returns a dict with:
      undeclared_top_keys: top-level keys outside IMPACT_TOP_KEYS
      undeclared_containers: paths to forbidden ticker container keys
      ticker_metrics: [{path, ticker, metric, value}] for every numeric market fact found
      undeclared_tickers: tickers cited structurally but absent from ticker_refs
    """
    declared = {t for t in (appraisal.get("ticker_refs") or []) if _is_ticker(t)}
    undeclared_top = [k for k in appraisal if k not in IMPACT_TOP_KEYS and not str(k).startswith("_")]
    containers: list[str] = []
    metrics: list[dict] = []

    def note_metric(path: str, ticker: str, metric: str, value) -> None:
        metrics.append({"path": path, "ticker": ticker, "metric": metric, "value": value})

    def walk(obj, path: str, parent_ticker: str | None) -> None:
        if isinstance(obj, dict):
            sibling_ticker = parent_ticker
            found = _dict_ticker(obj)
            if found:
                sibling_ticker = found
            for key, val in obj.items():
                loc = f"{path}.{key}" if path else key
                if not path and key in UNDECLARED_TICKER_CONTAINERS:
                    containers.append(loc)
                if _is_ticker(key) and isinstance(val, dict):
                    for mk, mv in val.items():
                        if mk in METADATA_KEYS:
                            continue
                        if _is_numeric(mv) and not _closed_numeric_path(f"{loc}.{mk}"):
                            note_metric(f"{loc}.{mk}", key, mk, mv)
                    walk(val, loc, key)
                    continue
                if sibling_ticker and key not in METADATA_KEYS and _is_numeric(val):
                    if not _closed_numeric_path(loc):
                        note_metric(loc, sibling_ticker, key, val)
                    continue
                if key in TICKER_METRIC_KEYS and _is_numeric(val):
                    if sibling_ticker:
                        note_metric(loc, sibling_ticker, key, val)
                    elif _is_ticker(key):
                        note_metric(loc, key, key, val)
                    continue
                if key in UNDECLARED_TICKER_CONTAINERS and path:
                    containers.append(loc)
                walk(val, loc, sibling_ticker)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                walk(item, f"{path}[{i}]", parent_ticker)

    walk(appraisal, "", None)
    cited = {m["ticker"] for m in metrics}
    return {
        "undeclared_top_keys": undeclared_top,
        "undeclared_containers": containers,
        "ticker_metrics": metrics,
        "undeclared_tickers": sorted(cited - declared),
    }


def rank_key(appraisal, computed=None):
    """Sort key for the derived queue: highest score first, UNRANKED last, id as tiebreak.

    Deliberately NOT a function of unmappedness. The queue orders by money and RENDERS
    unmappedness beside it (method §0.2), because folding both into one number would let a
    money score quietly overwrite §0's selection rule while still looking like §0.
    """
    c = computed or compute(appraisal)
    s = c["impact_score"]
    return (0 if s is not None else 1, -(s or 0.0), str(appraisal.get("id") or ""))


def expected_queue_ids(apps: list[dict]) -> list[str]:
    """Appraisal ids in the deterministic rank_key order impact_calibrate must emit."""
    return [str(a.get("id") or "") for a in sorted(apps, key=rank_key)]


def scores_match(want, got, tol: float = 0.05) -> bool:
    """True when two impact_score values agree within tolerance, including both NULL."""
    if want is None:
        return got is None
    if got is None:
        return False
    return abs(float(want) - float(got)) <= tol
