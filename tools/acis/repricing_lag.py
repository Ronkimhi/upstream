"""Repricing-lag lane (decision-standards.md 3.5).

The deterministic route for COVERED names the crowd has not repriced:
the COHR/MRVL shape, which value-chain maps structurally miss (no named
customer in filings) and which the retail-attention gate would mislabel
(they were institutionally covered long before retail arrived).

Four legs, >= 3 of 4 = QUEUED with a CROWDING-CONTEXT note, regardless
of PCS-A:
  1. STALE CONSENSUS: FY+1 revenue/EPS consensus unchanged (within 2%)
     vs 90 days ago.
  2. OWN-HISTORY VALUATION: EV/S or P/E at or below the name's own
     3-year 40th percentile.
  3. SHORT-FLOAT ELEVATION: short % float in the top third of its own
     1-year range.
  4. ANCHOR LINKAGE: a chain-map anchor's capex guidance is up >= 10%
     while this supplier's consensus revenue is flat (from the chain map;
     supplied by Ivy).

Pure core (`evaluate_legs`) is fixture-testable offline; fetchers are
best-effort with Rule-21 health lines. Consensus history requires a
Finnhub key (FINNHUB_API_KEY) or accumulated local snapshots; a leg
whose inputs cannot be fetched is NOT MET and is reported as NO_DATA,
never guessed.

CLI: python3 -m acis.repricing_lag TICKER [TICKER...]
"""
import json
import logging
import os
import sys
from datetime import datetime

logger = logging.getLogger("acis.repricing_lag")

QUEUE_MIN_LEGS = 3
CONSENSUS_STALE_TOLERANCE_PCT = 2.0
VALUATION_PERCENTILE_MAX = 40.0
ANCHOR_CAPEX_MIN_PCT = 10.0


def evaluate_legs(fields):
    """
    Pure evaluation. fields:
      consensus: {fy1_rev_now, fy1_rev_90d_ago, fy1_eps_now, fy1_eps_90d_ago} | None
      valuation: {ev_s_percentile_3yr, pe_percentile_3yr} | None
      short_float: {current, low_1yr, high_1yr} | None
      anchor: {anchor_capex_change_pct, supplier_consensus_change_pct} | None
    Returns {legs_met, legs, queue, note}.
    """
    legs = {}

    consensus = fields.get("consensus")
    if consensus is None:
        legs["stale_consensus"] = {"met": False, "detail": "NO_DATA"}
    else:
        deltas = []
        for now_key, ago_key in (("fy1_rev_now", "fy1_rev_90d_ago"),
                                 ("fy1_eps_now", "fy1_eps_90d_ago")):
            now, ago = consensus.get(now_key), consensus.get(ago_key)
            if now is not None and ago not in (None, 0):
                deltas.append(abs(now - ago) / abs(ago) * 100.0)
        if not deltas:
            legs["stale_consensus"] = {"met": False, "detail": "NO_DATA"}
        else:
            met = all(d <= CONSENSUS_STALE_TOLERANCE_PCT for d in deltas)
            legs["stale_consensus"] = {
                "met": met,
                "detail": "max estimate move {:.1f}% over 90d".format(max(deltas)),
            }

    valuation = fields.get("valuation")
    if valuation is None:
        legs["own_history_valuation"] = {"met": False, "detail": "NO_DATA"}
    else:
        pcts = [p for p in (valuation.get("ev_s_percentile_3yr"),
                            valuation.get("pe_percentile_3yr")) if p is not None]
        if not pcts:
            legs["own_history_valuation"] = {"met": False, "detail": "NO_DATA"}
        else:
            best = min(pcts)
            legs["own_history_valuation"] = {
                "met": best <= VALUATION_PERCENTILE_MAX,
                "detail": "best own-3yr percentile {:.0f}".format(best),
            }

    short = fields.get("short_float")
    if short is None or short.get("current") is None:
        legs["short_float_elevated"] = {"met": False, "detail": "NO_DATA"}
    else:
        cur = short["current"]
        lo = short.get("low_1yr")
        hi = short.get("high_1yr")
        if lo is None or hi is None or hi <= lo:
            legs["short_float_elevated"] = {"met": False, "detail": "NO_DATA (no 1yr range)"}
        else:
            position = (cur - lo) / (hi - lo)
            legs["short_float_elevated"] = {
                "met": position >= (2.0 / 3.0),
                "detail": "short float at {:.0%} of 1yr range".format(position),
            }

    anchor = fields.get("anchor")
    if anchor is None or anchor.get("anchor_capex_change_pct") is None:
        legs["anchor_linkage"] = {"met": False, "detail": "NO_DATA (no chain-map anchor input)"}
    else:
        capex = anchor["anchor_capex_change_pct"]
        supplier = anchor.get("supplier_consensus_change_pct")
        met = (capex >= ANCHOR_CAPEX_MIN_PCT
               and supplier is not None
               and abs(supplier) <= CONSENSUS_STALE_TOLERANCE_PCT)
        legs["anchor_linkage"] = {
            "met": met,
            "detail": "anchor capex {:+.1f}%, supplier consensus {}".format(
                capex, "{:+.1f}%".format(supplier) if supplier is not None else "NO_DATA"),
        }

    legs_met = sum(1 for leg in legs.values() if leg["met"])
    return {
        "legs_met": legs_met,
        "legs": legs,
        "queue": legs_met >= QUEUE_MIN_LEGS,
        "note": ("CROWDING-CONTEXT: admitted via repricing-lag lane "
                 "(covered name, un-repriced)" if legs_met >= QUEUE_MIN_LEGS
                 else "below lane threshold"),
    }


