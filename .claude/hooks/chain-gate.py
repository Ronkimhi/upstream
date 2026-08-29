#!/usr/bin/env python3
"""Stop hook: a session that rewrote a chain map does not end with the loop half-closed.

Sibling of `radar-gate.py`, same shape and same discipline. The chain postlude is written
down in `CLAUDE.md` and in the agent file, and both are text asking a model to remember
something. This is the same rule as a condition the session cannot ship past.

It fires ONLY against the session in front of it: it reads this session's own transcript for
writes under `data/chains/`, and if it finds them it requires two things on disk before the
session may stop:

  1. a ledger line dated today naming a `run chain` or a `refresh data/chains/...`
  2. a map-log calibration block regenerated today

It fails OPEN on anything it cannot read: no transcript, unreadable JSON, missing data files,
an unexpected schema. A session is never blocked by another session's hole, by a scheduled
job's leftovers, or by this hook's own bugs. `tools/check_chain.py` in the postlude still
catches everything that reaches disk another way.

Underscore-prefixed files (`_map-log.json`, `_archetypes.md`) are the agent's own stores, not
maps, so writing one does not by itself trigger the gate.

Registered as a Stop hook in `.claude/settings.json`.
"""
import datetime
import json
import re
import sys
from pathlib import Path

ALLOW = 0  # every exit is 0; blocking is expressed in the JSON payload


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

    if payload.get("stop_hook_active"):
        return allow()

    transcript = payload.get("transcript_path")
    if not transcript:
        return allow()
    try:
        raw = Path(transcript).read_text()
    except Exception:  # noqa: BLE001
        return allow()

    wrote_chain = False
    for line in raw.splitlines():
        if "data/chains/" not in line:
            continue
        try:
            ev = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        for blk in (ev.get("message") or {}).get("content") or []:
            if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                continue
            if blk.get("name") not in {"Write", "Edit", "NotebookEdit", "MultiEdit"}:
                continue
            path = str((blk.get("input") or {}).get("file_path") or "")
            m = re.search(r"data/chains/([^/]+)\.json$", path)
            if m and not m.group(1).startswith("_"):
                wrote_chain = True
                break
        if wrote_chain:
            break

    if not wrote_chain:
        return allow()

    root = Path(__file__).resolve().parent.parent.parent
    today = datetime.date.today().isoformat()
    missing = []

    ledger = root / "data" / "ledger.md"
    try:
        text = ledger.read_text()
    except Exception:  # noqa: BLE001
        return allow()  # cannot read it: fail open, never block on our own blindness
    if not any(ln.startswith(today) and re.search(r"\b(run chain|refresh data/chains)", ln)
               for ln in text.splitlines()):
        missing.append(
            f"a ledger line dated {today} naming the chain run, with the archetypes applied "
            f"and what the click queue held"
        )

    log = root / "data" / "chains" / "_map-log.json"
    try:
        gen = str((json.loads(log.read_text()).get("calibration") or {}).get("generated_at") or "")
    except Exception:  # noqa: BLE001
        gen = ""
        if log.exists():
            return allow()  # unreadable, not absent: fail open
    if not gen.startswith(today):
        missing.append(
            "a map-log calibration regenerated today: run `python3 tools/map_calibrate.py`"
        )

    if not missing:
        return allow()

    return block(
        "This session rewrote a chain map, so the cartographer loop must close before it ends. "
        "Still missing: " + "; ".join(missing) + ". "
        "Then run `python3 tools/check_chain.py` before committing: it also verifies that no "
        "link lost its heat block and no scenario vanished."
    )


if __name__ == "__main__":
    raise SystemExit(main())
