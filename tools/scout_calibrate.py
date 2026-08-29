#!/usr/bin/env python3
"""Nell's calibration engine. Stdlib only, offline, session venue.

Recomputes the machine-measurable half of `data/radar/scout-log.json` from what is
already on disk: conversion, per-family and per-source hit rates, death outcomes, and
intake latency. Judgment fields (proposed_rules, spot_tests, repairs, notes) are the
agent's to write and are preserved verbatim across runs.

Amend, never recreate: an existing log keeps its judgment fields and its changelog, and
gains one AMEND entry per run.

Every metric carries its denominator (Rule 21: a count with no denominator cannot fail).
Latency is only computed where a hard candidate-to-signal link exists; unmatched signals
are counted and named, never guessed at with title similarity.

Run: python3 tools/scout_calibrate.py [--dry-run]
"""
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "radar" / "scout-log.json"
NOW = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
TODAY = datetime.date.today()
EXPIRY_DAYS = 45
FAMILIES = ("POLICY", "CORPORATE", "TECH", "PHYSICAL", "GEO")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


def load_dir(folder: Path) -> list[dict]:
    if not folder.exists():
        return []
    out = []
    for f in sorted(folder.glob("*.json")):
        d = read_json(f)
        if isinstance(d, dict):
            d["_file"] = str(f.relative_to(ROOT))
            out.append(d)
    return out


def days_since(date_str: str) -> int | None:
    """Whole days from an ISO date or timestamp to today. None if unparseable."""
    if not isinstance(date_str, str) or len(date_str) < 10:
        return None
    try:
        return (TODAY - datetime.date.fromisoformat(date_str[:10])).days
    except ValueError:
        return None


def build_calibration() -> dict:
    signals = load_dir(DATA / "signals")
    chains = load_dir(DATA / "chains")
    screens = load_dir(DATA / "screens")
    stocks = load_dir(DATA / "stocks")
    cands = (read_json(DATA / "radar" / "candidates.json", {}) or {}).get("candidates", [])
    events = (read_json(DATA / "calendar" / "events.json", {}) or {}).get("events", [])
    feeds = (read_json(DATA / "feeds" / "latest.json", {}) or {}).get("items", [])

    sig_by_id = {s.get("id"): s for s in signals}

    # ---- how far did each signal actually travel down the funnel
    chained_signals = {c.get("signal_id") for c in chains if c.get("signal_id")}
    screened_chains = {s.get("chain_id") for s in screens if s.get("chain_id")}
    dived_chains = {s.get("chain_id") for s in stocks if s.get("chain_id") and not s.get("fixture")}
    money_corner_chains = {
        c.get("id") for c in chains
        if any((ln.get("heat") or {}).get("money_corner") for ln in c.get("links", []))
    }
    chain_of_signal = {c.get("signal_id"): c.get("id") for c in chains if c.get("signal_id")}

    def depth(sig_id: str) -> str:
        """Deepest funnel stage this signal reached. Closed vocabulary."""
        chain_id = chain_of_signal.get(sig_id)
        if not chain_id:
            return "SIGNAL"
        if chain_id in dived_chains:
            return "DIVE"
        if chain_id in screened_chains:
            return "SCREEN"
        return "CHAIN"

    # ---- conversion (candidates)
    by_status: dict[str, int] = {}
    for c in cands:
        by_status[c.get("status", "?")] = by_status.get(c.get("status", "?"), 0) + 1
    triaged = len(cands)
    promoted = by_status.get("PROMOTED", 0)
    conversion = {
        "candidates_triaged": triaged,
        "promoted": promoted,
        "dismissed": by_status.get("DISMISSED", 0),
        "expired": by_status.get("EXPIRED", 0),
        "ambient": by_status.get("AMBIENT", 0),
        "promote_rate": round(promoted / triaged, 3) if triaged else None,
    }

    # ---- per family: candidates in, and how deep the signals they became travelled
    per_family = {}
    for fam in FAMILIES:
        fam_cands = [c for c in cands if c.get("family") == fam]
        fam_promoted = [c for c in fam_cands if c.get("status") == "PROMOTED"]
        reached = [depth(c.get("promoted_signal_id")) for c in fam_promoted if c.get("promoted_signal_id")]
        per_family[fam] = {
            "triaged": len(fam_cands),
            "promoted": len(fam_promoted),
            "reached_chain": sum(1 for d in reached if d in ("CHAIN", "SCREEN", "DIVE")),
            "reached_money_corner": sum(
                1 for c in fam_promoted
                if chain_of_signal.get(c.get("promoted_signal_id")) in money_corner_chains
            ),
        }

    # ---- per source: feed volume seen vs what survived triage and promotion.
    # Keyed on `feed_source`, the feed store's own source string, not the card's prose
    # source_name: only an exact key can join the two sides. A candidate triaged from a
    # session beat rather than the feed store has no feed_source and is counted apart, so
    # the feed sources' denominators stay honest.
    per_source: dict[str, dict] = {}
    for it in feeds:
        src = it.get("source") or "?"
        per_source.setdefault(src, {"items_seen": 0, "triaged": 0, "promoted": 0})
        per_source[src]["items_seen"] += 1
    unkeyed = {"triaged": 0, "promoted": 0}
    for c in cands:
        src = c.get("feed_source")
        if not src:
            unkeyed["triaged"] += 1
            unkeyed["promoted"] += 1 if c.get("status") == "PROMOTED" else 0
            continue
        per_source.setdefault(src, {"items_seen": 0, "triaged": 0, "promoted": 0})
        per_source[src]["triaged"] += 1
        if c.get("status") == "PROMOTED":
            per_source[src]["promoted"] += 1
    per_source = dict(sorted(per_source.items(), key=lambda kv: -kv[1]["items_seen"]))
    per_source["_no_feed_source_key"] = {
        "items_seen": None,
        "triaged": unkeyed["triaged"],
        "promoted": unkeyed["promoted"],
        "note": "candidates with no feed_source: session beats, or triaged before the key existed",
    }

    # ---- latency: hard links only (candidate -> signal). No title-similarity guessing.
    # `first_feed_ts` is snapshotted onto the candidate at triage time because the feed
    # store prunes at 500 items: the id alone would dangle within weeks.
    feed_ts_by_id = {it.get("id"): it.get("ts") for it in feeds if it.get("id")}
    latency, unmatched = [], []
    for s in signals:
        sid = s.get("id")
        src_cand = next(
            (c for c in cands if c.get("promoted_signal_id") == sid), None
        )
        if not src_cand:
            unmatched.append({"signal_id": sid, "reason": "no candidate promoted into this signal"})
            continue
        feed_ts = src_cand.get("first_feed_ts") or feed_ts_by_id.get(src_cand.get("first_feed_item_id"))
        created = s.get("created_at")
        d0, d1 = days_since(feed_ts), days_since(created)
        if d0 is None or d1 is None:
            unmatched.append({
                "signal_id": sid,
                "reason": "candidate carries no first_feed_ts; latency unmeasurable for this signal",
            })
            continue
        latency.append({
            "signal_id": sid,
            "candidate_id": src_cand.get("id"),
            "first_feed_item_id": src_cand.get("first_feed_item_id"),
            "first_feed_ts": feed_ts,
            "signal_created_at": created,
            "days_late": d0 - d1,
        })

    # ---- death outcomes: my own false positives
    stale_signals = [
        {"id": s.get("id"), "review_by": s.get("review_by")}
        for s in signals
        if s.get("status") == "NEW" and isinstance(s.get("review_by"), str)
        and s["review_by"] < TODAY.isoformat()
    ]
    overdue_cands = [
        {"id": c.get("id"), "date": c.get("date"), "age_days": days_since(c.get("date"))}
        for c in cands
        if c.get("status") == "AMBIENT" and (days_since(c.get("date")) or 0) >= EXPIRY_DAYS
    ]
    passed_unpromoted = [
        {"id": e.get("id"), "date": e.get("date")}
        for e in events
        if e.get("status") == "PASSED" and not e.get("promoted_signal_id")
    ]

    notes_examined = sum(len(s.get("notes") or []) for s in signals) + \
        sum(len(c.get("notes") or []) for c in cands)

    return {
        "generated_at": NOW,
        "conversion": conversion,
        "per_family": per_family,
        "per_source": per_source,
        "funnel_depth": {
            "signals_total": len(signals),
            "reached_chain": len(chained_signals),
            "reached_screen": sum(1 for s in signals if depth(s.get("id")) in ("SCREEN", "DIVE")),
            "reached_dive": sum(1 for s in signals if depth(s.get("id")) == "DIVE"),
            "in_money_corner_chain": sum(
                1 for s in signals if chain_of_signal.get(s.get("id")) in money_corner_chains
            ),
        },
        "latency": latency,
        "latency_unmatched": unmatched,
        "death_outcomes": {
            "signals_new_past_review_by": stale_signals,
            "candidates_past_expiry": overdue_cands,
            "calendar_passed_unpromoted": passed_unpromoted,
        },
        "denominators": {
            "signals_examined": len(signals),
            "candidates_examined": len(cands),
            "calendar_examined": len(events),
            "feed_items_examined": len(feeds),
            "chains_examined": len(chains),
            "notes_examined": notes_examined,
        },
    }


