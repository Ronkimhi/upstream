#!/usr/bin/env python3
"""Heat gate. Legacy seed records warn; today's campaign-era writes fail closed."""
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from heat_score import band_for, heat_bucket, instrument_bucket, money_corner, score_from  # noqa: E402
from ledger_lines import command_lines  # noqa: E402

SCORES = ("impact", "crowdedness", "capture")
INSTRUMENT_SCORES = ("crowdedness", "capture")
TICKER = re.compile(r"^[A-Z0-9.\-]{1,10}$")


def load(path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def campaign_era(root, chain_id):
    if (root / "data" / "mappings" / f"{chain_id}.json").exists():
        return True
    for path in (root / "data" / "campaigns").glob("CAMP-*.json") if (root / "data" / "campaigns").exists() else []:
        text = path.read_text(errors="ignore")
        if chain_id in text:
            return True
    return False


def same_day(value, today):
    return str(value or "").startswith(today)


def market_file(root, ticker):
    """The fetcher's dashed filename for a ticker, or None when no market file exists."""
    if not isinstance(ticker, str) or not TICKER.fullmatch(ticker):
        return None
    path = root / "data" / "market" / f"{ticker.replace('.', '-')}.json"
    return path if path.exists() else None


def shadow_candidates(root, chain_id, link):
    """Tickers a shadow row could be written on for this link: every price instrument,
    then example tickers, then ACTIVE placement listing tickers from the mapping."""
    tickers = [i.get("ticker") for i in link.get("price_instruments") or [] if isinstance(i, dict)]
    tickers += [t for t in link.get("example_tickers") or [] if isinstance(t, str)]
    mapping = load(root / "data" / "mappings" / f"{chain_id}.json", {}) or {}
    # market_ticker first, ticker as fallback: the same join tools/shadow_heat.py and
    # tools/book.py make, because a foreign listing's bare ticker ("BA.") is not the
    # fetcher's symbol ("BA.L") and is one character from another company's.
    listing = {l.get("issuer_id"): (l.get("market_ticker") or l.get("ticker"))
               for l in mapping.get("listings") or [] if isinstance(l, dict)}
    for placement in mapping.get("placements") or []:
        if (isinstance(placement, dict) and placement.get("status") == "ACTIVE"
                and placement.get("link_id") == link.get("id") and listing.get(placement.get("issuer_id"))):
            tickers.append(listing[placement["issuer_id"]])
    seen, out = set(), []
    for t in tickers:
        if isinstance(t, str) and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def pending_prices_request(root, tickers):
    """True when a PENDING prices request exists for any of the tickers."""
    rows = (load(root / "data" / "requests.json", {}) or {}).get("requests") or []
    return any(isinstance(r, dict) and r.get("kind") == "prices" and r.get("status") == "PENDING"
               and r.get("ticker") in tickers for r in rows)


def shadow_state(root, chain_id, heat_as_of, link, shadow_rows):
    """One of: rows (graded rows exist for this call), pending (nothing priceable yet but a
    request is queued), none_priceable (no candidate has a market file and nothing queued),
    no_tickers (the link names no ticker at all), missing (priceable tickers, no rows)."""
    lid = link.get("id")
    rows = [r for r in shadow_rows if isinstance(r, dict) and r.get("origin") == "HEAT_OVER_CROWDED"
            and r.get("chain_id") == chain_id and r.get("link_id") == lid
            and str(r.get("verdict_date") or "") == str(heat_as_of or "")]
    if rows:
        return "rows"
    candidates = shadow_candidates(root, chain_id, link)
    if not candidates:
        return "no_tickers"
    if any(market_file(root, t) for t in candidates):
        return "missing"
    return "pending" if pending_prices_request(root, candidates) else "none_priceable"


def main():
    argv = sys.argv[1:]
    root = Path(argv[argv.index("--root") + 1]).resolve() if "--root" in argv else Path(__file__).resolve().parent.parent
    today = argv[argv.index("--date") + 1] if "--date" in argv else datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    failures, notes, examined = [], [], 0
    chains = []
    for p in sorted((root / "data" / "chains").glob("*.json")):
        if not p.name.startswith("_"):
            item = load(p)
            if isinstance(item, dict):
                chains.append(item)
    def instrument_scored_today(chain):
        for link in chain.get("links") or []:
            heat = link.get("heat") if isinstance(link, dict) else None
            inst = heat.get("instrument") if isinstance(heat, dict) else None
            if isinstance(inst, dict) and same_day(inst.get("as_of"), today):
                return True
        return False
    # A price-instrument block scored after the chain's heat run (method section 3, 2026-09-13)
    # carries its own as_of and leaves heat_as_of on the day the issuers were scored. It is
    # still a heat write, so it binds the same same-day ledger, calibration and health rules.
    touched = [c for c in chains if same_day(c.get("heat_as_of"), today) or instrument_scored_today(c)]
    if not touched:
        print(f"check_heat: NOT RUN TODAY ({today}). 0 heat runs, {len(chains)} chain(s) examined.")
        return 0
    ledger = (root / "data" / "ledger.md").read_text() if (root / "data" / "ledger.md").exists() else ""
    ember_lines = command_lines(ledger.splitlines(), ("run heat ",), day=today)
    calibration = load(root / "data" / "chains" / "_ember-log.json", {}) or {}
    calibration_today = same_day((calibration.get("calibration") or {}).get("generated_at"), today)
    shadow_rows = (load(root / "data" / "shadow" / "book.json", {}) or {}).get("rows") or []
    for chain in touched:
        cid, links = chain.get("id", "<unknown>"), chain.get("links") or []
        strict = campaign_era(root, cid)
        examined += len(links)
        bad, buckets = [], {"scored": 0, "pending": 0, "errors": 0}
        ibuckets = {"examined": 0, "scored": 0, "pending": 0, "errors": 0}
        shadow = {"over_crowded": 0, "rows": 0, "pending": 0, "none_priceable": 0, "no_tickers": 0, "missing": 0}
        for link in links:
            lid, heat = link.get("id", "<unknown>"), link.get("heat")
            before = len(bad)
            if not isinstance(heat, dict):
                bad.append(f"{lid}: missing heat block")
                buckets["errors"] += 1
                continue
            values, is_null = {}, False
            for key in SCORES:
                score = heat.get(key)
                if not isinstance(score, dict):
                    bad.append(f"{lid}.{key}: missing score object")
                    continue
                value = score.get("score")
                if value is None:
                    is_null = True
                    if not str(score.get("basis") or "").strip():
                        bad.append(f"{lid}.{key}: NULL without basis")
                else:
                    if score_from(heat, key) is None or not 0 <= value <= 100:
                        bad.append(f"{lid}.{key}: score is not 0-100")
                    if not str(score.get("rationale") or "").strip():
                        bad.append(f"{lid}.{key}: scored without rationale")
                    evidence = score.get("evidence") or []
                    if not any(isinstance(e, dict) and e.get("source_date") and str(e.get("url", "")).startswith(("http://", "https://")) for e in evidence):
                        bad.append(f"{lid}.{key}: scored without dated URL evidence")
                    values[key] = score_from(heat, key)
            complete = not is_null and len(values) == 3 and all(v is not None for v in values.values())
            if not complete:
                if heat.get("verdict") is not None or heat.get("money_corner") is not None:
                    bad.append(f"{lid}: incomplete/NULL heat must not retain verdict or money_corner")
            if is_null and any(v is not None for v in values.values()):
                bad.append(f"{lid}: mixed scored and NULL heat is not a complete explicit NULL")
            if complete:
                want = band_for(values["impact"], values["crowdedness"])
                if heat.get("verdict") != want:
                    bad.append(f"{lid}: verdict disagrees with scores (computed {want})")
                want_corner = money_corner(values["impact"], values["crowdedness"], values["capture"])
                if heat.get("money_corner") != want_corner:
                    bad.append(f"{lid}: money_corner disagrees with scores")
                if want in {"CROWDED", "OVER_CROWDED"} and not heat.get("repricing_check"):
                    bad.append(f"{lid}: {want} needs repricing_check")
            for ticker in heat.get("ticker_refs") or []:
                if not isinstance(ticker, str) or not TICKER.fullmatch(ticker) or not (root / "data" / "market" / f"{ticker}.json").exists():
                    bad.append(f"{lid}: ticker market reference {ticker!r} lacks data/market source")
            # The price-instrument expression (method section 3, 2026-09-13): a link that
            # carries price_instruments is scored twice, and the second score set is checked
            # with the same bar as the first. Impact is shared; the verdict is computed.
            instruments = [i for i in link.get("price_instruments") or [] if isinstance(i, dict)]
            inst = heat.get("instrument")
            if instruments and not isinstance(inst, dict):
                bad.append(f"{lid}: price_instruments present but heat.instrument missing")
            elif inst is not None and not instruments:
                bad.append(f"{lid}: heat.instrument on a link with no price_instruments")
            if instruments and isinstance(inst, dict):
                ibefore = len(bad)
                ibuckets["examined"] += 1
                ivalues, inull = {}, False
                for key in INSTRUMENT_SCORES:
                    score = inst.get(key)
                    if not isinstance(score, dict):
                        bad.append(f"{lid}.instrument.{key}: missing score object")
                        continue
                    value = score.get("score")
                    if value is None:
                        inull = True
                        if not str(score.get("basis") or "").strip():
                            bad.append(f"{lid}.instrument.{key}: NULL without basis")
                    else:
                        if score_from(inst, key) is None or not 0 <= value <= 100:
                            bad.append(f"{lid}.instrument.{key}: score is not 0-100")
                        if not str(score.get("rationale") or "").strip():
                            bad.append(f"{lid}.instrument.{key}: scored without rationale")
                        evidence = score.get("evidence") or []
                        if not any(isinstance(e, dict) and e.get("source_date") and str(e.get("url", "")).startswith(("http://", "https://")) for e in evidence):
                            bad.append(f"{lid}.instrument.{key}: scored without dated URL evidence")
                        ivalues[key] = score_from(inst, key)
                icomplete = (not inull and len(ivalues) == 2 and all(v is not None for v in ivalues.values())
                             and values.get("impact") is not None)
                if inull and any(v is not None for v in ivalues.values()):
                    bad.append(f"{lid}.instrument: mixed scored and NULL is not a complete explicit NULL")
                if not icomplete and inst.get("verdict") is not None:
                    bad.append(f"{lid}.instrument: incomplete/NULL instrument heat must not retain verdict")
                if icomplete:
                    iwant = band_for(values["impact"], ivalues["crowdedness"])
                    if inst.get("verdict") != iwant:
                        bad.append(f"{lid}.instrument: verdict disagrees with scores (computed {iwant})")
                if not re.match(r"^\d{4}-\d{2}-\d{2}", str(inst.get("as_of") or "")):
                    bad.append(f"{lid}.instrument: as_of is not a date")
                elif same_day(chain.get("heat_as_of"), today) and not same_day(inst.get("as_of"), today):
                    bad.append(f"{lid}.instrument: a heat run today must re-score the instrument (as_of is not the run date)")
                elif str(inst.get("as_of"))[:10] > today:
                    bad.append(f"{lid}.instrument: as_of is later than the run date")
                for ticker in inst.get("ticker_refs") or []:
                    if not market_file(root, ticker):
                        bad.append(f"{lid}.instrument: ticker market reference {ticker!r} lacks data/market source")
                ib = instrument_bucket(heat)
                ibuckets["errors" if len(bad) > ibefore or ib == "errors" else ib] += 1
            # The graded no (method section 8, 2026-09-13): an OVER_CROWDED call on a
            # campaign-era chain writes shadow rows in the same postlude, or names why it
            # cannot yet. A call nothing can grade is the failure this check exists for.
            if complete and heat.get("verdict") == "OVER_CROWDED":
                shadow["over_crowded"] += 1
                state = shadow_state(root, cid, chain.get("heat_as_of"), link, shadow_rows)
                shadow[state] += 1
                if state == "missing":
                    bad.append(f"{lid}: OVER_CROWDED with no shadow row (run tools/shadow_heat.py {cid} --request)")
                elif state == "none_priceable":
                    bad.append(f"{lid}: OVER_CROWDED with no priceable ticker and no PENDING prices request")
            bucket = heat_bucket(heat)
            buckets["errors" if len(bad) > before or bucket == "errors" else bucket] += 1
        health = chain.get("heat_health") or {}
        valid_health = all(isinstance(health.get(k), int) and not isinstance(health.get(k), bool) and health[k] >= 0 for k in ("examined", "scored", "pending", "errors"))
        if (not valid_health or health.get("examined") != len(links)
                or valid_health and any(health[key] != value for key, value in buckets.items())):
            bad.append(f"{cid}: heat_health must exactly match content buckets {buckets} across {len(links)} links")
        stored_instruments = health.get("instruments") if isinstance(health, dict) else None
        if ibuckets["examined"] and stored_instruments != ibuckets:
            bad.append(f"{cid}: heat_health.instruments must exactly match instrument buckets {ibuckets}")
        elif not ibuckets["examined"] and stored_instruments not in (None, ibuckets):
            bad.append(f"{cid}: heat_health.instruments claims instruments on a chain with none")
        if strict:
            if not ember_lines: bad.append(f"{cid}: no same-day RUN/AMEND ledger line for run heat")
            if not calibration_today: bad.append(f"{cid}: Ember calibration is not same-day")
        (failures if strict else notes).extend((f"{cid}: {x}" for x in bad))
        print(f"  {cid}: {len(links)} links examined, {'strict' if strict else 'legacy warning'} mode, {len(bad)} finding(s)")
        print(f"  {cid}: instruments: {ibuckets['examined']} examined, {ibuckets['scored']} scored, "
              f"{ibuckets['pending']} pending, {ibuckets['errors']} errors")
        print(f"  {cid}: shadow: {shadow['over_crowded']} OVER_CROWDED link(s), {shadow['rows']} with rows, "
              f"{shadow['pending']} pending a price, {shadow['no_tickers']} with no ticker, "
              f"{shadow['missing'] + shadow['none_priceable']} missing")
    if notes:
        print(f"  WARNING legacy seed heat: {len(notes)} finding(s) do not fail until a campaign-era heat write")
    if failures:
        print(*[f"  FAIL {x}" for x in failures], sep="\n")
        return 1
    print(f"check_heat: OK. {examined} links examined across {len(touched)} heat run(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
