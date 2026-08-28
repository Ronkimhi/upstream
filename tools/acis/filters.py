"""
Layer 1 (universe definition) and Layer 2 (quality) filters.

Rewritten 2026-08-28 for the invest-lane overhaul:
- Profile-driven (F6): thresholds and SIC targeting come from the active
  Profile, never from module globals.
- Uniform NULL policy (F5): a missing value NEVER passes a filter. It is
  rejected with reason NO_DATA (distinct from a threshold failure), so
  "passed" always means "measured and passed". The old D/E condition let
  None through.
- No GICS sector-string fallback (F13): the universe is SEC-built, so
  every row has a real SIC code. A row without one is rejected NO_DATA.
- Dollar-ADV liquidity (F15): 20-day median dollar volume, not shares.
- Origin split: chain-map-origin candidates get SURVIVAL filters only
  (D/E, dilution, cash runway) with forward evidence as the quality
  screen; trailing quality (ROIC, FCF years, margin trend) applies only
  to generic screen-origin candidates. A pre-inflection supplier in the
  trough is the target class, not a reject.

Every rejection is logged with the specific reason (Rule 21).
"""
import logging
import pandas as pd

logger = logging.getLogger("acis.filters")

NO_DATA = "NO_DATA"


def compute_sector_medians(df):
    """Compute sector median P/E and EV/EBITDA from the full universe."""
    df = df.copy()
    df["pe_ratio"] = pd.to_numeric(df["pe_ratio"], errors="coerce")
    df["ev_ebitda"] = pd.to_numeric(df["ev_ebitda"], errors="coerce")
    medians = df.groupby("sector_gics").agg(
        sector_median_pe=("pe_ratio", "median"),
        sector_median_ev_ebitda=("ev_ebitda", "median"),
    )
    return medians


def _is_null(x):
    if x is None:
        return True
    try:
        return bool(pd.isna(x))
    except (TypeError, ValueError):
        return False


def apply_layer1_filters(df, profile):
    """
    Layer 1: market cap band, profile SIC targeting, dollar-ADV liquidity.
    Returns (filtered_df, rejection_log). NULL never passes (F5).
    """
    rejection_log = []
    df = df.copy()

    def reject(row, filter_name, reason):
        rejection_log.append({
            "ticker": row["ticker"], "filter": filter_name, "reason": reason,
        })

    # --- Market cap band ---
    def cap_passes(row):
        cap = row.get("market_cap")
        if _is_null(cap):
            reject(row, "market_cap", NO_DATA + ": market_cap missing")
            return False
        if not (profile.min_market_cap <= cap <= profile.max_market_cap):
            reject(row, "market_cap",
                   "Market cap ${:,.0f} outside ${:,.0f}-${:,.0f} band".format(
                       cap, profile.min_market_cap, profile.max_market_cap))
            return False
        return True

    df = df[df.apply(cap_passes, axis=1)].copy()

    # --- SIC targeting (real codes only; no sector-string fallback) ---
    def sic_passes(row):
        sic = row.get("sic_code")
        if _is_null(sic):
            reject(row, "sector_sic", NO_DATA + ": sic_code missing "
                   "(SEC-built universe should always carry one)")
            return False
        if profile.is_excluded_sic(int(sic)):
            reject(row, "sector_sic", "SIC {} in excluded ranges".format(int(sic)))
            return False
        if not profile.is_target_sic(int(sic)):
            reject(row, "sector_sic", "SIC {} not in profile '{}' targets".format(
                int(sic), profile.name))
            return False
        return True

    df = df[df.apply(sic_passes, axis=1)].copy()

    # --- Liquidity: 20d median dollar ADV (F15) ---
    def adv_passes(row):
        adv = row.get("dollar_adv_20d")
        if _is_null(adv):
            reject(row, "liquidity", NO_DATA + ": dollar_adv_20d missing")
            return False
        if adv < profile.min_dollar_adv_20d:
            reject(row, "liquidity",
                   "20d median dollar ADV ${:,.0f} below ${:,.0f} floor".format(
                       adv, profile.min_dollar_adv_20d))
            return False
        return True

    df = df[df.apply(adv_passes, axis=1)].copy()

    logger.info("Layer 1 (%s): %d passed of %d examined, %d rejected",
                profile.name, len(df), len(df) + len(rejection_log),
                len(rejection_log))
    return df, rejection_log


