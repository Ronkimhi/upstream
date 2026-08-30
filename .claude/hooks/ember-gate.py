#!/usr/bin/env python3
"""Stop hook for Ember writes. It fails open only when it cannot inspect state."""
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from write_targets import from_tool_use  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
CHAIN = re.compile(r"(?:^|/)data/chains/([a-z0-9-]+)\.json$")


def output(decision, reason=None):
    value = {"decision": decision}
    if reason:
        value["reason"] = reason
    print(json.dumps(value))
    return 0


def utc_date(value):
    raw = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(datetime.timezone.utc).date()


def ledger_line_date(line):
    """UTC date of a ledger line whose first field is a timestamp, else None.

    `data/ledger.md` opens with prose headers, and two of them contain "|": the line-types
    legend and the format legend. Filtering on `"|" in line` and then parsing field one as a
    timestamp raised ValueError on line 3 of the real ledger, so this hook died with a
    traceback before it reached a single run line. Every heat and scenario session got a
    crash instead of an enforced gate, which is indistinguishable from a gate that passes.
    A line whose first field is not a timestamp is simply not a run line: skip it, keep
    scanning, and let the missing-evidence check below do its job.
    """
    try:
        return utc_date(line.partition("|")[0])
    except ValueError:
        return None


def written_chains(raw):
    result = set()
    for line in raw.splitlines():
        if "data/chains/" not in line:
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        for item in (event.get("message") or {}).get("content") or []:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                paths, _ = from_tool_use(str(item.get("name") or ""), item.get("input") or {})
                for path in paths:
                    hit = CHAIN.search(path)
                    if hit:
                        result.add(hit.group(1))
    return result


def main():
    try:
        payload = json.load(sys.stdin)
        if payload.get("stop_hook_active"):
            return output("approve")
        raw = Path(payload["transcript_path"]).read_text()
    except Exception:
        return output("approve")
    today, chains = datetime.datetime.now(datetime.timezone.utc).date(), written_chains(raw)
    if not chains:
        return output("approve")
    try:
        active = []
        for cid in chains:
            chain_path = ROOT / "data" / "chains" / f"{cid}.json"
            if not chain_path.exists():
                return output("approve")
            chain = json.loads(chain_path.read_text())
            if (str(chain.get("heat_as_of") or "").startswith(today.isoformat())
                    or str(chain.get("scenarios_as_of") or "").startswith(today.isoformat())):
                active.append(cid)
        if not active:
            return output("approve")
    except Exception:
        return output("approve")
    missing = []
    ledger_path = ROOT / "data" / "ledger.md"
    log_path = ROOT / "data" / "chains" / "_ember-log.json"
    if not ledger_path.exists():
        missing.append("data/ledger.md")
        ledger = ""
    else:
        try:
            ledger = ledger_path.read_text()
        except Exception:
            return output("approve")
    if not log_path.exists():
        missing.append("data/chains/_ember-log.json from `python3 tools/ember_calibrate.py`")
        log = {}
    else:
        try:
            log = json.loads(log_path.read_text())
        except Exception:
            return output("approve")
    if not any(ledger_line_date(line) == today and re.search(r"\brun (heat|scenarios) ", line)
               for line in ledger.splitlines() if "|" in line):
        missing.append("a same-day ledger line naming `run heat` or `run scenarios`")
    if not log_path.exists():
        fresh = False
    else:
        try:
            fresh = utc_date((log.get("calibration") or {}).get("generated_at")) == today
        except Exception:
            return output("approve")
    if not fresh:
        missing.append("a same-day Ember calibration from `python3 tools/ember_calibrate.py`")
    if missing:
        return output("block", "This session updated Ember-owned chain analysis. Still missing: "
                      + "; ".join(missing) + ". Then run the matching Ember gate.")
    return output("approve")


if __name__ == "__main__":
    raise SystemExit(main())
