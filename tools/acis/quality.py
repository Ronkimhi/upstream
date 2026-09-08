"""Earnings-quality, distress and market-implied-growth scores for a single name.

Runs in the GitHub Actions data plane only (`tools/fetch/fetch.py`, kind `quality`).
No Anthropic calls, no judgment: this module turns statements that are already on disk
into numbers that carry their own source and as_of, so the analyst agent never has to
compute one in its head.

The four scores exist because `docs/method.md` §7 needs them:
  - Piotroski F (0-9)       financial strength, the B/C half of the earnings-quality grade
  - Beneish M               manipulation screen; M > -1.78 is the review threshold
  - Altman Z                distress; context for the bear case, never a verdict on its own
  - implied growth          the reverse-DCF solve that fills the gap table's market column

Formulas come from `financetoolkit.models` (JerBouma/FinanceToolkit, MIT) so the arithmetic
is a vetted dependency rather than something we re-derived. See `docs/analyst-sources.md`.
We supply the inputs; it supplies the algebra.

Discipline (method §1):
  - A score with a missing input is null and names what was missing. Never interpolated.
  - Every result carries the denominator it examined (`inputs_found` of `inputs_needed`).
  - The reverse DCF's assumptions (discount rate, terminal growth, horizon) are written
    into the output. An assumption that is not on the page is a lie by omission.
"""
from __future__ import annotations

# Reverse-DCF assumptions. Written into every result; change them here, never inline.
DEFAULT_DISCOUNT_RATE = 0.09
DEFAULT_TERMINAL_GROWTH = 0.025
DEFAULT_HORIZON_YEARS = 5

BENEISH_REVIEW_THRESHOLD = -1.78   # M above this triggers manual review (method §7)


def _row(pairs):
    """[[date, value], ...] -> one-row DataFrame with PERIODS AS COLUMNS, oldest first.

    Periods-as-columns is financetoolkit's convention, not a stylistic choice: every
    period-over-period leg in its models is `.shift(1, axis=1)`. Hand it a Series indexed
    by date and it raises "No axis named 1". Verified against the installed source.
    """
    import pandas as pd
    if not pairs:
        return None
    clean = sorted((str(d), float(v)) for d, v in pairs
                   if d is not None and v is not None)
    if not clean:
        return None
    return pd.DataFrame([[v for _, v in clean]], columns=[d for d, _ in clean])


def _last(frame):
    """Last period's value out of a one-row frame (or a scalar passed straight through)."""
    if frame is None:
        return None
    try:
        v = frame.iloc[0, -1] if hasattr(frame, "iloc") else frame
        f = float(v)
    except (TypeError, ValueError, IndexError):
        return None
    return None if f != f else f  # NaN check without importing math


def _avg_assets(total_assets):
    """Piotroski wants average total assets, not period-end."""
    if total_assets is None or total_assets.shape[1] < 2:
        return total_assets
    return (total_assets + total_assets.shift(1, axis=1)) / 2


def _need(fields: dict, *names):
    """Return (values, missing) for the named fields. Missing is what is null."""
    missing = [n for n in names if fields.get(n) is None]
    return [fields.get(n) for n in names], missing


def _align(fields: dict, names):
    """Restrict the named fields to the fiscal periods ALL of them report.

    Without this, `revenue[t] / revenue[t-1]` can silently compare FY2025 against FY2023
    for one line item and FY2024 for another, because companyfacts does not guarantee that
    every concept is tagged in every year. The resulting score would be arithmetic on
    mismatched periods: wrong, and wrong in a way that looks fine. Fewer periods is the
    honest failure, so we intersect and let the change legs run short.
    """
    # Periods are matched by fiscal period, not by the exact end date. A 52/53-week filer
    # closes FY2025 on 2025-12-27 in one concept and reports 2025-12-31 in another, and
    # an instant fact dated a few days after the duration fact's end is the same period;
    # exact-string intersection read those as disjoint and returned "0 common fiscal
    # periods across 9 inputs" with every input present (ONT, FTEK, CECO, HII on
    # 2026-09-04). The key is the year-month of the end date shifted ten days forward,
    # so any end within the same fortnight around a month boundary collapses to one
    # period while annual periods, twelve months apart, never collide.
    from datetime import date, timedelta

    def period_key(col):
        try:
            d = date.fromisoformat(str(col)[:10]) + timedelta(days=10)
        except ValueError:
            return str(col)
        return f"{d.year:04d}-{d.month:02d}"

    keyed = {}
    for n in names:
        fr = fields[n]
        by_key = {}
        for col in fr.columns:
            k = period_key(col)
            if k not in by_key or str(col) > str(by_key[k]):
                by_key[k] = col  # two ends under one key: keep the later date
        keyed[n] = by_key
    common = set(keyed[names[0]])
    for n in names[1:]:
        common &= set(keyed[n])
    cols = sorted(common)
    out = {}
    for n in names:
        fr = fields[n][[keyed[n][k] for k in cols]]
        fr.columns = cols
        out[n] = fr
    return out, cols


