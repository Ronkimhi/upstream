#!/usr/bin/env python3
"""Tally's occurrence log and its theme clustering, recomputed from disk. Stdlib only, offline.

Two jobs, both mechanical, neither of them judgment:

  1. INGEST. Every occurrence this machine has seen gets one permanent row in
     `data/themes/occurrences.json`: feed items, ambient candidates, signal cards, calendar
     entries. Append-only, and it snapshots title, source, url and date at the moment it
     first sees them.

     That snapshot is the whole reason this store exists. `data/feeds/latest.json` prunes at
     CAP = 500 over a 14-day window and its ids are content hashes of source+title
     (`tools/fetch/feeds.py`), so everything radar did not promote is deleted within two
     weeks and its id dangles. A clustering computed over the live feed store would show
     last week and nothing before it, which is the opposite of what an occurrence LOG is
     for. `first_feed_ts` on candidates was snapshotted for exactly this reason; this file
     applies the same rule to the whole corpus.

  2. RECOMPUTE. Per theme per ISO week: how many occurrences, what the prior weeks looked
     like, and whether this week is a SURGE. A surging theme with no signal card behind it
     is an unclaimed surge, which is the object Ron described as "a week when a lot of noise
     was under one specific theme".

What this file does NOT decide is which theme an occurrence belongs to. That is Tally's
judgment, written on the row as `theme_id` with a `theme_basis` saying why, and preserved
here verbatim across every run. The split is the same one `tools/scout_calibrate.py` keeps:
the agent writes what it judged, the calibrator writes everything countable, and no number
in the log was ever typed by a session.

Run: python3 tools/theme_calibrate.py [--root PATH] [--dry-run]
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A theme is surging when this week's count clears BOTH bars: an absolute floor, so three
# items on a quiet theme is not a surge, and a multiple of its own recent baseline, so a
# theme that always runs hot does not surge every week. Shipped to the page in
# app/build.py payload.method.themes rather than retyped in app.js.
SURGE_MIN_COUNT = 8
SURGE_MULTIPLE = 2.0
BASELINE_WEEKS = 4
LIVE_CANDIDATE_STATUS = ("AMBIENT", "PROMOTED")


def load(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


def iso_week(d: str) -> str:
    """`2026-08-26` -> `2026-W35`. Empty string when the date will not parse, which is
    counted as undated rather than silently bucketed into the current week."""
    try:
        y, w, _ = date.fromisoformat(str(d)[:10]).isocalendar()
        return f"{y}-W{w:02d}"
    except Exception:  # noqa: BLE001
        return ""


def week_start(wk: str):
    try:
        y, w = wk.split("-W")
        return date.fromisocalendar(int(y), int(w), 1)
    except Exception:  # noqa: BLE001
        return None


def recent_weeks(today: date, n: int) -> list:
    """The n most recent ISO weeks ending with today's, oldest first."""
    out = []
    for i in range(n - 1, -1, -1):
        d = today - timedelta(weeks=i)
        y, w, _ = d.isocalendar()
        out.append(f"{y}-W{w:02d}")
    return out


# ---------------------------------------------------------------- ingest

def sighted(data: Path) -> list:
    """Every occurrence currently visible on disk, as ingest candidates.

    Order is deterministic (date, origin, ref) so two runs over the same disk assign the
    same ids. DISMISSED and EXPIRED candidates are still ingested here, unlike
    `impact_calibrate.occurrences()` which excludes them: that function measures APPRAISAL
    COVERAGE, where counting the dead would make the number fall when radar does its job.
    This is a LOG. An occurrence that was seen and thrown away is exactly the small thing
    the log exists to keep.
    """
    out = []
    feeds = load(data / "feeds" / "latest.json", {}) or {}
    for it in feeds.get("items", []):
        if not isinstance(it, dict) or not it.get("id"):
            continue
        out.append({
            "origin": "feed", "origin_ref": it["id"], "ts": it.get("ts", ""),
            "title": it.get("title", ""), "source": it.get("source", ""),
            "url": it.get("url", ""), "family": it.get("family"),
        })
    cands = load(data / "radar" / "candidates.json", {}) or {}
    for c in cands.get("candidates", []):
        if not isinstance(c, dict) or not c.get("id"):
            continue
        out.append({
            "origin": "candidate", "origin_ref": c["id"], "ts": c.get("date", ""),
            "title": c.get("title", ""), "source": c.get("source_name", ""),
            "url": "", "family": c.get("family"),
            "feed_ref": c.get("first_feed_item_id"), "status": c.get("status"),
        })
    for f in sorted((data / "signals").glob("*.json")):
        s = load(f)
        if not isinstance(s, dict) or not s.get("id"):
            continue
        occ = s.get("occurrence") or {}
        out.append({
            "origin": "signal", "origin_ref": s["id"],
            "ts": occ.get("anchor_date") or s.get("created_at", ""),
            "title": s.get("title", ""), "source": "signal card", "url": "",
            "family": None, "status": s.get("status"),
        })
    cal = load(data / "calendar" / "events.json", {}) or {}
    for e in cal.get("events", []):
        if not isinstance(e, dict) or not e.get("id"):
            continue
        out.append({
            "origin": "calendar", "origin_ref": e["id"], "ts": e.get("date", ""),
            "title": e.get("title", ""), "source": e.get("source_name", ""),
            "url": "", "family": e.get("kind"), "status": e.get("status"),
        })
    out.sort(key=lambda r: (str(r.get("ts") or ""), r["origin"], r["origin_ref"]))
    return out


