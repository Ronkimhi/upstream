"""Universe/screen profiles (F6/F14, 2026-08-28).

A profile owns everything sector-specific that used to live as globals in
acis.config: SIC targeting, cap band, liquidity floor, quality thresholds,
and the SEC keyword groups. config.py keeps only shared constants.

Active profile resolution: ACIS_PROFILE env var, else "ai_value_chain".
The commodity profile is BENCHED: runnable by name, never scheduled.
"""
import os

from acis.config_profiles import ai_value_chain, deep_value_commodity

PROFILES = {
    "ai_value_chain": ai_value_chain.PROFILE,
    "deep_value_commodity": deep_value_commodity.PROFILE,
}


def get_profile(name=None):
    name = name or os.environ.get("ACIS_PROFILE", "ai_value_chain")
    if name not in PROFILES:
        raise KeyError(
            "Unknown ACIS profile %r. Known: %s" % (name, sorted(PROFILES))
        )
    return PROFILES[name]
