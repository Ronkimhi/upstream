#!/usr/bin/env python3
"""Theme and occurrence-log postlude gate. Stdlib only, offline.

`CLAUDE.md` states what a `run themes` run must verify before it commits. That column is
prose, so it can only ever be honoured by memory. This is the same list as an exit code.

Corpus integrity (every invocation, whenever the log exists):
  - ids unique, and one row per (origin, origin_ref): one occurrence in the world is one
    row, or every count in the file double-counts the items that travelled furthest
  - the log is COMPLETE against disk: every feed item, candidate, signal and calendar entry
    currently visible has a row. An occurrence log that quietly stopped ingesting looks
    exactly like a quiet week
  - every assigned row names a theme that exists and carries a non-empty theme_basis. A
    theme tag with no stated basis is the same defect class as an invented price
    (app/templates/app.js says so about `family`, which is derived and never inferred)
  - the calibration is RECOMPUTED and compared: per-theme totals, weekly buckets,
    denominators and surge flags must equal a fresh count over the rows on disk. A derived
    store nobody recomputes is a store that drifts
  - append-only: no row committed at git HEAD has been dropped or had its identity rewritten

Run-day only (occurrences or themes touched today, or a THEMES ledger line dated today):
  - calibration regenerated today
  - a THEMES ledger line naming logged: and assigned:
  - refuse a THEMES ledger line with no write behind it

It reports its denominator on EVERY run, including the empty one: "0 unassigned" over zero
rows is the failure mode method section 9 and Rule 21 exist to catch.

Run: python3 tools/check_themes.py [--date YYYY-MM-DD] [--root PATH]
Exit 0 clean, 1 on any failure.
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from theme_calibrate import calibration, sighted  # noqa: E402

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


def head_rows(root: Path) -> list:
    """The occurrence rows committed at git HEAD, or None when git cannot answer.

    Fails OPEN on an unreadable git: a preservation check that blocks because the repo is
    in a state it did not expect would stop work it was never meant to police.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "show", "HEAD:data/themes/occurrences.json"],
            capture_output=True, text=True, timeout=20)
        if out.returncode != 0:
            return None
        return (json.loads(out.stdout) or {}).get("occurrences", [])
    except Exception:  # noqa: BLE001
        return None


def check_identity(rows: list) -> None:
    ids = Counter(r.get("id") for r in rows)
    dupe_ids = sorted(i for i, n in ids.items() if n > 1)
    if dupe_ids:
        fail(f"{len(dupe_ids)} duplicate occurrence id(s): {', '.join(map(str, dupe_ids[:5]))}")
    keys = Counter((r.get("origin"), r.get("origin_ref")) for r in rows)
    dupe_keys = sorted(k for k, n in keys.items() if n > 1)
    if dupe_keys:
        fail(f"{len(dupe_keys)} occurrence(s) logged twice under one (origin, origin_ref): "
             f"{dupe_keys[:3]}. One occurrence in the world is one row, or every count here "
             f"double-counts exactly the items that travelled furthest through the funnel")
    for r in rows:
        missing = [k for k in ("id", "origin", "origin_ref", "title") if not r.get(k)]
        if missing:
            fail(f"occurrence {r.get('id', '?')}: missing {', '.join(missing)}")
    report(f"identity: {len(rows)} row(s) examined, {len(dupe_ids)} duplicate id(s), "
           f"{len(dupe_keys)} duplicate origin key(s)")


def check_complete(rows: list, data: Path) -> None:
    have = {(r.get("origin"), r.get("origin_ref")) for r in rows}
    have |= {("feed", r["promoted_from_feed"]) for r in rows if r.get("promoted_from_feed")}
    want = sighted(data)
    missing = [w for w in want if (w["origin"], w["origin_ref"]) not in have]
    if missing:
        kinds = Counter(m["origin"] for m in missing)
        fail(f"{len(missing)} occurrence(s) on disk have no row in the log ({dict(kinds)}). "
             f"A log that quietly stopped ingesting is indistinguishable from a quiet week. "
             f"First: {missing[0]['origin']} {missing[0]['origin_ref']} {missing[0]['title'][:60]!r}")
    report(f"ingest: {len(want) - len(missing)}/{len(want)} occurrence(s) visible on disk are logged")


def check_assignments(rows: list, themes: list) -> None:
    known = {t.get("id") for t in themes if t.get("id")}
    assigned = [r for r in rows if r.get("theme_id")]
    orphan = [r for r in assigned if r["theme_id"] not in known]
    if orphan:
        fail(f"{len(orphan)} occurrence(s) name a theme that does not exist: "
             f"{sorted({r['theme_id'] for r in orphan})[:5]}")
    unbased = [r for r in assigned if not str(r.get("theme_basis") or "").strip()]
    if unbased:
        fail(f"{len(unbased)} assigned occurrence(s) carry no theme_basis: "
             f"{[r.get('id') for r in unbased[:5]]}. A theme tag with no stated basis is the "
             f"same defect class as an invented price")
    for t in themes:
        if not str(t.get("definition") or "").strip():
            fail(f"theme {t.get('id', '?')} has no definition: without one, nobody can say "
                 f"whether the next occurrence belongs in it")
    report(f"assignment: {len(assigned)}/{len(rows)} row(s) assigned across {len(themes)} theme(s), "
           f"{len(rows) - len(assigned)} unassigned, {len(orphan)} orphaned, {len(unbased)} unbased")


