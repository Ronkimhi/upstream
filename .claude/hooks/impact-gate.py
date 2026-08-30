#!/usr/bin/env python3
"""Stop hook: a session that wrote an appraisal does not end with the loop half-closed.

The impact postlude is written down in CLAUDE.md and in `.claude/agents/tally-appraiser.md`.
Both are text asking a model to remember something, and promises regress. This is the same
rule as a condition the session cannot ship past.

It fires ONLY against the session in front of it: it reads this session's own transcript for
writes under `data/impact/`, and if it finds them it requires two things to exist on disk
before the session may stop:

  1. an IMPACT ledger line dated today
  2. a rank-log calibration regenerated today

The calibration matters more here than in the other gates. The ranked queue is the whole
product of this stage, it is DERIVED from the appraisals on disk, and an appraisal written
without re-deriving it leaves a queue that is silently one row short while still looking
complete. A missing queue row and a low-ranked occurrence are indistinguishable downstream.

Deliberately narrow, and it fails OPEN on anything it cannot read: no transcript, unreadable
JSON, missing data files, an unexpected schema. A session is never blocked by another
session's hole or by this hook's own bugs. The audit path (`tools/check_impact.py` in the
postlude) still catches everything that reaches disk another way.

Registered as a Stop hook in `.claude/settings.json`.
"""
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from write_targets import from_tool_use  # noqa: E402

ALLOW = 0  # every exit from this hook is 0; blocking is expressed in the JSON payload

# The appraisals themselves, never the log: the log is what the calibrator writes, so keying
# on it would make the gate fire on its own remedy.
APPRAISAL_RE = re.compile(r"data/impact/(?!_)[^/]+\.json$")


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

    # Ask what each tool call WROTE, not what it was named: a `cat > data/impact/...` heredoc
    # is a write, and a gate keyed on tool names misses it.
    wrote = False
    for line in raw.splitlines():
        if "data/impact/" not in line:
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
            if any(APPRAISAL_RE.search(t) for t in written):
                wrote = True
                break
        if wrote:
            break

    if not wrote:
        return allow()

    root = Path(__file__).resolve().parent.parent.parent
    # UTC, not local. Ledger lines are stamped in UTC (the `Z` in every line), so a session
    # running between local midnight and UTC midnight would look for a date the ledger will
    # never carry and block on a timezone. tools/validate.py carries the same note; the two
    # other hooks that predate this one still use local time and have the bug.
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    missing = []

    ledger = root / "data" / "ledger.md"
    try:
        text = ledger.read_text()
    except Exception:  # noqa: BLE001
        return allow()  # cannot read the ledger: fail open, never block on our own blindness
    if not any(ln.startswith(today) and re.search(r"\|\s*IMPACT\s*\|", ln)
               for ln in text.splitlines()):
        missing.append(
            f"an IMPACT ledger line dated {today} in data/ledger.md, naming `ranked:` and "
            f"`band:`"
        )

    log = root / "data" / "impact" / "_rank-log.json"
    try:
        cal = json.loads(log.read_text()).get("calibration") or {}
        gen = str(cal.get("as_of") or "")
    except Exception:  # noqa: BLE001
        gen = ""
        if log.exists():
            return allow()  # unreadable, not absent: fail open
    if not gen.startswith(today):
        missing.append(
            "a rank-log calibration regenerated today: run "
            "`python3 tools/impact_calibrate.py`. The queue is derived from disk, so an "
            "appraisal written without re-deriving it leaves the queue one row short"
        )

    if not missing:
        return allow()

    return block(
        "This session wrote an impact appraisal, so the appraisal loop must close before it "
        "ends. Still missing: " + "; ".join(missing) + ". "
        "Then run `python3 tools/check_impact.py` before committing."
    )


if __name__ == "__main__":
    raise SystemExit(main())
