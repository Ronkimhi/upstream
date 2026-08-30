"""Canonical heat-map derived fields. Shared by validate.py and Ember's gate."""
from math import isfinite
import re

TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")


def score_from(heat: dict, key: str):
    """Numeric score under heat[key], otherwise None."""
    block = (heat or {}).get(key)
    value = block.get("score") if isinstance(block, dict) else None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None


def band_for(impact, crowdedness):
    """Method §3 attention bands. Boundaries partition 0..100."""
    if crowdedness is None or impact is None:
        return None
    if crowdedness > 80:
        return "OVER_CROWDED"
    if crowdedness >= 60:
        return "CROWDED"
    if crowdedness > 40:
        return "EMERGING"
    return "UNDISCOVERED" if impact >= 60 else "QUIET"


def money_corner(impact, crowdedness, capture):
    """True only for the exact method §3 target."""
    if None in (impact, crowdedness, capture):
        return None
    return impact >= 60 and crowdedness <= 40 and capture >= 60


def heat_bucket(heat: dict):
    """Return scored, pending, or errors from heat content and derived fields."""
    if not isinstance(heat, dict):
        return "errors"
    values = [score_from(heat, key) for key in ("impact", "crowdedness", "capture")]
    raw = [(heat.get(key) or {}).get("score") if isinstance(heat.get(key), dict) else object()
           for key in ("impact", "crowdedness", "capture")]
    if all(value is not None for value in values):
        want_band = band_for(values[0], values[1])
        want_corner = money_corner(*values)
        if heat.get("verdict") != want_band or heat.get("money_corner") != want_corner:
            return "errors"
        for key in ("impact", "crowdedness", "capture"):
            block = heat[key]
            if not str(block.get("rationale") or "").strip():
                return "errors"
            if not any(isinstance(item, dict) and item.get("source_date")
                       and str(item.get("url") or "").startswith(("http://", "https://"))
                       for item in block.get("evidence") or []):
                return "errors"
        if want_band in {"CROWDED", "OVER_CROWDED"} and not heat.get("repricing_check"):
            return "errors"
        return "scored"
    if all(value is None for value in raw):
        blocks = [heat.get(key) for key in ("impact", "crowdedness", "capture")]
        if (all(isinstance(block, dict) and str(block.get("basis") or "").strip()
                for block in blocks)
                and heat.get("verdict") is None and heat.get("money_corner") is None):
            return "pending"
    return "errors"


def scenario_bucket(scenario: dict, link_ids: set[str]):
    """Return scored only when a scenario has all required analytical content."""
    if not isinstance(scenario, dict) or not str(scenario.get("narrative") or "").strip():
        return "errors"
    moves = scenario.get("links_moved")
    indicators = scenario.get("leading_indicators")
    if not isinstance(moves, list) or not moves or not isinstance(indicators, list) or len(indicators) < 2:
        return "errors"
    if not scenario.get("invalidation_signs"):
        return "errors"
    for move in moves:
        if not isinstance(move, dict) or move.get("link_id") not in link_ids:
            return "errors"
        if move.get("direction") not in {"UP", "DOWN"} or move.get("magnitude") not in {"SMALL", "MEDIUM", "LARGE"}:
            return "errors"
        if not str(move.get("why") or "").strip():
            return "errors"
    for indicator in indicators:
        if not isinstance(indicator, dict) or not str(indicator.get("signal") or indicator.get("name") or "").strip():
            return "errors"
        check = indicator.get("check")
        if check is not None:
            if indicator.get("armed") is not True:
                return "errors"
            if not (isinstance(check, dict) and check.get("type") == "price"
                    and isinstance(check.get("ticker"), str) and TICKER_RE.fullmatch(check["ticker"])
                    and check.get("op") in {">", ">=", "<", "<="}
                    and isinstance(check.get("level"), (int, float))
                    and not isinstance(check.get("level"), bool) and isfinite(check["level"])):
                return "errors"
        elif indicator.get("armed") is True:
            return "errors"
    return "scored"
