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
import importlib.util
import math
import re
from pathlib import Path as _Path

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


# ---------------------------------------------------------------- evidence excerpts
#
# method section 1, from 2026-08-30: every evidence item carries `source_excerpt`, a
# verbatim span from the fetched source that contains the claim, and every number the claim
# asserts must appear in that span.
#
# The measured cause: on the first day the impact stage ran at scale, 36 appraisals were
# written and adversarial verifiers that actually fetched every cited URL found that roughly
# 85% carried at least one evidence item whose source does not contain the claim. The
# pattern was always the same, and it is not laziness about citing: the agent wrote the
# claim from a search snippet or from memory, attached a real and topically relevant URL,
# and never opened the page. A URL check cannot see that. An excerpt can, because the
# excerpt is the part of the page you can only produce by having fetched it.
#
# The matcher is deliberately generous about FORM and strict about DIGITS. `4.9` and `4.90`,
# `21.6 million` and `21,600,000`, `$167bn` and `USD 167 billion` and `167 billion` are the
# same figure and must never fail. `200 gigawatts` against a source saying `474 GW` must.

def _load_normalizer():
    """check_screen.normalize, imported rather than copied.

    Three gates now claim to check something "verbatim" and they have to agree on what that
    means. A second copy of this function would drift, and the drift would surface as an
    excerpt one gate accepts and another calls fabricated.
    """
    path = _Path(__file__).resolve().parent / "check_screen.py"
    spec = importlib.util.spec_from_file_location("_check_screen_for_impact", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.normalize


_normalize_text = _load_normalizer()

# A span shorter than this cannot contain a claim; it is a gesture at one.
_EXCERPT_MIN_CHARS = 20

# Scale words and suffixes. A token emits BOTH its scaled value and its bare mantissa, on
# both sides, so a claim saying "8" matches an excerpt saying "$8 billion" and the reverse.
# Units are not audited here: this is a presence check on the figure, not a dimensional one.
_SCALES = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mn": 1e6, "mm": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "t": 1e12, "tn": 1e12, "trn": 1e12, "trillion": 1e12,
}
# Suffixes that are units rather than scales: the number stands as written.
_UNITS = frozenset({
    "%", "pct", "percent", "bps", "pp", "x", "bbl", "boe", "kg", "km", "mi", "ha",
    "days", "day", "months", "month", "years", "year", "weeks", "week", "hours", "hour",
    "units", "ships", "vessels", "nm", "c", "f", "usd", "eur", "gbp", "jpy", "cny", "krw",
    "gw", "mw", "kw", "tw", "gwh", "mwh", "kwh", "twh", "ghz", "mhz", "gb", "tb",
    "mt", "kt", "tonnes", "tonne", "tons", "ton", "tpa", "sqm", "sqkm", "acres", "ktpa",
})
# Integers a source may spell out. Asymmetric generosity: a claim writing "6" is satisfied
# by an excerpt writing "six", which is a real formatting difference and not a fabrication.
_NUMBER_WORDS = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
    13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen",
    18: "eighteen", 19: "nineteen", 20: "twenty",
}
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"])}
_MONTHS.update({m[:3]: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"])})

_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_TEXT_DATE_RE = re.compile(
    rf"\b(?:(?P<d1>\d{{1,2}})\s+)?(?P<mon>{_MONTH_ALT})\.?\s*(?:(?P<d2>\d{{1,2}})\s*,?\s*)?"
    rf"(?P<y>\d{{4}})\b", re.I)
# A digit run with whatever letters are glued to it on either side. `pre` is what makes
# `1SXP`, `LPDDR6`, `GLP-1` and `FY2027` identifiers rather than the numbers 1, 6, 1, 2027.
_NUM_TOKEN_RE = re.compile(r"""
    (?P<pre>[A-Za-z]+-?)?
    (?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    (?P<post>-?[A-Za-z%][A-Za-z0-9%/]*)?
""", re.X)
# A scale word separated by a space: "$21.6 million" is the same figure as "21,600,000".
_LOOSE_SCALE_RE = re.compile(
    r"^\s+(thousand|million|billion|trillion|bn|mn|tn|trn)\b", re.I)
_SCALE_HEAD_RE = re.compile(r"(trn|bn|mn|mm|tn|k|m|b|t)(?=[a-z])")


def _sig_figs(written: str) -> int:
    """Significant digits in a number AS THE CLAIM WROTE IT.

    This is the whole tolerance story. "about 30bn" asserts one significant digit, so a
    source saying 29.7 supports it. "200 gigawatts" also asserts one, and a source saying
    474 does not round to it at any precision. "19 days" asserts two, and 8 is not 19. The
    tolerance therefore scales with the precision the writer chose, rather than being one
    global epsilon that is either too loose for small integers or too tight for round
    headline figures.
    """
    s = written.replace(",", "").lstrip("0")
    if "." in s:
        return len((s.replace(".", "").lstrip("0")) or "0") or 1
    return len(s.rstrip("0") or "0") or 1


def _round_sig(value: float, digits: int) -> float:
    if value == 0:
        return 0.0
    exp = math.floor(math.log10(abs(value)))
    factor = 10 ** (digits - 1 - exp)
    return round(value * factor) / factor


def _num_match(claim_value: float, found: float, sig: int) -> bool:
    """True when `found` supports a claim written to `sig` significant digits."""
    if claim_value == found:
        return True
    scale = max(abs(claim_value), abs(found))
    if scale == 0:
        return True
    # Both must hold: the source must round to what the claim wrote, AND the gap must stay
    # inside 10%. The cap exists because one significant digit alone would let "30" accept
    # anything from 25 to 35, which is wider than any honest rounding of a cited figure.
    if abs(claim_value - found) / scale > 0.10:
        return False
    return abs(_round_sig(found, sig) - claim_value) <= abs(claim_value) * 1e-9 + 1e-9


def _date_spans(text: str) -> tuple[list[tuple[int, int, int | None]], list[tuple[int, int]]]:
    """Dates as (year, month, day-or-None) plus the character spans they occupy."""
    dates, spans = [], []
    for m in _ISO_DATE_RE.finditer(text):
        y, mo, d = (int(g) for g in m.groups())
        dates.append((y, mo, d))
        spans.append(m.span())
    for m in _SLASH_DATE_RE.finditer(text):
        a, b, y = (int(g) for g in m.groups())
        # Ambiguous by design: accept both readings rather than guessing a locale.
        dates.append((y, a, b))
        dates.append((y, b, a))
        spans.append(m.span())
    for m in _TEXT_DATE_RE.finditer(text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        day = m.group("d1") or m.group("d2")
        dates.append((int(m.group("y")), _MONTHS[m.group("mon").lower()],
                      int(day) if day else None))
        spans.append(m.span())
    return dates, spans


def scan_numerics(text: str) -> dict:
    """Numbers, identifiers and dates in one span of text.

    `numbers` are (written, [candidate values], significant digits). `identifiers` are digit
    runs glued to letters (`1SXP`, `LPDDR6`, `FY2027`, `Q2`): they are names, not
    quantities, so they are counted and reported but never numerically cross-checked.
    """
    if not isinstance(text, str):
        return {"numbers": [], "identifiers": [], "dates": []}
    dates, spans = _date_spans(text)
    numbers, identifiers = [], []
    for m in _NUM_TOKEN_RE.finditer(text):
        if any(a <= m.start("num") < b for a, b in spans):
            continue
        pre, num, post = m.group("pre"), m.group("num"), m.group("post")
        if pre:
            identifiers.append(m.group(0))
            continue
        value = float(num.replace(",", ""))
        candidates = [value]
        loose = _LOOSE_SCALE_RE.match(text[m.end():])
        if loose and not post:
            candidates.append(value * _SCALES[loose.group(1).lower()])
        if post:
            suffix = post.lstrip("-").lower().rstrip(".")
            if suffix in _SCALES:
                candidates.append(value * _SCALES[suffix])
            elif suffix in _UNITS or suffix.startswith("%"):
                pass
            else:
                head = _SCALE_HEAD_RE.match(suffix)
                if head:
                    candidates.append(value * _SCALES[head.group(1)])
                elif not post.startswith("-"):
                    identifiers.append(m.group(0))
                    continue
        numbers.append((m.group(0).strip(), candidates, _sig_figs(num)))
    return {"numbers": numbers, "identifiers": identifiers, "dates": dates}


def _haystack(text: str):
    """The excerpt side: every value it offers, its dates, and its normalized words."""
    scan = scan_numerics(text)
    values = [v for _, cands, _ in scan["numbers"] for v in cands]
    return values, scan["dates"], _normalize_text(text)


def _number_supported(candidates, sig, values, words) -> bool:
    for c in candidates:
        for v in values:
            if _num_match(c, v, sig):
                return True
        if float(c).is_integer() and int(c) in _NUMBER_WORDS:
            if re.search(rf"\b{_NUMBER_WORDS[int(c)]}\b", words):
                return True
    return False


def _date_supported(claim_date, excerpt_dates, values, words) -> bool:
    y, mo, d = claim_date
    same_year = [e for e in excerpt_dates if e[0] == y]
    if same_year:
        return any(e[1] == mo and (e[2] is None or d is None or e[2] == d)
                   for e in same_year)
    # No date in the excerpt to disagree with: the year alone carries it. A quoted body
    # sentence often has no dateline, and failing that would push writers to stop quoting.
    return any(_num_match(float(y), v, 4) for v in values)


def excerpt_text(item: dict) -> str:
    """`source_excerpt` as one string. A list of spans is allowed and is the honest shape
    when a claim synthesises two sentences of one page."""
    raw = (item or {}).get("source_excerpt")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list) and all(isinstance(s, str) for s in raw):
        return "\n".join(raw)
    return ""


def audit_excerpt(item: dict) -> dict:
    """method section 1 for one evidence item, as findings plus Rule 21 denominators.

    Returns {findings, has_excerpt, numerics, matched, unmatched, derived, identifiers}.
    Findings carry no object id; the caller prefixes them.
    """
    out = {"findings": [], "has_excerpt": False, "numerics": 0, "matched": 0,
           "unmatched": [], "derived": 0, "identifiers": 0}
    claim = (item or {}).get("claim")
    excerpt = excerpt_text(item)
    norm_excerpt = _normalize_text(excerpt)
    if len(norm_excerpt.strip()) < _EXCERPT_MIN_CHARS:
        out["findings"].append(
            "carries no source_excerpt. An evidence item without a verbatim span from the "
            "fetched source is not evidence (method section 1): the excerpt is the part of "
            "the page that can only be produced by having opened it")
        return out
    out["has_excerpt"] = True
    if norm_excerpt.strip() == _normalize_text(claim or "").strip():
        out["findings"].append(
            "source_excerpt is byte-identical to its own claim. An excerpt is copied out of "
            "the source, not restated from the claim")
        return out

    values, dates, words = _haystack(excerpt)
    scan = scan_numerics(claim or "")
    out["identifiers"] = len(scan["identifiers"])

    exempt, derived_findings = _derived_exemptions(item, scan, values, words)
    out["findings"].extend(derived_findings)
    out["derived"] = len(exempt)

    for written, candidates, sig in scan["numbers"]:
        out["numerics"] += 1
        if any(_num_match(candidates[0], e, sig) for e in exempt):
            out["matched"] += 1
            continue
        if _number_supported(candidates, sig, values, words):
            out["matched"] += 1
        else:
            out["unmatched"].append(written)
    for cd in scan["dates"]:
        out["numerics"] += 1
        if _date_supported(cd, dates, values, words):
            out["matched"] += 1
        else:
            out["unmatched"].append("%04d-%02d-%02d" % cd)

    if out["unmatched"]:
        out["findings"].append(
            f"claim asserts {out['unmatched']} which the source_excerpt does not contain. "
            f"Either the figure is not in the source, or the wrong span was quoted; a "
            f"figure the claim COMPUTES rather than quotes is declared in derived_from")
    return out


def _derived_exemptions(item, claim_scan, values, words):
    """Parse `derived_from` and return (exempt claim values, findings).

    The escape has to be narrow or it is a hole. Each entry names the derived figure as the
    claim wrote it, the quoted figures it came from, and how. The quoted figures are held to
    the same excerpt bar, so the exemption buys a computation, never an unsourced number.
    """
    raw = (item or {}).get("derived_from")
    if raw in (None, [], ""):
        return [], []
    findings, exempt = [], []
    if not isinstance(raw, list):
        return [], ["derived_from must be a list of {value, from, how} objects"]
    claim_values = {c[0] for _, cands, _ in claim_scan["numbers"] for c in [cands]}
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict) or not entry.get("how") \
                or not isinstance(entry.get("from"), list) or not entry["from"] \
                or entry.get("value") in (None, ""):
            findings.append(f"derived_from[{i}] needs value, a non-empty from list, and how")
            continue
        try:
            value = float(str(entry["value"]).replace(",", "").lstrip("$"))
        except ValueError:
            findings.append(f"derived_from[{i}].value {entry['value']!r} is not a number")
            continue
        if not any(abs(value - cv) <= 1e-9 for cv in claim_values):
            findings.append(
                f"derived_from[{i}].value {entry['value']!r} is not a figure this claim "
                f"states, so it exempts nothing")
            continue
        ok = True
        for src in entry["from"]:
            sub = scan_numerics(str(src))
            if not sub["numbers"]:
                findings.append(f"derived_from[{i}].from {src!r} states no figure")
                ok = False
                continue
            for _, cands, sig in sub["numbers"]:
                if not _number_supported(cands, sig, values, words):
                    findings.append(
                        f"derived_from[{i}].from {src!r} is not in the source_excerpt "
                        f"either. A derivation is only as sourced as its inputs")
                    ok = False
        if ok:
            exempt.append(value)
    return exempt, findings