def _stale_inputs(fields: dict, names, tolerance_days: int = 400):
    """Name the inputs whose own series ends long before the freshest one.

    An empty intersection is almost never "this filer reports nothing". It is one concept
    that went dead while its siblings kept running: ADBE's long_term_debt_fy stops at
    2009-11-27 while its other eight inputs run to 2025-11-28, so the periods every input
    shares is the empty set. The message the callers used to print, "0 common fiscal
    period(s) across the 9 inputs", named nothing and was true of both this and a genuinely
    empty filer, which is why 44 tickers sat PENDING_DATA with inputs_found == inputs_needed
    and nobody could tell why. Naming the outlier points at the FACT_MAP entry that needs
    widening, which is the actual repair.
    """
    from datetime import date

    ends = {}
    for n in names:
        fr = fields.get(n)
        if fr is None or len(fr.columns) == 0:
            continue
        ends[n] = max(str(c)[:10] for c in fr.columns)
    if not ends:
        return []
    newest = max(ends.values())
    out = []
    for n, end in sorted(ends.items()):
        try:
            lag = (date.fromisoformat(newest) - date.fromisoformat(end)).days
        except ValueError:
            continue
        if lag > tolerance_days:
            out.append(f"{n} ends {end}, {lag} days behind the freshest input "
                       f"({newest}); its concept likely went dead and FACT_MAP needs a "
                       "fresher candidate")
    return out



def piotroski(f: dict) -> dict:
    """F-score 0-9. Needs 4 consecutive annual periods to make the change legs honest."""
    from financetoolkit.models import piotroski_model as pm
    needed = ("net_income_fy", "operating_cashflow_fy", "total_assets_fy",
              "long_term_debt_fy", "current_assets_fy", "current_liabilities_fy",
              "shares_fy", "revenue_fy", "cost_of_revenue_fy")
    _vals, missing = _need(f, *needed)
    if missing:
        return {"score": None, "state": "PENDING_DATA", "missing": missing,
                "inputs_found": len(needed) - len(missing), "inputs_needed": len(needed)}
    aligned, cols = _align(f, needed)
    if len(cols) < 2:
        return {"score": None, "state": "PENDING_DATA",
                "missing": [f"only {len(cols)} common fiscal period(s) across the 9 inputs; "
                            "the change legs need at least 2"] + _stale_inputs(f, needed),
                "inputs_found": len(needed), "inputs_needed": len(needed),
                "common_periods": cols}
    f = aligned
    avg_ta = _avg_assets(f["total_assets_fy"])
    try:
        criteria = {
            "roa": pm.get_return_on_assets_criteria(f["net_income_fy"], avg_ta),
            "cfo": pm.get_operating_cashflow_criteria(f["operating_cashflow_fy"]),
            "d_roa": pm.get_change_in_return_on_asset_criteria(f["net_income_fy"], avg_ta),
            "accruals": pm.get_accruals_criteria(f["net_income_fy"], avg_ta,
                                                 f["operating_cashflow_fy"], f["total_assets_fy"]),
            "d_leverage": pm.get_change_in_leverage_criteria(f["long_term_debt_fy"], avg_ta),
            "d_current": pm.get_change_in_current_ratio_criteria(f["current_assets_fy"],
                                                                 f["current_liabilities_fy"]),
            # The library's contract is proceeds from stock ISSUED, scoring 1 at zero.
            # companyfacts does not reliably carry that concept, so we proxy with the
            # rise in share count, clipped at zero: a buyback is not an issuance and must
            # not score as one. The proxy is declared in the output, never silently.
            "shares": pm.get_number_of_shares_criteria(
                f["shares_fy"].diff(axis=1).clip(lower=0)),
            "gross_margin": pm.get_gross_margin_criteria(f["revenue_fy"], f["cost_of_revenue_fy"]),
            "asset_turnover": pm.get_asset_turnover_ratio_criteria(f["revenue_fy"], avg_ta),
        }
        score = _last(pm.get_piotroski_score(*criteria.values()))
    except Exception as e:  # noqa: BLE001 - a formula that blew up is not a zero
        return {"score": None, "state": "ERROR", "error": str(e)[:200],
                "inputs_found": len(needed), "inputs_needed": len(needed)}
    if score is None:
        return {"score": None, "state": "ERROR", "error": "score resolved to NaN",
                "inputs_found": len(needed), "inputs_needed": len(needed)}
    score = int(round(score))
    return {
        "score": score,
        "state": "STRONG" if score >= 7 else ("WEAK" if score <= 3 else "MIDDLING"),
        "criteria": {k: (None if _last(v) is None else bool(_last(v))) for k, v in criteria.items()},
        "inputs_found": len(needed), "inputs_needed": len(needed),
    }


