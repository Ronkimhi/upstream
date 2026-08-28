"""Output generation for Phase 2 scanner results: XLSX and JSON."""
import json
import os
import logging
from datetime import datetime
import pandas as pd
import numpy as np

logger = logging.getLogger("acis.scanners.output")


class _NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return None if np.isnan(obj) else float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        return super().default(obj)


def generate_scanner_xlsx(qualified_companies, input_companies, output_dir,
                          scan_metadata=None, all_scanner_outputs=None):
    """Generate 4-tab XLSX workbook for scanner results."""
    os.makedirs(output_dir, exist_ok=True)
    filename = "acis_signals_{}.xlsx".format(datetime.now().strftime("%Y%m%d"))
    path = os.path.join(output_dir, filename)

    # Build set of all tickers that had ANY signal (not just qualified)
    all_signaled_tickers = set()
    if all_scanner_outputs:
        for output in all_scanner_outputs:
            for signal in output.get("signals", []):
                all_signaled_tickers.add(signal["ticker"])

    # Build company_name lookup from input
    name_lookup = {c["ticker"]: c.get("company_name", "") for c in input_companies}

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        # Tab 1: Signal Summary
        summary_rows = []
        for comp in qualified_companies:
            scores = comp.get("scanner_scores", {})
            signal_summaries = [s.get("summary", "") for s in comp.get("signals", []) if s.get("summary")]
            summary_text = "; ".join(signal_summaries[:3])

            summary_rows.append({
                "ticker": comp["ticker"],
                "company_name": name_lookup.get(comp["ticker"], ""),
                "tier": comp["tier"],
                "composite_score": comp["composite_score"],
                "signal_count": comp["signal_count"],
                "sec_filing_score": scores.get("sec_filings", 0),
                "insider_score": scores.get("insider", 0),
                "options_score": scores.get("options", 0),
                "earnings_nlp_score": scores.get("earnings_nlp", 0),
                "commodity_score": scores.get("commodity", 0),
                "primary_catalyst_hint": comp.get("primary_catalyst_hint", ""),
                "signal_summary_text": summary_text,
            })
        summary_df = pd.DataFrame(summary_rows) if summary_rows else pd.DataFrame(
            columns=["ticker", "company_name", "tier", "composite_score", "signal_count"]
        )
        summary_df.to_excel(writer, sheet_name="Signal Summary", index=False)

        # Tab 2: Signal Details
        detail_rows = []
        for comp in qualified_companies:
            for signal in comp.get("signals", []):
                for ev in signal.get("evidence", []):
                    detail_rows.append({
                        "ticker": comp["ticker"],
                        "scanner_name": signal.get("signal_type", ""),
                        "score": signal["score"],
                        "signal_type": signal.get("signal_type", ""),
                        "catalyst_type_hint": signal.get("catalyst_type_hint", ""),
                        "summary": signal.get("summary", ""),
                        "source_type": ev.get("source_type", ""),
                        "source_name": ev.get("source_name", ""),
                        "source_date": ev.get("source_date", ""),
                        "source_ref": ev.get("source_ref", ""),
                        "confidence": ev.get("confidence", ""),
                    })
        detail_df = pd.DataFrame(detail_rows) if detail_rows else pd.DataFrame(
            columns=["ticker", "scanner_name", "score"]
        )
        detail_df.to_excel(writer, sheet_name="Signal Details", index=False)

        # Tab 3: No Signal Companies (zero signals from ANY scanner, not just qualified)
        if all_signaled_tickers:
            no_signal = [c for c in input_companies if c["ticker"] not in all_signaled_tickers]
        else:
            qualified_tickers = set(c["ticker"] for c in qualified_companies)
            no_signal = [c for c in input_companies if c["ticker"] not in qualified_tickers]
        no_signal_df = pd.DataFrame(no_signal) if no_signal else pd.DataFrame(
            columns=["ticker", "company_name"]
        )
        no_signal_df.to_excel(writer, sheet_name="No Signal Companies", index=False)

        # Tab 4: Scan Metadata
        meta = scan_metadata or {}
        meta_df = pd.DataFrame([{
            "scan_date": meta.get("scan_date", datetime.now().isoformat()),
            "lookback_days": meta.get("lookback_days", 180),
            "input_count": meta.get("input_count", len(input_companies)),
            "qualified_count": len(qualified_companies),
            "scanners_run": meta.get("scanners_run", "all"),
        }])
        meta_df.to_excel(writer, sheet_name="Scan Metadata", index=False)

    logger.info("Scanner XLSX written to %s", path)
    return path


def generate_signal_json(qualified_companies, input_companies,
                         lookback_days, output_dir):
    """Generate JSON output for Phase 3 consumption."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "signal_results.json")

    tier_counts = {1: 0, 2: 0, 3: 0}
    for comp in qualified_companies:
        tier = comp.get("tier", 3)
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    output = {
        "run_metadata": {
            "scan_date": datetime.now().isoformat(),
            "lookback_days": lookback_days,
            "input_company_count": len(input_companies),
            "qualified_company_count": len(qualified_companies),
            "tier_1_count": tier_counts.get(1, 0),
            "tier_2_count": tier_counts.get(2, 0),
            "tier_3_count": tier_counts.get(3, 0),
        },
        "qualified_companies": qualified_companies,
    }

    with open(path, "w") as f:
        json.dump(output, f, indent=2, cls=_NumpyEncoder)

    logger.info("Signal JSON written to %s", path)
    return path
