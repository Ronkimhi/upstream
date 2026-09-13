#!/usr/bin/env python3
"""Stop hook: a session that wrote a signal card does not end with the loop half-closed.

The radar postlude is written down in CLAUDE.md and in the agent file. Both are text asking
a model to remember something, and promises regress. This is the same rule as a condition
the session cannot ship past.

It fires ONLY against the session in front of it: it reads this session's own transcript for
writes under `data/signals/`, and if it finds them it requires two things to exist on disk
before the session may stop:

  1. a RADAR (or RADAR-DEGRADED) ledger line dated today
  2. a scout-log calibration block regenerated today

Deliberately narrow, and it fails OPEN on anything it cannot read: no transcript, unreadable
JSON, missing data files, an unexpected schema. A session is never blocked by another
session's hole, by a scheduled job's leftovers, or by this hook's own bugs. The audit path
(`tools/check_radar.py` in the postlude) still catches everything that reaches disk another
way.

Registered as a Stop hook in `.claude/settings.json`.
"""
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from write_targets import from_tool_use, in_repo as _in_repo  # noqa: E402

ALLOW = 0  # every exit from this hook is 0; blocking is expressed in the JSON payload


def allow() -> int:
    print(json.dumps({"decision": "approve"}))
    return ALLOW


def block(reason: str) -> int:
    print(json.dumps({"decision": "block", "reason": reason}))
    return ALLOW


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return allow()

    # Never re-enter: if the model is already responding to this hook, let it finish.
    if payload.get("stop_hook_active"):
        return allow()

    transcript = payload.get("transcript_path")
    if not transcript:
        return allow()
    try:
        raw = Path(transcript).read_text()
    except Exception:  # noqa: BLE001
        return allow()

    # Did THIS session write a signal card? Ask what each tool call WROTE, not what it was
    # named: a `cat > data/signals/...` heredoc is a write and this gate used to miss it.
    wrote_signal = False
    for line in raw.splitlines():
        if "data/signals/" not in line:
            continue
        try:
            ev = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        for block_ in (ev.get("message") or {}).get("content") or []:
            if not isinstance(block_, dict) or block_.get("type") != "tool_use":
                continue
            written, _unresolved = from_tool_use(
                str(block_.get("name") or ""), block_.get("input") or {}
            )
            root_ = Path(__file__).resolve().parent.parent.parent
            if any(_in_repo(t, root_, "data/signals", r"[^/]+\.json") for t in written):
                wrote_signal = True
                break
        if wrote_signal:
            break

    if not wrote_signal:
        return allow()

    root = Path(__file__).resolve().parent.parent.parent
    # UTC, like the other five gates: ledger lines and calibrations are stamped in UTC, and
    # a local date blocked closed loops on any machine whose date differed from UTC.
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    missing = []

    ledger = root / "data" / "ledger.md"
    try:
        text = ledger.read_text()
    except Exception:  # noqa: BLE001
        return allow()  # cannot read the ledger: fail open, never block on our own blindness
    if not any(ln.startswith(today) and re.search(r"\|\s*RADAR(-DEGRADED)?\s*\|", ln)
               for ln in text.splitlines()):
        missing.append(
            f"a RADAR ledger line dated {today} in data/ledger.md (RADAR-DEGRADED if a lane "
            f"failed: a silent no-op is the only wrong result)"
        )

    log = root / "data" / "radar" / "scout-log.json"
    try:
        gen = str((json.loads(log.read_text()).get("calibration") or {}).get("generated_at") or "")
    except Exception:  # noqa: BLE001
        gen = ""
        if log.exists():
            return allow()  # unreadable, not absent: fail open
    if not gen.startswith(today):
        missing.append(
            "a scout-log calibration regenerated today: run `python3 tools/scout_calibrate.py`"
        )

    if not missing:
        return allow()

    return block(
        "This session wrote a signal card, so the radar loop must close before it ends. "
        "Still missing: " + "; ".join(missing) + ". "
        "Then run `python3 tools/check_radar.py` before committing."
    )


if __name__ == "__main__":
    raise SystemExit(main())
