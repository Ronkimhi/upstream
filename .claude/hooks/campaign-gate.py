#!/usr/bin/env python3
"""Stop hook: campaign manifest writes require a same-day closed loop.

It inspects only this session's transcript. When the session wrote a
data/campaigns/CAMP-*.json manifest, it requires:

  1. a same-day ledger line naming the command that refreshed the campaign
  2. a same-day campaign_calibrate changelog entry in every touched manifest

The hook fails open when its input, transcript, ledger, or an existing JSON file cannot be
read. A missing required file is not an unreadable file and therefore blocks.
"""
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from write_targets import from_tool_use  # noqa: E402

ALLOW = 0
ROOT = Path(__file__).resolve().parent.parent.parent
UTC = datetime.timezone.utc
CAMPAIGN_PATH = re.compile(
    r"(?:^|/)data/campaigns/(CAMP-\d{8}-\d{2}\.json)$"
)


def allow() -> int:
    print(json.dumps({"decision": "approve"}))
    return ALLOW


def block(reason: str) -> int:
    print(json.dumps({"decision": "block", "reason": reason}))
    return ALLOW


def utc_date(timestamp: object) -> datetime.date:
    """Return a timestamp's UTC date, rejecting timezone-naive values."""
    value = str(timestamp or "").strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC).date()


def utc_today() -> datetime.date:
    return datetime.datetime.now(UTC).date()


def ledger_has_same_day(
    ledger: str, command_re: re.Pattern[str], day: datetime.date
) -> bool:
    same_day = False
    for line in ledger.splitlines():
        if not command_re.search(line):
            continue
        raw_timestamp, separator, _rest = line.partition("|")
        if not separator or not raw_timestamp.strip():
            continue
        same_day = utc_date(raw_timestamp) == day or same_day
    return same_day


def written_campaigns(raw: str) -> set[str]:
    paths = set()
    for line in raw.splitlines():
        if "data/campaigns/" not in line:
            continue
        try:
            event = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        for item in (event.get("message") or {}).get("content") or []:
            if not isinstance(item, dict) or item.get("type") != "tool_use":
                continue
            written, _unresolved = from_tool_use(
                str(item.get("name") or ""), item.get("input") or {}
            )
            for target in written:
                match = CAMPAIGN_PATH.search(target)
                if match:
                    paths.add(match.group(1))
    return paths


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

    touched = written_campaigns(raw)
    if not touched:
        return allow()

    today = utc_today()
    try:
        ledger = (ROOT / "data" / "ledger.md").read_text()
    except Exception:  # noqa: BLE001
        return allow()

    missing = []
    command_re = re.compile(
        r"\b(?:run campaign init|run selection CAMP-\d{8}-\d{2}|"
        r"run chain SIG-\d{8}-\d{2}|run universe [a-z0-9-]{2,40}|"
        r"run heat [a-z0-9-]{2,40}|run scenarios [a-z0-9-]{2,40}|"
        r"run screen [a-z0-9-]{2,40}(?: S[1-6])?|"
        r"run profile (?:[A-Z0-9.-]{1,10}|--campaign CAMP-\d{8}-\d{2})|"
        r"run deepdive [A-Z0-9.-]{1,10} [a-z0-9-]{2,40}|"
        r"run redteam [A-Z0-9.-]{1,10} [a-z0-9-]{2,40}|"
        r"refresh data/chains/[A-Za-z0-9._-]+\.json)\b"
    )
    try:
        same_day_ledger = ledger_has_same_day(ledger, command_re, today)
    except Exception:  # noqa: BLE001
        return allow()
    if not same_day_ledger:
        missing.append(
            f"a ledger line dated {today.isoformat()} naming the command that refreshed "
            "the campaign"
        )

    for name in sorted(touched):
        path = ROOT / "data" / "campaigns" / name
        if not path.exists():
            missing.append(f"the touched campaign manifest data/campaigns/{name}")
            continue
        try:
            campaign = json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            return allow()
        try:
            calibration_rows = [
                row for row in campaign.get("changelog") or []
                if isinstance(row, dict) and row.get("by") == "campaign_calibrate"
            ]
            calibrated = any(row.get("ts") and utc_date(row.get("ts")) == today
                             for row in calibration_rows)
        except Exception:  # noqa: BLE001
            return allow()
        if not calibrated:
            missing.append(
                f"a same-day campaign calibration in data/campaigns/{name}: "
                "run `python3 tools/campaign_calibrate.py`"
            )

    if not missing:
        return allow()
    return block(
        "This session wrote a campaign manifest, so the campaign loop must close before "
        "it ends. Still missing: " + "; ".join(missing) + ". "
        "Then run `python3 tools/check_campaign.py` before committing."
    )


if __name__ == "__main__":
    raise SystemExit(main())