def check_recompute(store_cal: dict, rows: list, themes: list, data: Path,
                    today: datetime.date) -> None:
    """The numbers on disk against a fresh count. The whole point of a calibrator is that
    no session ever types one of these; this is what makes that checkable."""
    fresh = calibration(rows, themes, data, today)
    for key in ("denominators", "per_theme", "unassigned_by_week", "origins", "families"):
        if store_cal.get(key) != fresh.get(key):
            fail(f"calibration.{key} on disk disagrees with a fresh count over the rows in "
                 f"the log. Re-run tools/theme_calibrate.py; never hand-edit a derived block")
    on_disk = {s.get("theme_id") for s in (store_cal.get("surges") or [])}
    computed = {s["theme_id"] for s in fresh["surges"]}
    if on_disk != computed:
        fail(f"calibration.surges names {sorted(on_disk)} but the rows compute "
             f"{sorted(computed)}")
    d = fresh["denominators"]
    report(f"recompute: {d['occurrences_logged']} logged, {d['assigned']} assigned, "
           f"{d['unassigned']} unassigned, {len(fresh['surges'])} surge(s) in "
           f"{fresh['current_week']}")


def check_preserved(rows: list, root: Path) -> None:
    prior = head_rows(root)
    if prior is None:
        report("preservation: no committed occurrence log at HEAD to compare (first run, "
               "or git unreadable). Nothing checked")
        return
    now = {r.get("id"): r for r in rows}
    dropped = [p.get("id") for p in prior if p.get("id") not in now]
    if dropped:
        fail(f"{len(dropped)} occurrence(s) committed at HEAD are gone from the log: "
             f"{dropped[:5]}. This store is append-only. An occurrence deleted is an "
             f"occurrence the machine can no longer be shown to have seen")
    rewritten = [p.get("id") for p in prior
                 if p.get("id") in now
                 and (now[p["id"]].get("first_seen") != p.get("first_seen")
                      or now[p["id"]].get("ts") != p.get("ts"))]
    if rewritten:
        fail(f"{len(rewritten)} occurrence(s) had first_seen or ts rewritten: {rewritten[:5]}. "
             f"The snapshot is the point; a re-dated row is a different occurrence")
    report(f"preservation: {len(prior)} row(s) at HEAD, {len(dropped)} dropped, "
           f"{len(rewritten)} re-dated")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    root = Path(a.root).resolve()
    today = a.date or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    data = root / "data"

    occ = read_json(data / "themes" / "occurrences.json", None)
    thm = read_json(data / "themes" / "themes.json", None)
    ledger = (data / "ledger.md").read_text() if (data / "ledger.md").exists() else ""
    theme_lines = [ln for ln in ledger.splitlines()
                   if ln.startswith(today) and re.search(r"\|\s*THEMES\s*\|", ln)]

    if occ is None and thm is None:
        if theme_lines:
            print(f"check_themes: FAILED — a THEMES ledger line is dated {today} but "
                  f"data/themes/ does not exist. A ledger line with no store behind it is "
                  f"the record of a run that did not happen")
            return 1
        print(f"check_themes: NO STORE ({today}). data/themes/ does not exist yet, so there "
              f"is nothing to gate. This is not a pass over the corpus, it is the absence "
              f"of one")
        return 0
    if occ is None or thm is None:
        print("check_themes: FAILED — data/themes/ holds one of the two stores. "
              "occurrences.json and themes.json are written together by "
              "tools/theme_calibrate.py and neither is meaningful alone")
        return 1

    rows = occ.get("occurrences") or []
    themes = thm.get("themes") or []
    cal = thm.get("calibration") or {}

    check_identity(rows)
    check_complete(rows, data)
    check_assignments(rows, themes)
    check_recompute(cal, rows, themes, data, datetime.date.fromisoformat(today))
    check_preserved(rows, root)

    touched = today in (str(occ.get("as_of") or "") + json.dumps(occ.get("changelog") or [])
                        + str(thm.get("as_of") or "") + json.dumps(thm.get("changelog") or []))
    run_day = bool(touched or theme_lines)

    if run_day:
        gen = str(cal.get("generated_at") or "")
        if not gen.startswith(today):
            fail(f"calibration.generated_at is {gen!r}, not {today}. The store was written "
                 f"today and its derived half was not, so every count in it is one run stale")
        if touched and not theme_lines:
            fail(f"data/themes/ was written today with no THEMES ledger line dated {today}")
        for ln in theme_lines:
            if "logged:" not in ln:
                fail(f"THEMES ledger line names no logged: count -> {ln[:110]}")
            if "assigned:" not in ln:
                fail(f"THEMES ledger line names no assigned: count -> {ln[:110]}")
        if theme_lines and not touched:
            fail(f"a THEMES ledger line was written on {today} but neither store was "
                 f"touched. A record of work that did not happen is the defect this gate "
                 f"exists to catch")
        report(f"ledger: {len(theme_lines)} THEMES line(s) dated {today}")
    else:
        report(f"run day: no (0 stores touched, 0 THEMES ledger lines on {today}). "
               f"Corpus integrity still examined")

    print(f"check_themes: {len(rows)} occurrence(s) logged, "
          f"{len([r for r in rows if r.get('theme_id')])} assigned across {len(themes)} theme(s)")
    for ln in lines:
        print(f"  {ln}")
    if failures:
        for f_ in failures:
            print(f"  FAIL  {f_}")
        print(f"check_themes: FAILED with {len(failures)} finding(s)")
        return 1
    print("check_themes: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
