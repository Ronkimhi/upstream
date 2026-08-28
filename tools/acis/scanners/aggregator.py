"""Signal aggregation engine: combines scanner outputs into prioritized company list."""
from acis.config import SCANNER_SCORE_THRESHOLDS


def aggregate_signals(scanner_outputs):
    """
    Combine all scanner outputs into a composite score per company.

    Returns a sorted list of qualified company dicts (those meeting
    minimum thresholds for composite_score and signal_count).
    """
    min_score = SCANNER_SCORE_THRESHOLDS["min_composite_score"]
    min_signals = SCANNER_SCORE_THRESHOLDS["min_signal_count"]
    t1_score = SCANNER_SCORE_THRESHOLDS["tier1_min_score"]
    t1_signals = SCANNER_SCORE_THRESHOLDS["tier1_min_signals"]
    t2_score = SCANNER_SCORE_THRESHOLDS["tier2_min_score"]
    t2_signals = SCANNER_SCORE_THRESHOLDS["tier2_min_signals"]

    company_scores = {}

    for scanner_output in scanner_outputs:
        scanner_name = scanner_output["scanner_name"]
        for signal in scanner_output.get("signals", []):
            ticker = signal["ticker"]
            if ticker not in company_scores:
                company_scores[ticker] = {
                    "ticker": ticker,
                    "composite_score": 0,
                    "signal_count": 0,
                    "scanner_scores": {},
                    "signals": [],
                }
            company_scores[ticker]["composite_score"] += signal["score"]
            company_scores[ticker]["scanner_scores"][scanner_name] = signal["score"]
            company_scores[ticker]["signal_count"] += 1
            company_scores[ticker]["signals"].append(signal)

    # Filter by thresholds
    qualified = {
        ticker: data for ticker, data in company_scores.items()
        if data["composite_score"] >= min_score and data["signal_count"] >= min_signals
    }

    # Tier assignment + primary catalyst hint
    for data in qualified.values():
        if data["composite_score"] >= t1_score and data["signal_count"] >= t1_signals:
            data["tier"] = 1
        elif data["composite_score"] >= t2_score and data["signal_count"] >= t2_signals:
            data["tier"] = 2
        else:
            data["tier"] = 3

        # Determine primary catalyst hint by most common across signals
        hints = [s.get("catalyst_type_hint", "") for s in data["signals"] if s.get("catalyst_type_hint")]
        data["primary_catalyst_hint"] = max(set(hints), key=hints.count) if hints else None

    # Sort by composite score desc, then signal count desc
    sorted_companies = sorted(
        qualified.values(),
        key=lambda x: (x["composite_score"], x["signal_count"]),
        reverse=True,
    )

    return sorted_companies