def main() -> int:
    dry = "--dry-run" in sys.argv
    cal = build_calibration()

    existing = read_json(LOG) if LOG.exists() else None
    if isinstance(existing, dict):
        log = existing
        prior = (log.get("calibration") or {}).get("generated_at")
        log.setdefault("changelog", []).append({
            "ts": NOW, "by": "scout_calibrate",
            "change": "recomputed calibration from data/ (amend)",
            "prior": prior,
        })
    else:
        log = {
            "id": "scout-log",
            "created_at": NOW,
            "generated_by": "nell-scanner",
            "proposed_rules": [],
            "spot_tests": [],
            "repairs": [],
            "notes": [],
            "confidence_audit": {"verified": 0, "inferred": 0, "speculative": 0, "null": 0},
            "changelog": [{"ts": NOW, "by": "scout_calibrate", "change": "created scout log"}],
        }
    log["as_of"] = NOW[:10]
    log["updated_at"] = NOW
    log["calibration"] = cal

    d = cal["denominators"]
    c = cal["conversion"]
    print(
        f"scout_calibrate: {d['candidates_examined']} candidates, {d['signals_examined']} signals, "
        f"{d['feed_items_examined']} feed items, {d['notes_examined']} notes examined"
    )
    print(
        f"  promote rate {c['promoted']}/{c['candidates_triaged']} · "
        f"latency matched {len(cal['latency'])}/{d['signals_examined']} "
        f"({len(cal['latency_unmatched'])} unmatched, named in the log)"
    )
    dd = cal["death_outcomes"]
    print(
        f"  my misses: {len(dd['signals_new_past_review_by'])} of {d['signals_examined']} signals past review_by · "
        f"{len(dd['candidates_past_expiry'])} of {c['ambient']} AMBIENT past {EXPIRY_DAYS}d · "
        f"{len(dd['calendar_passed_unpromoted'])} of {d['calendar_examined']} calendar entries passed unpromoted"
    )
    if dry:
        print("scout_calibrate: --dry-run, nothing written")
        return 0
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n")
    print(f"scout_calibrate: wrote {LOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
