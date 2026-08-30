#!/usr/bin/env python3
"""Stop hook: issuer-universe and fresh-context audit writes close their own loop.

It inspects only this session's transcript. When the session wrote a normalized
data/mappings/<chain>.json map, it requires:

  1. the latest same-day owning ledger line for each touched map
  2. universe authoring to have a map-log calibration generated today
  3. universe-audit to have a current audit written today, without requiring calibration

The hook fails open when its input, transcript, ledger, or an existing calibration file
cannot be read. A missing required file is not an unreadable file and therefore blocks.
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
MAPPING_PATH = re.compile(r"(?:^|/)data/mappings/([a-z0-9-]{2,40})\.json$")

sys.path.insert(0, str(ROOT / "tools"))
from check_map import (  # noqa: E402
    AUDIT_REVIEWER,
    audit_failures,
    audit_write_scope_failures,
    mapping_fingerprint,
    _head_json,
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


def latest_owning_command(
    ledger: str, slug: str, day: datetime.date
) -> str | None:
    """Return universe or universe-audit, whichever appears last that UTC day."""
    universe = f"run universe {slug}"
    universe_audit = f"run universe-audit {slug}"
    latest = None
    for line in ledger.splitlines():
        fields = [field.strip() for field in line.split("|")]
        if len(fields) < 3 or fields[2] not in {universe, universe_audit}:
            continue
        if not fields[0]:
            continue
        if utc_date(fields[0]) != day:
            continue
        latest = "universe-audit" if fields[2] == universe_audit else "universe"
    return latest


def audit_state_failures(path: Path, day: datetime.date) -> list[str] | None:
    """Current audit failures, or None when readable state cannot be established."""
    try:
        mapping = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(mapping, dict):
        return ["the touched issuer map is not a JSON object"]
    audit = mapping.get("audit")
    if not isinstance(audit, dict):
        return ["the touched issuer map has no audit object"]
    try:
        audited_day = utc_date(audit.get("audited_at"))
    except Exception:  # noqa: BLE001
        return None

    failures = []
    if audited_day != day:
        failures.append(
            f"audit.audited_at is {audit.get('audited_at')!r}, not UTC day {day}"
        )
    if audit.get("reviewed_by") != AUDIT_REVIEWER:
        failures.append(f"audit.reviewed_by is not {AUDIT_REVIEWER!r}")
    try:
        current_fingerprint = mapping_fingerprint(mapping)
    except (TypeError, ValueError):
        return None
    if audit.get("mapping_fingerprint") != current_fingerprint:
        failures.append("audit.mapping_fingerprint does not match current material content")
    failures.extend(audit_failures(mapping))

    prior = _head_json(ROOT, path)
    if isinstance(prior, dict):
        failures.extend(audit_write_scope_failures(prior, mapping))
    return failures


def written_mappings(raw: str) -> set[str]:
    slugs = set()
    for line in raw.splitlines():
        if "data/mappings/" not in line:
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
                match = MAPPING_PATH.search(target)
                if match:
                    slugs.add(match.group(1))
    return slugs


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

    touched = written_mappings(raw)
    if not touched:
        return allow()

    today = utc_today()
    try:
        ledger = (ROOT / "data" / "ledger.md").read_text()
    except Exception:  # noqa: BLE001
        return allow()

    missing = []
    needs_calibration = False
    for slug in sorted(touched):
        try:
            owner = latest_owning_command(ledger, slug, today)
        except Exception:  # noqa: BLE001
            return allow()
        if owner is None:
            missing.append(
                f"a ledger line dated {today.isoformat()} naming "
                f"`run universe {slug}` or `run universe-audit {slug}`"
            )
        path = ROOT / "data" / "mappings" / f"{slug}.json"
        if not path.exists():
            missing.append(f"the touched issuer map data/mappings/{slug}.json")
            continue
        if owner == "universe-audit":
            audit_findings = audit_state_failures(path, today)
            if audit_findings is None:
                return allow()
            missing.extend(
                f"data/mappings/{slug}.json: {finding}"
                for finding in audit_findings
            )
        elif owner == "universe":
            needs_calibration = True

    if needs_calibration:
        log = ROOT / "data" / "chains" / "_map-log.json"
        if not log.exists():
            missing.append(
                "data/chains/_map-log.json generated by `python3 tools/map_calibrate.py`"
            )
        else:
            try:
                generated = str(
                    (json.loads(log.read_text()).get("calibration") or {})
                    .get("generated_at") or ""
                )
            except Exception:  # noqa: BLE001
                return allow()
            if generated:
                try:
                    generated_today = utc_date(generated) == today
                except Exception:  # noqa: BLE001
                    return allow()
            else:
                generated_today = False
            if not generated_today:
                missing.append(
                    "a map-log calibration regenerated today: "
                    "run `python3 tools/map_calibrate.py`"
                )

    if not missing:
        return allow()
    return block(
        "This session wrote an issuer universe or audit, so its loop must close before "
        "it ends. Still missing: " + "; ".join(missing) + ". "
        "Then run `python3 tools/check_map.py` before committing."
    )


if __name__ == "__main__":
    raise SystemExit(main())