def ingest(store: dict, seen: list, today: str) -> tuple:
    """Add a row for every occurrence not already logged. Never edits or drops one.

    A candidate that names the feed item it was triaged from PROMOTES that row instead of
    adding a second: one occurrence in the world is one row here, or every count in the
    file double-counts exactly the items that made it furthest through the funnel.
    """
    rows = store.setdefault("occurrences", [])
    by_key = {(r.get("origin"), r.get("origin_ref")): r for r in rows}
    by_feed = {r.get("origin_ref"): r for r in rows if r.get("origin") == "feed"}
    added = promoted = 0
    used = Counter(str(r.get("id", ""))[4:12] for r in rows)
    for item in seen:
        key = (item["origin"], item["origin_ref"])
        if key in by_key:
            row = by_key[key]
            if item.get("status") and row.get("status") != item["status"]:
                row["status"] = item["status"]
            continue
        feed_ref = item.get("feed_ref")
        if feed_ref and feed_ref in by_feed:
            row = by_feed[feed_ref]
            row["origin"] = item["origin"]
            row["promoted_from_feed"] = feed_ref
            row["origin_ref"] = item["origin_ref"]
            row["title"] = item["title"] or row["title"]
            if item.get("status"):
                row["status"] = item["status"]
            by_key[(row["origin"], row["origin_ref"])] = row
            del by_feed[feed_ref]
            promoted += 1
            continue
        stamp = (item.get("ts") or today)[:10].replace("-", "")
        if len(stamp) != 8 or not stamp.isdigit():
            stamp = today.replace("-", "")
        used[stamp] += 1
        row = {
            "id": f"OCC-{stamp}-{used[stamp]:03d}",
            "first_seen": today,
            "origin": item["origin"],
            "origin_ref": item["origin_ref"],
            "ts": item.get("ts") or "",
            "title": item.get("title") or "",
            "source": item.get("source") or "",
            "url": item.get("url") or "",
            "family": item.get("family"),
            "theme_id": None,
            "theme_basis": "",
            "theme_by": "",
        }
        if item.get("status"):
            row["status"] = item["status"]
        rows.append(row)
        by_key[key] = row
        if row["origin"] == "feed":
            by_feed[row["origin_ref"]] = row
        added += 1
    return added, promoted


# ---------------------------------------------------------------- assignment

def _hit(term: str, hay: str) -> bool:
    """Whole-word match, never a bare substring.

    A plain `in` test was written first and `"ai" in "ukraine"` is True, which would have
    filed every Ukraine headline under the AI buildout. Multi-word terms still work: the
    boundary is applied at the ends of the phrase.
    """
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", hay) is not None


def _terms(theme: dict, key: str) -> list:
    m = theme.get("match") or {}
    return [str(t).lower() for t in (m.get(key) or []) if str(t).strip()]


def theme_match(row: dict, theme: dict) -> str:
    """The term that puts this occurrence in this theme, or empty string.

    Matching is on the occurrence's OWN text: its title and its source, snapshotted at
    ingest. Never on anything the machine inferred about it later. `app/templates/app.js`
    states the rule this follows for `family`: a tag is derived from something the record
    actually says, and an invented one is the same defect class as an invented price. Here
    the derivation is a term written into the theme's own definition, so the basis on the
    row names both the term and the theme it came from, and the gate can re-run it.
    """
    hay = (str(row.get("title") or "") + " " + str(row.get("source") or "")).lower()
    for bad in _terms(theme, "not"):
        if _hit(bad, hay):
            return ""
    for need in _terms(theme, "all"):
        if not _hit(need, hay):
            return ""
    any_terms = _terms(theme, "any")
    if not any_terms:
        return "all" if _terms(theme, "all") else ""
    for term in any_terms:
        if _hit(term, hay):
            return term
    return ""