def beneish(f: dict) -> dict:
    """M-score. Above -1.78 means look harder, not 'fraud'. It is a review trigger."""
    from financetoolkit.models import beneish_model as bm
    needed = ("receivables_fy", "revenue_fy", "cost_of_revenue_fy", "current_assets_fy",
              "ppe_net_fy", "total_assets_fy", "depreciation_fy", "sga_fy",
              "current_liabilities_fy", "long_term_debt_fy", "net_income_fy",
              "operating_cashflow_fy")
    _vals, missing = _need(f, *needed)
    if missing:
        return {"score": None, "state": "PENDING_DATA", "missing": missing,
                "inputs_found": len(needed) - len(missing), "inputs_needed": len(needed)}
    aligned, cols = _align(f, needed)
    if len(cols) < 2:
        return {"score": None, "state": "PENDING_DATA",
                "missing": [f"only {len(cols)} common fiscal period(s) across the 12 inputs; "
                            "every Beneish index is a year-over-year ratio"]
                           + _stale_inputs(f, needed),
                "inputs_found": len(needed), "inputs_needed": len(needed),
                "common_periods": cols}
    f = aligned
    try:
        m = _last(bm.get_beneish_m_score(
            bm.get_days_sales_in_receivables_index(f["receivables_fy"], f["revenue_fy"]),
            bm.get_gross_margin_index(f["revenue_fy"], f["cost_of_revenue_fy"]),
            bm.get_asset_quality_index(f["current_assets_fy"], f["ppe_net_fy"], f["total_assets_fy"]),
            bm.get_sales_growth_index(f["revenue_fy"]),
            bm.get_depreciation_index(f["depreciation_fy"], f["ppe_net_fy"]),
            bm.get_selling_general_and_administrative_expenses_index(f["sga_fy"], f["revenue_fy"]),
            bm.get_leverage_index(f["current_liabilities_fy"], f["long_term_debt_fy"], f["total_assets_fy"]),
            bm.get_total_accruals_to_total_assets(f["net_income_fy"], f["operating_cashflow_fy"],
                                                  f["total_assets_fy"]),
        ))
    except Exception as e:  # noqa: BLE001
        return {"score": None, "state": "ERROR", "error": str(e)[:200],
                "inputs_found": len(needed), "inputs_needed": len(needed)}
    if m is None:
        return {"score": None, "state": "ERROR", "error": "score resolved to NaN",
                "inputs_found": len(needed), "inputs_needed": len(needed)}
    return {
        "score": round(m, 3),
        "threshold": BENEISH_REVIEW_THRESHOLD,
        "state": "REVIEW" if m > BENEISH_REVIEW_THRESHOLD else "CLEAN",
        "inputs_found": len(needed), "inputs_needed": len(needed),
    }


