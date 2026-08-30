#!/usr/bin/env python3
"""Heat gate. Legacy seed records warn; today's campaign-era writes fail closed."""
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from heat_score import band_for, heat_bucket, money_corner, score_from  # noqa: E402

SCORES = ("impact", "crowdedness", "capture")
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
    touched = [c for c in chains if same_day(c.get("heat_as_of"), today)]
    if not touched:
        print(f"check_heat: NOT RUN TODAY ({today}). 0 heat runs, {len(chains)} chain(s) examined.")
        return 0
    ledger = (root / "data" / "ledger.md").read_text() if (root / "data" / "ledger.md").exists() else ""
    ember_lines = [line for line in ledger.splitlines() if line.startswith(today) and "run heat " in line and re.search(r"\|\s*(RUN|AMEND)\s*\|", line)]
    calibration = load(root / "data" / "chains" / "_ember-log.json", {}) or {}
    calibration_today = same_day((calibration.get("calibration") or {}).get("generated_at"), today)
    for chain in touched:
        cid, links = chain.get("id", "<unknown>"), chain.get("links") or []
        strict = campaign_era(root, cid)
        examined += len(links)
        bad, buckets = [], {"scored": 0, "pending": 0, "errors": 0}
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
            bucket = heat_bucket(heat)
            buckets["errors" if len(bad) > before or bucket == "errors" else bucket] += 1
        health = chain.get("heat_health") or {}
        valid_health = all(isinstance(health.get(k), int) and not isinstance(health.get(k), bool) and health[k] >= 0 for k in ("examined", "scored", "pending", "errors"))
        if (not valid_health or health.get("examined") != len(links)
                or valid_health and any(health[key] != value for key, value in buckets.items())):
            bad.append(f"{cid}: heat_health must exactly match content buckets {buckets} across {len(links)} links")
        if strict:
            if not ember_lines: bad.append(f"{cid}: no same-day RUN/AMEND ledger line for run heat")
            if not calibration_today: bad.append(f"{cid}: Ember calibration is not same-day")
        (failures if strict else notes).extend((f"{cid}: {x}" for x in bad))
        print(f"  {cid}: {len(links)} links examined, {'strict' if strict else 'legacy warning'} mode, {len(bad)} finding(s)")
    if notes:
        print(f"  WARNING legacy seed heat: {len(notes)} finding(s) do not fail until a campaign-era heat write")
    if failures:
        print(*[f"  FAIL {x}" for x in failures], sep="\n")
        return 1
    print(f"check_heat: OK. {examined} links examined across {len(touched)} heat run(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