def assign(rows: list, themes: list) -> tuple:
    """Apply the themes' own match rules to every row not assigned by hand.

    A row whose `theme_by` is a person or an agent is NEVER overwritten: a rule is the
    default, a judgment overrides it, and the two are told apart by that field alone. Rules
    are tried in theme order and the first match wins, so a theme meant to catch a narrower
    case is written above the broad one.
    """
    matched = cleared = 0
    for row in rows:
        by = str(row.get("theme_by") or "")
        # Any hand assignment wins, INCLUDING one that deliberately says "no theme". The
        # first draft skipped only rows that carried a theme_id, so Tally reading an item
        # and judging it to belong nowhere was silently overturned by a keyword the next
        # run. A judgment that an occurrence does not cluster is still a judgment.
        if by and not by.startswith("rule:"):
            continue
        hit_id = hit_term = ""
        for t in themes:
            if not t.get("id"):
                continue
            term = theme_match(row, t)
            if term:
                hit_id, hit_term = t["id"], term
                break
        if hit_id:
            row["theme_id"] = hit_id
            row["theme_by"] = f"rule:{hit_id}"
            # Short on purpose. The row already carries theme_id, and the page has the
            # theme's own label and rule, so spelling both out again on 191 rows was 13 KB
            # of the same sentence. What the basis has to carry is the TERM that placed it,
            # which is the part nothing else on the page can reconstruct.
            row["theme_basis"] = f"matched {hit_term!r}"
            matched += 1
        elif by.startswith("rule:"):
            # The rule that placed it was edited or removed. Clearing beats leaving a row
            # tagged by a rule that no longer says so.
            row["theme_id"] = None
            row["theme_by"] = ""
            row["theme_basis"] = ""
            cleared += 1
    return matched, cleared


# ---------------------------------------------------------------- recompute

def calibration(rows: list, themes: list, data: Path, today: date) -> dict:
    weeks = recent_weeks(today, 8)
    baseline_pool = recent_weeks(today, BASELINE_WEEKS + 1)[:-1]
    current = weeks[-1]
    by_theme = {}
    signals_by_theme = {}
    for t in themes:
        tid = t.get("id")
        if tid:
            by_theme[tid] = [r for r in rows if r.get("theme_id") == tid]
            signals_by_theme[tid] = sorted({
                r["origin_ref"] for r in by_theme[tid] if r.get("origin") == "signal"
            } | set(t.get("signal_refs") or []))

    per_theme, surges = {}, []
    for t in themes:
        tid = t.get("id")
        if not tid:
            continue
        mine = by_theme.get(tid, [])
        wk = Counter(iso_week(r.get("ts")) for r in mine)
        present = [w for w in baseline_pool if w in wk or week_start(w)]
        base_counts = [wk.get(w, 0) for w in baseline_pool]
        baseline = round(sum(base_counts) / len(base_counts), 2) if base_counts else 0.0
        now = wk.get(current, 0)
        claimed = bool(signals_by_theme.get(tid))
        surging = now >= SURGE_MIN_COUNT and now >= SURGE_MULTIPLE * max(baseline, 0.5)
        per_theme[tid] = {
            "label": t.get("label", ""),
            "total": len(mine),
            "by_week": {w: wk.get(w, 0) for w in weeks},
            "undated": wk.get("", 0),
            "current_week": now,
            "baseline": baseline,
            "baseline_weeks": len(base_counts),
            "surging": surging,
            "claimed": claimed,
            "signal_refs": signals_by_theme.get(tid, []),
            "origins": dict(Counter(r.get("origin") for r in mine)),
        }
        if surging:
            surges.append({
                "theme_id": tid, "label": t.get("label", ""), "week": current,
                "count": now, "baseline": baseline, "claimed": claimed,
                "signal_refs": signals_by_theme.get(tid, []),
            })
    surges.sort(key=lambda s: (s["claimed"], -s["count"]))

    unassigned = [r for r in rows if not r.get("theme_id")]
    uwk = Counter(iso_week(r.get("ts")) for r in unassigned)
    feeds = load(data / "feeds" / "latest.json", {}) or {}
    cands = load(data / "radar" / "candidates.json", {}) or {}
    cal = load(data / "calendar" / "events.json", {}) or {}
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
        "weeks": weeks,
        "current_week": current,
        "surge_rule": {"min_count": SURGE_MIN_COUNT, "multiple": SURGE_MULTIPLE,
                       "baseline_weeks": BASELINE_WEEKS},
        "denominators": {
            "occurrences_logged": len(rows),
            "assigned": len(rows) - len(unassigned),
            "unassigned": len(unassigned),
            "themes": len(themes),
            "feed_items_on_disk": len([i for i in feeds.get("items", []) if isinstance(i, dict)]),
            "candidates_on_disk": len(cands.get("candidates", [])),
            "signals_on_disk": len(list((data / "signals").glob("*.json"))),
            "calendar_on_disk": len(cal.get("events", [])),
        },
        "origins": dict(Counter(r.get("origin") for r in rows)),
        "families": dict(Counter(r.get("family") or "unfiled" for r in rows)),
        "per_theme": per_theme,
        "unassigned_by_week": {w: uwk.get(w, 0) for w in weeks},
        "surges": surges,
        # Stated rather than implied: with a 14-day feed window this log has no deep past
        # yet, so an early baseline is thin and a surge computed against it is weak
        # evidence. Saying so beats printing a confident flag over two weeks of history.
        "note": (f"Baseline is the mean of the {BASELINE_WEEKS} complete weeks before "
                 f"{current}. The occurrence log began accumulating on first ingest, so "
                 "weeks before that hold only what the feed store still carried."),
    }


