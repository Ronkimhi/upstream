#!/usr/bin/env python3
"""Radar postlude gate. Stdlib only, offline.

`CLAUDE.md` states what a radar run must verify before it commits. That column was prose,
so it could only ever be honoured by memory. This is the same list as an exit code.

Checks, each reported with the denominator it examined (Rule 21):
  1. every signal touched today carries an occurrence block and >= 2 dated evidence items
  2. the forward calendar was swept (as_of is today) and no WATCHING date has passed
  3. candidate expiry ran (no AMBIENT candidate 45+ days old)
  4. every PROMOTED candidate carries first_feed_ts, so latency stays measurable
  5. the scout log's calibration was regenerated today
  6. every signal dismissed today carries a shadow row (method §8)
  7. today's radar ledger line names a taste-rules-applied count AND what the click queue held
  8. an EMPTY day (a run that wrote 0 cards) proves it swept the feed: the scout log's
     feed_items_examined is > 0, so a scanner that silently stopped searching cannot pass as
     a quiet day

Scope: checks 1, 2, 3, 5 and 6 only bind on a day that actually wrote a radar artifact.
On a day with no radar run the gate reports NOT RUN TODAY and exits 0, rather than
reporting a clean pass over nothing. Check 8 is the opposite boundary: it binds ONLY on a
day that ran but touched no signals, the exact case where checks 1 and 6 pass over nothing.

Run: python3 tools/check_radar.py [--date YYYY-MM-DD] [--root PATH]
Exit 0 clean, 1 on any failure.
"""
import datetime
import json
import re
import sys
from pathlib import Path

EXPIRY_DAYS = 45
MIN_EVIDENCE = 2

failures: list[str] = []
lines: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def report(msg: str) -> None:
    lines.append(msg)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


def days_since(date_str: str, today: datetime.date) -> int | None:
    if not isinstance(date_str, str) or len(date_str) < 10:
        return None
    try:
        return (today - datetime.date.fromisoformat(date_str[:10])).days
    except ValueError:
        return None


