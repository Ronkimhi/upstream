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
            feeds_store = {"as_of": _f.get("as_of"), "items": [
                {"t": (it.get("title") or "")[:110], "s": it.get("source"),
                 "f": it.get("family"), "d": it.get("ts")}
                for it in _f.get("items", [])[:300] if isinstance(it, dict)]}
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
        print(f"build: --check OK ({size_mb:.2f} MB, {len(payload['signals'])} signals, {len(payload['chains'])} chains, {len(payload['stocks'])} dives)")
        return 0

    (APP / "index.html").write_text(html)
    print(f"build: wrote app/index.html ({size_mb:.2f} MB) at {payload['built_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