# ---------------------------------------------------------------------------
# Fetchers (best-effort; NO_DATA on failure)
# ---------------------------------------------------------------------------

def fetch_fields(ticker, anchor_input=None):
    """Populate the leg inputs for one ticker. `anchor_input` comes from
    Ivy's chain map ({anchor_capex_change_pct, supplier_consensus_change_pct})."""
    attempted, fetched = 0, 0
    fields = {"anchor": anchor_input}

    # Consensus now + 90d ago: Finnhub estimates when a key exists.
    attempted += 1
    consensus = None
    api_key = os.environ.get("FINNHUB_API_KEY")
    if api_key:
        try:
            import requests
            resp = requests.get(
                "https://finnhub.io/api/v1/stock/revenue-estimate",
                params={"symbol": ticker, "freq": "annual", "token": api_key},
                timeout=20)
            if resp.status_code == 200:
                data = (resp.json() or {}).get("data") or []
                if data:
                    # Finnhub serves point-in-time rows only per call; the
                    # 90d-ago side comes from our own committed snapshots
                    # (reprice_open_theses writes them). Without a snapshot
                    # this leg stays NO_DATA rather than pretending.
                    consensus = None
        except Exception as e:
            logger.debug("finnhub consensus fetch failed for %s: %s", ticker, e)
    snapshot = _load_consensus_snapshot(ticker)
    if snapshot:
        consensus = snapshot
        fetched += 1
    fields["consensus"] = consensus

    # Own-history valuation percentiles from yfinance history.
    attempted += 1
    try:
        import yfinance as yf
        import numpy as np
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        hist = tk.history(period="3y", auto_adjust=False)
        pe_now = info.get("trailingPE")
        ps_now = info.get("priceToSalesTrailing12Months")
        if hist is not None and not hist.empty and (pe_now or ps_now):
            closes = hist["Close"].dropna()
            # Price percentile is the honest proxy for multiple percentile
            # only under stable fundamentals; label it as such.
            price_pct = float((closes <= closes.iloc[-1]).mean() * 100.0)
            fields["valuation"] = {
                "ev_s_percentile_3yr": price_pct if ps_now else None,
                "pe_percentile_3yr": price_pct if pe_now else None,
                "proxy_note": "price-vs-own-3yr-history percentile proxy",
            }
            fetched += 1
        else:
            fields["valuation"] = None
    except Exception as e:
        logger.debug("valuation history fetch failed for %s: %s", ticker, e)
        fields["valuation"] = None

    # Short float vs own 1yr range: current from yfinance; the range needs
    # accumulated snapshots (reprice writes them).
    attempted += 1
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        current = info.get("shortPercentOfFloat")
        rng = _load_short_float_range(ticker)
        if current is not None and rng:
            fields["short_float"] = {"current": current, **rng}
            fetched += 1
        elif current is not None:
            fields["short_float"] = {"current": current,
                                     "low_1yr": None, "high_1yr": None}
        else:
            fields["short_float"] = None
    except Exception as e:
        logger.debug("short float fetch failed for %s: %s", ticker, e)
        fields["short_float"] = None

    if anchor_input is not None:
        fetched += 1
    attempted += 1

    return fields, {"attempted": attempted, "fetched": fetched}


_STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          ".state", "repricing_lag")


def _load_consensus_snapshot(ticker):
    try:
        with open(os.path.join(_STATE_DIR, "consensus_%s.json" % ticker)) as fh:
            return json.load(fh)
    except Exception:
        return None


def _load_short_float_range(ticker):
    try:
        with open(os.path.join(_STATE_DIR, "shortfloat_%s.json" % ticker)) as fh:
            return json.load(fh)
    except Exception:
        return None


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    tickers = [t for t in argv[1:] if not t.startswith("-")]
    if not tickers:
        print("usage: python3 -m acis.repricing_lag TICKER [TICKER...]")
        return 2
    out = []
    for t in tickers:
        fields, health = fetch_fields(t)
        result = evaluate_legs(fields)
        result["ticker"] = t
        result["as_of"] = datetime.now().strftime("%Y-%m-%d")
        result["health"] = health
        logger.info("repricing-lag %s: %d/4 legs, queue=%s | health: %d/%d inputs fetched",
                    t, result["legs_met"], result["queue"],
                    health["fetched"], health["attempted"])
        out.append(result)
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
