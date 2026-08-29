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
    }

    shell = (APP / "templates" / "shell.html").read_text()
    css = (APP / "templates" / "app.css").read_text()
    js = (APP / "templates" / "app.js").read_text()
    if "</script" in js:
        print("build: refused — app.js contains a literal </script>, which would terminate the inline tag; split the string")
        return 1
    # </script>-safe JSON embedding
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")

    html = shell.replace("{{APP_CSS}}", css).replace("{{UPSTREAM_DATA}}", blob).replace("{{APP_JS}}", js)

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