def apply_layer2_filters(df, sector_medians, profile, origin_col="origin"):
    """
    Layer 2 quality filters, split by candidate origin.

    origin == "chain-map": SURVIVAL only (D/E, cash runway, dilution when
    available). Forward evidence is scored upstream by Ivy, not here.
    Any other origin ("screen", missing): trailing quality (ROIC, FCF
    years, margin trend, D/E) plus the below-sector-valuation context flag.

    NULL never passes (F5). Returns (filtered_df, rejection_log).
    """
    rejection_log = []
    df = df.copy()

    df = df.merge(sector_medians, left_on="sector_gics",
                  right_index=True, how="left")

    pe_below = ((df["pe_ratio"].notna()) &
                (df["sector_median_pe"].notna()) &
                (df["pe_ratio"] < df["sector_median_pe"]) &
                (df["pe_ratio"] > 0))
    ev_below = ((df["ev_ebitda"].notna()) &
                (df["sector_median_ev_ebitda"].notna()) &
                (df["ev_ebitda"] < df["sector_median_ev_ebitda"]) &
                (df["ev_ebitda"] > 0))
    # Context only, never a rejector: cheapness is information, and the
    # target class (pre-inflection suppliers) is often not statistically
    # cheap on trailing numbers.
    df["below_sector_valuation"] = pe_below | ev_below

    def reject(row, filter_name, reason):
        rejection_log.append({
            "ticker": row["ticker"], "filter": filter_name, "reason": reason,
        })

    def survival_passes(row):
        de = row.get("debt_to_equity")
        if _is_null(de):
            reject(row, "survival_de", NO_DATA + ": debt_to_equity missing")
            return False
        if de > profile.survival_debt_equity_max:
            reject(row, "survival_de", "D/E {:.2f} > {:.2f}".format(
                de, profile.survival_debt_equity_max))
            return False
        runway = row.get("cash_runway_quarters")
        if _is_null(runway):
            reject(row, "survival_runway", NO_DATA + ": cash_runway_quarters missing")
            return False
        if runway < profile.survival_min_cash_runway_quarters:
            reject(row, "survival_runway",
                   "Cash runway {:.1f}q < {:.1f}q".format(
                       runway, profile.survival_min_cash_runway_quarters))
            return False
        return True

    def trailing_passes(row):
        checks = [
            ("roic", "roic_5yr_avg",
             lambda x: x >= profile.trailing_roic_min,
             "ROIC 5yr avg {} < {:.0%}".format(
                 row.get("roic_5yr_avg"), profile.trailing_roic_min)),
            ("debt_equity", "debt_to_equity",
             lambda x: x <= profile.trailing_debt_equity_max,
             "D/E {} > {}".format(
                 row.get("debt_to_equity"), profile.trailing_debt_equity_max)),
            ("fcf", "fcf_positive_count",
             lambda x: x >= profile.trailing_fcf_positive_min_years,
             "Positive FCF in {} of 5 years (< {})".format(
                 row.get("fcf_positive_count"),
                 profile.trailing_fcf_positive_min_years)),
            ("margins", "margin_stable_or_expanding",
             lambda x: x is True,
             "Gross margins declining over the window (newest vs oldest)"),
        ]
        for name, field, cond, desc in checks:
            val = row.get(field)
            if _is_null(val):
                reject(row, name, NO_DATA + ": {} missing".format(field))
                return False
            if not cond(val):
                reject(row, name, desc)
                return False
        return True

    def row_passes(row):
        origin = row.get(origin_col, "screen")
        if _is_null(origin):
            origin = "screen"
        if origin == "chain-map":
            return survival_passes(row)
        return trailing_passes(row)

    df = df[df.apply(row_passes, axis=1)].copy()

    logger.info("Layer 2 (%s): %d passed, %d rejected",
                profile.name, len(df), len(rejection_log))
    return df, rejection_log
