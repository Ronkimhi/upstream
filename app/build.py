#!/usr/bin/env python3
"""Upstream UI builder. Stdlib only, offline, runs identically in every venue.

Reads data/, inlines everything into app/templates/shell.html, writes app/index.html.
Runs tools/validate.py first and refuses to build on invalid data.

Usage: python3 app/build.py [--check]   (--check: validate + assemble, write nothing)
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
DATA = ROOT / "data"
SIZE_WARN_MB = 2.0
# Vendored UMD modules, inlined in this exact order (each attaches to window.d3;
# d3-force resolves the other three off that object at define time). Vetting record
# and licence: app/templates/vendor/README.md and LICENSE-d3.txt.
VENDOR_FILES = ["d3-quadtree.min.js", "d3-dispatch.min.js", "d3-timer.min.js", "d3-force.min.js"]


def read_json_dir(folder: Path) -> list:
    out = []
    if folder.exists():
        for f in sorted(folder.glob("*.json")):
            if f.name.startswith("_"):
                continue
            out.append(json.loads(f.read_text()))
    return out


BLOB_MARKER = "window.UPSTREAM_DATA = "


def extract_committed_payload():
    """The data blob out of the committed app/index.html, or None with a reason.

    CLAUDE.md says index.html "is never hand-edited", and nothing enforced it. `--check`
    validated data/ and assembled the HTML in memory, then threw it away without ever
    comparing it to the file on disk — so a page left stale for a week, or edited by
    hand, or built from data that has since moved, passed CI cleanly. The artifact is
    what Ron actually reads; a number on it that no longer matches data/ is a
    hallucination with a build step in front of it.
    """
    page = APP / "index.html"
    if not page.exists():
        return None, "app/index.html does not exist yet"
    text = page.read_text()
    i = text.find(BLOB_MARKER)
    if i < 0:
        return None, f"no {BLOB_MARKER!r} marker in app/index.html"
    start = i + len(BLOB_MARKER)
    end = text.find(";\n", start)
    if end < 0:
        return None, "the data blob is not terminated"
    try:
        return json.loads(text[start:end].replace("<\\/", "</")), None
    except Exception as e:  # noqa: BLE001
        return None, f"the embedded blob is not valid JSON: {e}"


def _is_contiguous_run(sub: list, whole: list) -> bool:
    """True when `sub` appears in `whole` as a run of consecutive items.

    Both are windows on the tail of data/ledger.md, so the page's window may start
    earlier than the current one; a page line that is not in the current window at all
    is only a problem if the page's run is not a suffix-aligned slice of it. Comparing
    on the overlap keeps this honest without failing on a slid window.
    """
    if not sub:
        return True
    for i in range(len(whole) - 1, -1, -1):
        if whole[i] == sub[-1]:
            n = min(len(sub), i + 1)
            if sub[-n:] == whole[i + 1 - n:i + 1]:
                return True
    # The page's last line may have slid out of the current 60-line window entirely.
    # Then the only check available is that every page line is a real ledger line.
    return all(s in whole for s in sub)


def compare_committed(payload: dict) -> list:
    """Top-level keys where the committed page disagrees with freshly-read data/.

    built_at is excluded: it changes on every build by design and says nothing about
    whether the content drifted.
    """
    committed, why = extract_committed_payload()
    if committed is None:
        return [why]
    drift = []
    for key in sorted(set(payload) | set(committed)):
        if key == "built_at":
            continue
        if key == "ledger":
            # The postlude order is build, THEN append the ledger line describing the
            # build, THEN commit — so the committed page is always a line or two behind
            # by construction. Requiring equality here would fail every honest commit.
            # What must hold is that the page invented nothing and dropped nothing: its
            # lines are a contiguous run of the real ledger, in order.
            page_lines, now_lines = committed.get(key) or [], payload.get(key) or []
            if page_lines and not _is_contiguous_run(page_lines, now_lines):
                drift.append("ledger: the committed page's lines are not a contiguous run "
                             "of data/ledger.md — the page shows entries the ledger does "
                             "not have, or in a different order")
            continue
        if key not in committed:
            drift.append(f"{key}: missing from the committed page")
        elif key not in payload:
            drift.append(f"{key}: on the committed page but no longer built")
        elif committed[key] != payload[key]:
            a, b = committed[key], payload[key]
            detail = ""
            if isinstance(a, list) and isinstance(b, list) and len(a) != len(b):
                detail = f" ({len(a)} on the page, {len(b)} in data/)"
            elif isinstance(a, dict) and isinstance(b, dict):
                moved = sorted(set(a) ^ set(b)) or \
                    sorted(k for k in set(a) & set(b) if a[k] != b[k])
                detail = f" (differs at: {', '.join(map(str, moved[:6]))})" if moved else ""
            drift.append(f"{key}: the committed page disagrees with data/{detail}")
    return drift


def main() -> int:
    check = "--check" in sys.argv

    r = subprocess.run([sys.executable, str(ROOT / "tools" / "validate.py")])
    if r.returncode != 0:
        print("build: refused — validation failed (fix data/, then rebuild)")
        return 1

    market = {}
    mdir = DATA / "market"
    if mdir.exists():
        for f in sorted(mdir.glob("*.json")):
            if f.name == "_meta.json":
                continue
            m = json.loads(f.read_text())
            market[f.stem] = m

    trades = []
    tfile = DATA / "trades.jsonl"
    if tfile.exists():
        for line in tfile.read_text().splitlines():
            if line.strip():
                trades.append(json.loads(line))

    ledger_lines = []
    lfile = DATA / "ledger.md"
    if lfile.exists():
        for line in lfile.read_text().splitlines():
            if line[:2].isdigit() and "|" in line:
                ledger_lines.append(line.strip())

    digests = read_json_dir(DATA / "digest")
    digests.sort(key=lambda d: d.get("week", ""), reverse=True)

    # trimmed feed subset for the cortex dust ring (title/source/family/date only)
    feeds_store = {"items": []}
    fp = DATA / "feeds" / "latest.json"
    if fp.exists():
        try:
            _f = json.loads(fp.read_text())
            _items = [it for it in _f.get("items", []) if isinstance(it, dict)]
            # `total` is the whole store; `items` is the subset inlined for the cortex
            # dust ring. The UI used to render len(items) under the word HELD, so the
            # page said "300 HELD" while the store held 317 and the Scout card two
            # clicks away said 317 — one corpus, two numbers, on one page. The
            # truncation is fine; reporting it as the total was not.
            feeds_store = {"as_of": _f.get("as_of"), "total": len(_items),
                           "items": [
                               {"t": (it.get("title") or "")[:110], "s": it.get("source"),
                                "f": it.get("family"), "d": it.get("ts")}
                               for it in _items[:300]]}
        except Exception:
            pass

    payload = {
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
        "signals": read_json_dir(DATA / "signals"),
        "chains": read_json_dir(DATA / "chains"),
        "screens": read_json_dir(DATA / "screens"),
        "stocks": read_json_dir(DATA / "stocks"),
        "market": market,
        "shadow": {
            "book": json.loads((DATA / "shadow" / "book.json").read_text()) if (DATA / "shadow" / "book.json").exists() else {"rows": []},
            "results": json.loads((DATA / "shadow" / "results.json").read_text()) if (DATA / "shadow" / "results.json").exists() else {},
        },
        "trades": trades,
        "requests": json.loads((DATA / "requests.json").read_text()) if (DATA / "requests.json").exists() else {"requests": []},
        "health": {
            "sessions": json.loads((DATA / "health" / "sessions.json").read_text()) if (DATA / "health" / "sessions.json").exists() else {},
            "actions": json.loads((DATA / "health" / "actions.json").read_text()) if (DATA / "health" / "actions.json").exists() else {},
        },
        "ledger": ledger_lines[-60:],
        "digests": digests[:4],
        "indicators": json.loads((DATA / "indicators.json").read_text()) if (DATA / "indicators.json").exists() else {"trips": []},
        "calendar": json.loads((DATA / "calendar" / "events.json").read_text()) if (DATA / "calendar" / "events.json").exists() else {"events": []},
        "feeds": feeds_store,
        "candidates": json.loads((DATA / "radar" / "candidates.json").read_text()) if (DATA / "radar" / "candidates.json").exists() else {"candidates": []},
        "scout": json.loads((DATA / "radar" / "scout-log.json").read_text()) if (DATA / "radar" / "scout-log.json").exists() else None,
        "map": json.loads((DATA / "chains" / "_map-log.json").read_text()) if (DATA / "chains" / "_map-log.json").exists() else None,
        # The scoring thresholds, shipped to the page instead of retyped in it. app.js
        # had 60/40/60 and the band edges written as literals, duplicating
        # tools/validate.py — they agreed on the day they were written and nothing kept
        # them agreeing. A UI that draws a money-corner box from its own copy of the
        # rule can disagree with the validator that computed the verdict.
        "method": {
            "money_corner": {"impact_min": 60, "crowd_max": 40, "capture_min": 60},
            "bands": {"over_crowded_above": 80, "crowded_above": 60, "emerging_above": 40,
                      "undiscovered_impact_min": 60},
        },
    }

    shell = (APP / "templates" / "shell.html").read_text()
    css = (APP / "templates" / "app.css").read_text()
    js = (APP / "templates" / "app.js").read_text()
    if "</script" in js:
        print("build: refused — app.js contains a literal </script>, which would terminate the inline tag; split the string")
        return 1

    # Vendored third-party code (app/templates/vendor/README.md carries the vetting record).
    # Explicit allowlist, never a glob: order matters (each UMD attaches to window.d3 and
    # d3-force reads the other three off it), and an unreviewed file must never inline itself.
    vendor_js = ""
    for name in VENDOR_FILES:
        vf = APP / "templates" / "vendor" / name
        if not vf.exists():
            print(f"build: refused — missing vendor file {vf}")
            return 1
        vt = vf.read_text()
        if "</script" in vt:
            print(f"build: refused — vendor/{name} contains a literal </script>")
            return 1
        if "Copyright" not in vt:
            print(f"build: refused — vendor/{name} lost its licence banner (ISC requires the notice in all copies)")
            return 1
        vendor_js += f"/* vendored verbatim: {name} · licence: app/templates/vendor/LICENSE-d3.txt */\n{vt}\n"

    # </script>-safe JSON embedding
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")

    # Data substituted LAST so a feed headline containing a literal placeholder cannot be replaced.
    html = (shell.replace("{{APP_CSS}}", css)
                 .replace("{{VENDOR_JS}}", vendor_js)
                 .replace("{{APP_JS}}", js)
                 .replace("{{UPSTREAM_DATA}}", blob))

    size_mb = len(html.encode()) / 1e6
    if size_mb > SIZE_WARN_MB:
        print(f"build: WARNING index.html is {size_mb:.1f} MB (> {SIZE_WARN_MB} MB) — consider pruning ARCHIVED series from the inline blob")

    if check:
        drift = compare_committed(payload)
        if drift:
            print("build: --check FAILED — app/index.html does not match data/:")
            for d in drift:
                print(f"  - {d}")
            print("build: run `python3 app/build.py` and commit the result")
            return 1
        print(f"build: --check OK ({size_mb:.2f} MB, {len(payload['signals'])} signals, "
              f"{len(payload['chains'])} chains, {len(payload['stocks'])} dives; "
              f"committed page matches data/)")
        return 0

    (APP / "index.html").write_text(html)
    print(f"build: wrote app/index.html ({size_mb:.2f} MB) at {payload['built_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