def main() -> int:
    argv = sys.argv[1:]
    root = Path(argv[argv.index("--root") + 1]).resolve() if "--root" in argv \
        else Path(__file__).resolve().parent.parent
    today = argv[argv.index("--date") + 1] if "--date" in argv \
        else datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    today_d = datetime.date.fromisoformat(today)
    data = root / "data"

    signals = []
    sig_dir = data / "signals"
    if sig_dir.exists():
        for f in sorted(sig_dir.glob("*.json")):
            s = read_json(f)
            if isinstance(s, dict):
                s["_file"] = f.name
                signals.append(s)
    touched = [s for s in signals
               if today in (str(s.get("updated_at") or "") + str(s.get("created_at") or ""))]

    ledger = (data / "ledger.md").read_text() if (data / "ledger.md").exists() else ""
    radar_lines = [ln for ln in ledger.splitlines()
                   if ln.startswith(today) and re.search(r"\|\s*RADAR(-DEGRADED)?\s*\|", ln)]

    ran_today = bool(touched or radar_lines)
    if not ran_today:
        print(f"check_radar: NOT RUN TODAY ({today}). 0 signals touched, 0 radar ledger lines. "
              f"Nothing to gate; this is not a pass over the checks below.")
        return 0

    # 1. occurrence block + evidence on every touched signal
    for s in touched:
        sid = s.get("id", s["_file"])
        occ = s.get("occurrence")
        if not isinstance(occ, dict) or not occ.get("kind") or not occ.get("anchor_date"):
            fail(f"{sid}: touched today with no occurrence block (kind + anchor_date)")
        ev = [e for e in (s.get("evidence") or [])
              if isinstance(e, dict) and e.get("source_date")]
        if len(ev) < MIN_EVIDENCE:
            fail(f"{sid}: {len(ev)} dated evidence items, needs {MIN_EVIDENCE}")
    report(f"signals touched today: {len(touched)} of {len(signals)} examined")

    # 2. calendar swept
    cal = read_json(data / "calendar" / "events.json", {}) or {}
    events = cal.get("events", [])
    if cal.get("as_of") != today:
        fail(f"calendar as_of is {cal.get('as_of')!r}, not {today}: the sweep did not run")
    stale_watching = [e.get("id") for e in events
                      if e.get("status") == "WATCHING"
                      and isinstance(e.get("date"), str) and e["date"] < today]
    if stale_watching:
        fail(f"calendar: {len(stale_watching)} WATCHING entries past their date, mark PASSED: "
             f"{', '.join(map(str, stale_watching))}")
    # An empty calendar makes the sweep check unfalsifiable, so the scope boundary is stated
    # on the same line as the result rather than left for a reader to infer.
    report(f"calendar: {len(events)} entries examined, {len(stale_watching)} past-date WATCHING"
           + ("  <- EMPTY: the sweep passed over nothing. Forward visibility is part of the "
              "hunt (method §0.1); this is a scope boundary, not a clean result."
              if not events else ""))

    # 3 + 4. candidate expiry ran, and promoted candidates stay latency-measurable
    cands = (read_json(data / "radar" / "candidates.json", {}) or {}).get("candidates", [])
    overdue = [c.get("id") for c in cands
               if c.get("status") == "AMBIENT" and (days_since(c.get("date"), today_d) or 0) >= EXPIRY_DAYS]
    if overdue:
        fail(f"candidates: {len(overdue)} AMBIENT past {EXPIRY_DAYS} days, expiry did not run: "
             f"{', '.join(map(str, overdue))}")
    promoted = [c for c in cands if c.get("status") == "PROMOTED"]
    no_ts = [c.get("id") for c in promoted if not c.get("first_feed_ts")]
    if no_ts:
        fail(f"candidates: {len(no_ts)} of {len(promoted)} PROMOTED carry no first_feed_ts, "
             f"so intake latency is unmeasurable for them: {', '.join(map(str, no_ts))}")
    ambient = sum(1 for c in cands if c.get("status") == "AMBIENT")
    report(f"candidates: {len(cands)} examined, {ambient} AMBIENT, {len(overdue)} past expiry, "
           f"{len(promoted)} promoted ({len(promoted) - len(no_ts)} latency-measurable)")

    # 5. calibration regenerated
    log = read_json(data / "radar" / "scout-log.json")
    if not isinstance(log, dict):
        fail("data/radar/scout-log.json missing or unreadable: calibration did not run")
    else:
        gen = str((log.get("calibration") or {}).get("generated_at") or "")
        if not gen.startswith(today):
            fail(f"scout-log calibration generated_at is {gen or 'absent'!r}, not {today}: "
                 f"run tools/scout_calibrate.py")
        else:
            live = sum(1 for r in log.get("proposed_rules", [])
                       if isinstance(r, dict) and r.get("status") == "HARDENED")
            report(f"scout log: calibration regenerated {gen}, {live} hardened taste rules live")

    # 6. every signal dismissed today is priced: a dismissal with no shadow row is an
    #    opinion made unfalsifiable (method §8). Mirrors the TOO_LATE rule on dives.
    book = read_json(data / "shadow" / "book.json", {}) or {}
    rows = book.get("rows") or []
    row_refs = {r.get("id") for r in rows if isinstance(r, dict)} | \
               {r.get("ref") for r in rows if isinstance(r, dict)}
    dismissed = [s for s in touched if s.get("status") == "DISMISSED"]
    unpriced = [s.get("id") for s in dismissed
                if not s.get("shadow_ref") or s.get("shadow_ref") not in row_refs]
    if unpriced:
        fail(f"{len(unpriced)} of {len(dismissed)} signals dismissed today have no shadow row: "
             f"{', '.join(map(str, unpriced))}. A dismissal that is never graded cannot be wrong.")
    if dismissed:
        report(f"dismissals: {len(dismissed)} today, {len(dismissed) - len(unpriced)} priced into "
               f"the shadow book ({len(rows)} rows total)")

    # 7. the ledger line names what it filtered, and what the click queue held
    if not radar_lines:
        fail(f"no RADAR ledger line for {today} but {len(touched)} signals were touched")
    else:
        for ln in radar_lines:
            if not re.search(r"taste[- ]?(ledger|rule)", ln, re.I):
                fail("radar ledger line does not name a taste-rules-applied count "
                     "(method §6: taste filtering is applied VISIBLY)")
            # The postlude's artifact republish CLEARS the queue, so an undrained click is a
            # deleted click. The run must say what the queue held, even if the answer is empty.
            if not re.search(r"(click )?queue", ln, re.I):
                fail("radar ledger line does not say what the click queue held. The postlude's "
                     "republish clears the queue, so an undrained entry is destroyed, not "
                     "delayed. State it even when empty.")
        report(f"ledger: {len(radar_lines)} radar line(s) for {today}")

    # 8. an empty day must show its work. A run that touched no signals still ran, so check 1
    #    (occurrence blocks) and check 6 (dismissals) loop over nothing and pass vacuously.
    #    Without this, a scout that silently stopped searching is byte-identical to a scout
    #    that swept the whole feed and found nothing worth a card. The falsifiable difference
    #    is feed_items_examined: the sweep either looked at the feed store or it did not.
    if radar_lines and not touched:
        denoms = {}
        if isinstance(log, dict):
            denoms = (log.get("calibration") or {}).get("denominators") \
                or log.get("denominators") or {}
        examined = denoms.get("feed_items_examined")
        if not isinstance(examined, int) or examined <= 0:
            fail(f"radar ran today and wrote 0 signal cards, but the scout log shows "
                 f"feed_items_examined={examined!r}. A run that examined nothing is a scanner "
                 f"that stopped searching, not a quiet day. If the feed store was genuinely "
                 f"empty or the fetch failed, the run is degraded: mark the ledger line "
                 f"RADAR-DEGRADED and say why, so an absence of input is never read as an "
                 f"absence of opportunity.")
        else:
            report(f"empty day: 0 cards written, but {examined} feed items examined -- a quiet "
                   f"day that showed its work, not a silent stop")

    print(f"check_radar: {today}")
    for ln in lines:
        print(f"  {ln}")
    if failures:
        for f_ in failures:
            print(f"  FAIL  {f_}")
        print(f"check_radar: FAILED with {len(failures)} finding(s)")
        return 1
    print("check_radar: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