def altman(f: dict, market_cap: float | None) -> dict:
    """Z-score. Needs a market cap, so it is null for any name without a live price."""
    from financetoolkit.models import altman_model as am
    needed = ("current_assets_fy", "current_liabilities_fy", "total_assets_fy",
              "retained_earnings_fy", "operating_income_fy", "total_liabilities_fy",
              "revenue_fy")
    _vals, missing = _need(f, *needed)
    if market_cap is None:
        missing = missing + ["market_cap"]
    if missing:
        return {"score": None, "state": "PENDING_DATA", "missing": missing,
                "inputs_found": len(needed) + 1 - len(missing), "inputs_needed": len(needed) + 1}
    aligned, cols = _align(f, needed)
    if not cols:
        return {"score": None, "state": "PENDING_DATA",
                "missing": ["no common fiscal period across the 7 statement inputs"]
                           + _stale_inputs(f, needed),
                "inputs_found": len(needed) + 1, "inputs_needed": len(needed) + 1}
    f = aligned
    try:
        wc = f["current_assets_fy"] - f["current_liabilities_fy"]
        z = _last(am.get_altman_z_score(
            am.get_working_capital_to_total_assets_ratio(wc, f["total_assets_fy"]),
            am.get_retained_earnings_to_total_assets_ratio(f["retained_earnings_fy"], f["total_assets_fy"]),
            am.get_earnings_before_interest_and_taxes_to_total_assets_ratio(
                f["operating_income_fy"], f["total_assets_fy"]),
            am.get_market_value_of_equity_to_book_value_of_total_liabilities_ratio(
                market_cap, f["total_liabilities_fy"]),
            am.get_sales_to_total_assets_ratio(f["revenue_fy"], f["total_assets_fy"]),
        ))
    except Exception as e:  # noqa: BLE001
        return {"score": None, "state": "ERROR", "error": str(e)[:200],
                "inputs_found": len(needed) + 1, "inputs_needed": len(needed) + 1}
    if z is None:
        return {"score": None, "state": "ERROR", "error": "score resolved to NaN",
                "inputs_found": len(needed) + 1, "inputs_needed": len(needed) + 1}
    return {
        "score": round(z, 2),
        "state": "DISTRESS" if z < 1.81 else ("GREY" if z < 2.99 else "SAFE"),
        "inputs_found": len(needed) + 1, "inputs_needed": len(needed) + 1,
    }


# Explicit-period lengths the reverse DCF is re-solved over, so the headline number is
# always accompanied by its sensitivity to the one assumption we choose most arbitrarily.
HORIZON_SENSITIVITY_YEARS = (5, 7, 10)


def implied_growth(fcf0, enterprise_value, discount_rate=DEFAULT_DISCOUNT_RATE,
                   terminal_growth=DEFAULT_TERMINAL_GROWTH, years=DEFAULT_HORIZON_YEARS) -> dict:
    """Reverse DCF: the FCF CAGR the current enterprise value already assumes.

    This is the market-implied column of the gap table. It is not a fair value and it is
    not a recommendation: it is the bet the price is making, stated as one number so the
    analyst can disagree with something specific.

    Bisection on g over [-50%, +100%]. Solved, not guessed.
    """
    assumptions = {"discount_rate": discount_rate, "terminal_growth": terminal_growth,
                   "horizon_years": years, "method": "bisection on FCF CAGR",
                   "tag": "SPECULATIVE"}
    if fcf0 is None or enterprise_value is None:
        return {"implied_fcf_cagr": None, "state": "PENDING_DATA",
                "missing": [n for n, v in (("fcf", fcf0), ("enterprise_value", enterprise_value))
                            if v is None],
                "assumptions": assumptions}
    if fcf0 <= 0:
        return {"implied_fcf_cagr": None, "state": "NOT_APPLICABLE",
                "reason": "base-year free cash flow is not positive; a growth rate on a "
                          "negative base is meaningless",
                "assumptions": assumptions}
    if enterprise_value <= 0:
        return {"implied_fcf_cagr": None, "state": "NOT_APPLICABLE",
                "reason": "enterprise value is not positive (net cash exceeds market cap)",
                "assumptions": assumptions}
    if discount_rate <= terminal_growth:
        return {"implied_fcf_cagr": None, "state": "ERROR",
                "reason": "discount rate must exceed terminal growth",
                "assumptions": assumptions}

    def pv(g):
        total = 0.0
        fcf = fcf0
        for t in range(1, years + 1):
            fcf = fcf * (1 + g)
            total += fcf / ((1 + discount_rate) ** t)
        terminal = fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
        return total + terminal / ((1 + discount_rate) ** years)

    def solve(horizon):
        """Bisection on g for one explicit-period length. None when out of range."""
        def pv_h(g):
            total = 0.0
            fcf = fcf0
            for t in range(1, horizon + 1):
                fcf = fcf * (1 + g)
                total += fcf / ((1 + discount_rate) ** t)
            terminal = fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
            return total + terminal / ((1 + discount_rate) ** horizon)
        lo_, hi_ = -0.50, 1.00
        if pv_h(lo_) > enterprise_value or pv_h(hi_) < enterprise_value:
            return None
        for _ in range(200):
            mid = (lo_ + hi_) / 2
            if pv_h(mid) < enterprise_value:
                lo_ = mid
            else:
                hi_ = mid
        return round((lo_ + hi_) / 2, 4)

    if pv(-0.50) > enterprise_value:
        return {"implied_fcf_cagr": None, "state": "OUT_OF_RANGE",
                "reason": "price implies decline steeper than -50% CAGR", "assumptions": assumptions}
    if pv(1.00) < enterprise_value:
        return {"implied_fcf_cagr": None, "state": "OUT_OF_RANGE",
                "reason": "price implies growth above +100% CAGR", "assumptions": assumptions}
    g = solve(years)
    # The headline number is one point on a curve, and the curve is steep. VRT on
    # 2026-08-29: 32.95% over a 5-year explicit period, 18.33% over 10 — same price, same
    # cash flow, same discount rate, different convention. The 2026-08-29 red team
    # overturned a TOO_LATE verdict that had treated the 5-year figure as "what the market
    # expects", when the market can equally be underwriting half that rate for twice as
    # long. The horizon is our choice, not the market's, and `assumptions.tag` has always
    # said SPECULATIVE. Reporting the band next to the point makes the sensitivity
    # impossible to miss and lets a gap table cite a range instead of a single artifact.
    by_horizon = {}
    for h in HORIZON_SENSITIVITY_YEARS:
        v = solve(h)
        if v is not None:
            by_horizon[str(h)] = v
    spread = (max(by_horizon.values()) - min(by_horizon.values())) if len(by_horizon) > 1 else None
    return {
        "implied_fcf_cagr": g,
        "state": "SOLVED",
        "base_fcf": fcf0,
        "enterprise_value": enterprise_value,
        "implied_by_horizon": by_horizon,
        "horizon_spread": round(spread, 4) if spread is not None else None,
        "horizon_note": (
            "implied_fcf_cagr is the solve at the default horizon only. implied_by_horizon "
            "shows the same solve over other explicit-period lengths; where the spread is "
            "wide, a gap against the headline number is a statement about the horizon "
            "convention as much as about the price, and a dive must say which it means."),
        "assumptions": assumptions,
    }


