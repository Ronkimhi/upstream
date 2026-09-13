#!/usr/bin/env python3
"""Stop hook: issuer-universe and fresh-context audit writes close their own loop.

It inspects only this session's transcript. When the session wrote a normalized
data/mappings/<chain>.json map, it requires, FOR THE UTC DAY THAT WRITE HAPPENED:

  1. the latest owning ledger line for each touched map, dated that day
  2. universe authoring to have a map-log calibration re-derived on or after it
  3. universe-audit to have an audit written that day, without requiring calibration

The day is the write's own, not "today". Keyed on today, a census that closed on Friday with
its own dated line and same-day calibration blocked every stop for the rest of a multi-day
session, and the only way past was to write `run universe <slug>` into the ledger on a day no
such run happened (2026-09-13). A forged line in an append-only record is worse than a missing
one. It also fixes the UTC-midnight edge the old rule had backwards: a write at 23:58Z owes a
line stamped 23:58Z, which is yesterday's date by 00:02Z.

The hook fails open when its input, transcript, ledger, or an existing calibration file
cannot be read. A missing required file is not an unreadable file and therefore blocks.
"""
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from write_targets import transcript_writes  # noqa: E402

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
    placement_audit = f"{universe_audit} --placement "
    latest = None
    for line in ledger.splitlines():
        fields = [field.strip() for field in line.split("|")]
        if len(fields) < 3:
            continue
        command = fields[2]
        if command not in {universe, universe_audit} and \
                not command.startswith(placement_audit):
            continue
        if not fields[0]:
            continue
        if utc_date(fields[0]) != day:
            continue
        if command.startswith(placement_audit):
            # Ron, 2026-09-01: the fresh-context audit of ONE placement, written to
            # placement_audits[] (check_map.placement_audit_failures). Its loop closes on
            # an entry audited today, not on a whole-census audit object.
            latest = "universe-audit-placement"
        else:
            latest = "universe-audit" if command == universe_audit else "universe"
    return latest


def placement_audit_state_failures(path: Path, day: datetime.date) -> list[str] | None:
    """A placement audit written today, shape-checked by check_map, or None if unreadable."""
    try:
        mapping = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(mapping, dict):
        return ["the touched issuer map is not a JSON object"]
    entries = mapping.get("placement_audits")
    if not isinstance(entries, list) or not entries:
        return ["the touched issuer map has no placement_audits entries"]
    today_entries = []
    for entry in entries:
        try:
            if isinstance(entry, dict) and utc_date(entry.get("audited_at")) == day:
                today_entries.append(entry)
        except Exception:  # noqa: BLE001
            return None
    failures = []
    if not today_entries:
        failures.append(f"no placement_audits entry is audited on UTC day {day}")
    from check_map import placement_audit_failures
    failures.extend(placement_audit_failures(mapping))
    prior = _head_json(ROOT, path)
    if isinstance(prior, dict):
        failures.extend(audit_write_scope_failures(prior, mapping))
    return failures


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


def written_mappings(raw: str, unknown: datetime.date) -> dict[str, datetime.date]:
    """{chain slug: the most recent UTC day this session wrote that map}.

    The day is the point. This hook used to return bare slugs and then demand a ledger line
    dated TODAY, so a census that closed on Friday with its own dated line blocked every stop
    for the rest of the session, and the only way past was to write `run universe <slug>` into
    the ledger on a day no such run happened. A forged line in an append-only record is worse
    than a missing one, so the question became "which day was it written", and the line is
    owed for that day.

    `unknown` is the day assumed for a write whose transcript line cannot be dated: pass
    today, so an unreadable timestamp keeps the gate firing instead of switching it off.
    """
    days: dict[str, datetime.date] = {}
    for day, target in transcript_writes(raw, "data/mappings/"):
        match = MAPPING_PATH.search(target)
        if not match:
            continue
        resolved = day if day is not None else unknown
        slug = match.group(1)
        if slug not in days or resolved > days[slug]:
            days[slug] = resolved
    return days


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

    today = utc_today()
    touched = written_mappings(raw, today)
    if not touched:
        return allow()

    try:
        ledger = (ROOT / "data" / "ledger.md").read_text()
    except Exception:  # noqa: BLE001
        return allow()

    missing = []
    # The latest write day across the touched maps, for the one calibration check below:
    # map_calibrate re-derives every chain at once, so the freshest write sets the bar.
    calibration_day = None
    for slug, day in sorted(touched.items()):
        try:
            owner = latest_owning_command(ledger, slug, day)
        except Exception:  # noqa: BLE001
            return allow()
        if owner is None:
            missing.append(
                f"a ledger line dated {day.isoformat()} naming "
                f"`run universe {slug}` or `run universe-audit {slug}`"
            )
        path = ROOT / "data" / "mappings" / f"{slug}.json"
        if not path.exists():
            missing.append(f"the touched issuer map data/mappings/{slug}.json")
            continue
        if owner == "universe-audit":
            audit_findings = audit_state_failures(path, day)
            if audit_findings is None:
                return allow()
            missing.extend(
                f"data/mappings/{slug}.json: {finding}"
                for finding in audit_findings
            )
        elif owner == "universe-audit-placement":
            audit_findings = placement_audit_state_failures(path, day)
            if audit_findings is None:
                return allow()
            missing.extend(
                f"data/mappings/{slug}.json: {finding}"
                for finding in audit_findings
            )
        elif owner == "universe":
            if calibration_day is None or day > calibration_day:
                calibration_day = day

    if calibration_day is not None:
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
            # On or after the write day, not exactly on it: a calibration re-derived LATER
            # than the map already reflects it, and an exact-match rule rejects the fresher
            # store. Absent or undateable still blocks.
            if generated:
                try:
                    fresh_enough = utc_date(generated) >= calibration_day
                except Exception:  # noqa: BLE001
                    return allow()
            else:
                fresh_enough = False
            if not fresh_enough:
                missing.append(
                    "a map-log calibration re-derived on or after "
                    f"{calibration_day.isoformat()}: run `python3 tools/map_calibrate.py`"
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