def build(root: Path, dry_run: bool = False) -> int:
    data = root / "data"
    folder = data / "themes"
    today = datetime.now(timezone.utc).date()
    tstr = today.isoformat()

    occ_path, thm_path = folder / "occurrences.json", folder / "themes.json"
    occ = load(occ_path, {"as_of": tstr, "owner": "Tally", "occurrences": [], "changelog": []})
    thm = load(thm_path, {"as_of": tstr, "owner": "Tally", "themes": [], "notes": [], "changelog": []})
    occ.setdefault("occurrences", [])
    thm.setdefault("themes", [])
    thm.setdefault("changelog", [])
    occ.setdefault("changelog", [])

    added, promoted = ingest(occ, sighted(data), tstr)
    matched, cleared = assign(occ["occurrences"], thm["themes"])
    occ["as_of"] = tstr
    occ["owner"] = "Tally"
    occ["generated_by"] = "tools/theme_calibrate.py (rows) + Tally (theme_id, theme_basis)"
    thm["as_of"] = tstr
    thm["owner"] = "Tally"
    thm["generated_by"] = "tools/theme_calibrate.py"
    thm["calibration"] = calibration(occ["occurrences"], thm["themes"], data, today)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if added or promoted or matched or cleared:
        occ["changelog"].append({
            "ts": stamp, "by": "theme_calibrate",
            "change": f"AMEND ingest: {added} new occurrence(s) logged, "
                      f"{promoted} feed row(s) promoted to a candidate in place; "
                      f"{matched} row(s) matched a theme rule, {cleared} cleared by a "
                      f"rule that no longer claims them",
        })
    thm["changelog"].append({
        "ts": stamp, "by": "theme_calibrate",
        "change": (f"AMEND calibration recomputed over "
                   f"{thm['calibration']['denominators']['occurrences_logged']} logged "
                   f"occurrence(s), {thm['calibration']['denominators']['assigned']} assigned, "
                   f"{len(thm['calibration']['surges'])} surge(s)"),
    })

    d = thm["calibration"]["denominators"]
    print(f"theme_calibrate: {d['occurrences_logged']} logged "
          f"({d['assigned']} assigned, {d['unassigned']} unassigned) across {d['themes']} theme(s); "
          f"+{added} new, {promoted} promoted, {matched} rule-matched, {cleared} cleared; "
          f"{len(thm['calibration']['surges'])} surge(s) "
          f"in {thm['calibration']['current_week']}")
    if dry_run:
        print("theme_calibrate: --dry-run, wrote nothing")
        return 0
    folder.mkdir(parents=True, exist_ok=True)
    occ_path.write_text(json.dumps(occ, indent=1, ensure_ascii=False) + "\n")
    thm_path.write_text(json.dumps(thm, indent=1, ensure_ascii=False) + "\n")
    print(f"theme_calibrate: wrote {occ_path.relative_to(root)} and {thm_path.relative_to(root)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    return build(Path(a.root), a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