def compute_quality(fundamentals: dict, market_cap=None, enterprise_value=None,
                    as_of=None) -> dict:
    """Assemble the whole `quality` block for one ticker.

    `fundamentals` is data/market/<T>.json's widened fundamentals dict. Every list-shaped
    field becomes a Series; anything absent stays None and every score that needed it says
    so by name.
    """
    series_fields = ("revenue_fy", "net_income_fy", "operating_income_fy", "gross_profit_fy",
                     "cost_of_revenue_fy", "operating_cashflow_fy", "capex_fy",
                     "depreciation_fy", "total_assets_fy", "current_assets_fy",
                     "current_liabilities_fy", "total_liabilities_fy", "retained_earnings_fy",
                     "equity_fy", "receivables_fy", "inventory_fy", "sga_fy",
                     "long_term_debt_fy", "ppe_net_fy", "shares_fy")
    f = {k: _row(fundamentals.get(k)) for k in series_fields}

    ocf, capex = _last(f["operating_cashflow_fy"]), _last(f["capex_fy"])
    fcf = None if ocf is None or capex is None else ocf - abs(capex)

    present = sum(1 for k in series_fields if f[k] is not None)
    return {
        "as_of": as_of,
        "source": fundamentals.get("source"),
        "fundamentals_as_of": fundamentals.get("as_of"),
        "piotroski": {**piotroski(f),
                      "proxies": {"shares": "share-count increase clipped at zero; "
                                            "companyfacts lacks reliable issuance proceeds"}},
        "beneish": beneish(f),
        "altman": altman(f, market_cap),
        "reverse_dcf": implied_growth(fcf, enterprise_value),
        "derived": {"fcf": fcf, "market_cap": market_cap, "enterprise_value": enterprise_value},
        "health": {"statement_fields_found": present,
                   "statement_fields_needed": len(series_fields),
                   "periods_available": sorted(
                       {c for k in series_fields if f[k] is not None for c in f[k].columns})},
        "formulas": "financetoolkit.models (MIT); see docs/analyst-sources.md",
    }
