#!/usr/bin/env python3
"""Hitch's trace miner: what this project's agent sessions actually DID. Stdlib only, offline.

Every other check in this repo reads what a session WROTE. None reads what a session did:
which hooks crashed, which gates blocked, which tools failed, which model answered and how
many tokens it took. That record already exists on the machine that ran the session, as
Claude Code's own transcripts under ~/.claude/projects/<project>/ (subagents under
<session>/subagents/), and until this file nobody opened it. Two Stop gates crashed about
300 times across 40 sessions before anyone noticed, and a crashed Stop gate lets a session
end exactly as if it had passed.

CONTENT-FREE BY CONSTRUCTION. This repo is public and a transcript holds everything a
session saw. The miner returns counts, hook script names, tool names (MCP tools collapsed
to `mcp`), model ids, agent types (anything outside this repo's roster and the built-ins
collapsed to `other`), exit codes and `<session>:<entry>` uuid refs. It never returns a
prompt, a tool input or output, hook stderr, or a path.

The transcript format is internal to Claude Code and changes between versions, so nothing
here raises on an entry it does not recognise. Unparseable lines and unknown entry types
are counted, and tools/check_harness.py reports a non-empty read with zero recognised
assistant messages as format drift rather than as a quiet week.

Run: python3 tools/harness_traces.py [--days N] [--transcripts DIR] [--root PATH]
"""
import argparse
import datetime
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_DAYS = 7
MAX_REFS = 3
UTC = datetime.timezone.utc

BUILTIN_AGENT_TYPES = frozenset({
    "Explore", "Plan", "general-purpose", "claude-code-guide", "statusline-setup", "claude",
})
KNOWN_TYPES = frozenset({
    "user", "assistant", "system", "attachment", "summary", "file-history-snapshot",
    "queue-operation", "last-prompt", "custom-title", "mode", "bridge-session", "atis-latch",
    "history-suppression",
})
# A line is only worth json.loads when it can carry something counted here. User lines are
# the bulk of a transcript (every tool output rides in one) and only the error ones matter.
_WANTED = re.compile(r'"type"\s*:\s*"(?:assistant|attachment|system)"|"is_error"\s*:\s*true')
# The seven Stop gates open their refusal with a stable sentence. A blocking-error entry
# carries the message but not the script, so the gate is attributed by that sentence: a
# HEURISTIC, and labelled as one wherever check_harness prints it.
BLOCK_SIGNATURES = (
    ("radar-gate.py", "wrote a signal card"),
    ("chain-gate.py", "rewrote a chain map"),
    ("campaign-gate.py", "wrote a campaign manifest"),
    ("universe-gate.py", "wrote an issuer universe"),
    ("profile-gate.py", "wrote a reusable issuer profile"),
    ("impact-gate.py", "wrote an impact appraisal"),
    ("ember-gate.py", "updated Ember-owned chain analysis"),
)
HOOK_SCRIPT_RE = re.compile(r"hooks/([A-Za-z0-9_.\-]+\.py)")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MODEL_RE = re.compile(r"^claude-[a-z0-9.\-]+$")
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
              "cache_creation_input_tokens")


def default_transcripts_dir(root: Path) -> Path:
    """Claude Code names a project's transcript folder after the project's absolute path with
    every non-alphanumeric character turned into a dash. The project is the MAIN worktree, so a
    linked worktree still finds the sessions that ran in the primary checkout."""
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / "projects"
    project = root
    try:
        common = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=10).stdout.strip()
        if common:
            project = Path(common).parent
    except Exception:  # noqa: BLE001
        pass
    return base / re.sub(r"[^A-Za-z0-9]", "-", str(project.resolve()))


def roster(root: Path) -> set:
    return {p.stem for p in (root / ".claude" / "agents").glob("*.md")}


def _when(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _mtime(path: Path):
    return datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return ""


def _error_class(text: str) -> str:
    t = text[:400].lower()
    if "permission" in t or "denied" in t or "doesn't want to proceed" in t:
        return "permission"
    if "hook" in t:
        return "hook"
    if t.startswith("exit code"):
        return "exit_code"
    return "other"


def _tool_name(name) -> str:
    name = str(name or "unknown")
    return "mcp" if name.startswith("mcp__") else name


def _add_ref(refs: list, session: str, entry) -> None:
    if len(refs) < MAX_REFS and UUID_RE.match(session or "") and UUID_RE.match(str(entry or "")):
        refs.append(f"{session}:{entry}")


def mine(transcripts_dir, days=DEFAULT_DAYS, since=None, known_agents=(), now=None) -> dict:
    """Aggregate every transcript line dated inside the window. Never raises on content."""
    now = now or datetime.datetime.now(UTC)
    cutoff = since or (now - datetime.timedelta(days=days))
    scope = {"transcripts_dir_found": False, "main_files": 0, "subagent_files": 0,
             "sessions": 0, "lines": 0, "parsed": 0, "unparsed": 0, "unknown_types": {},
             "assistant_messages": 0, "first_ts": None, "last_ts": None}
    out = {"window": {"from": cutoff.isoformat(), "to": now.isoformat(),
                      "days": None if since else days},
           "scope": scope,
           "hooks": {"stop_evaluations": 0, "runs": {}, "crashes": {}, "blocks": {}},
           "tools": {"calls": {}, "errors": {}, "error_classes": {}, "refs": {}},
           "api_errors": {"count": 0, "refs": []},
           "models": {"by_model": {}, "by_agent_type": {}}}
    base = Path(transcripts_dir) if transcripts_dir else None
    if base is None or not base.is_dir():
        return out
    scope["transcripts_dir_found"] = True
    mains = sorted(p for p in base.glob("*.jsonl") if _mtime(p) >= cutoff)
    subs = sorted(p for p in base.glob("*/subagents/**/*.jsonl") if _mtime(p) >= cutoff)
    scope["main_files"], scope["subagent_files"] = len(mains), len(subs)

    unknown, runs, calls, errors, classes = Counter(), Counter(), Counter(), Counter(), Counter()
    crashes = defaultdict(lambda: {"count": 0, "sessions": set(), "exit_codes": Counter(), "refs": []})
    blocks = defaultdict(lambda: {"count": 0, "sessions": set(), "events": Counter(), "refs": []})
    class_refs = defaultdict(list)
    by_model = defaultdict(Counter)
    by_agent = defaultdict(Counter)
    sessions, first, last = set(), None, None

    for path in mains + subs:
        session = path.relative_to(base).parts[0].removesuffix(".jsonl")
        agent_type = None
        if path in subs:
            meta = {}
            try:
                meta = json.loads(path.with_name(path.stem + ".meta.json").read_text())
            except Exception:  # noqa: BLE001
                pass
            raw = str(meta.get("agentType") or "unknown")
            agent_type = raw if raw in known_agents or raw in BUILTIN_AGENT_TYPES else "other"
        names, seen_msgs, stamp, counted = {}, set(), None, False
        try:
            handle = path.open(errors="ignore")
        except OSError:
            continue
        with handle:
            for line in handle:
                scope["lines"] += 1
                if not _WANTED.search(line):
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    scope["unparsed"] += 1
                    continue
                if not isinstance(d, dict):
                    scope["unparsed"] += 1
                    continue
                scope["parsed"] += 1
                stamp = _when(d.get("timestamp")) or stamp or _mtime(path)
                if stamp < cutoff:
                    continue
                if not counted:
                    sessions.add(session)
                    counted = True
                first = stamp if first is None or stamp < first else first
                last = stamp if last is None or stamp > last else last
                kind, entry = d.get("type"), d.get("uuid")
                if kind not in KNOWN_TYPES:
                    unknown[str(kind)[:40]] += 1
                msg = d.get("message") if isinstance(d.get("message"), dict) else {}
                content = msg.get("content") if isinstance(msg.get("content"), list) else []
                if kind == "assistant":
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            names[c.get("id")] = _tool_name(c.get("name"))
                            calls[_tool_name(c.get("name"))] += 1
                    key = msg.get("id") or d.get("requestId") or entry
                    if key in seen_msgs:
                        continue
                    seen_msgs.add(key)
                    scope["assistant_messages"] += 1
                    model = str(msg.get("model") or "")
                    model = model if MODEL_RE.match(model) else "other"
                    usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
                    by_model[model]["messages"] += 1
                    for k in USAGE_KEYS:
                        if isinstance(usage.get(k), int):
                            by_model[model][k] += usage[k]
                    if agent_type:
                        by_agent[agent_type][model] += 1
                elif kind == "user":
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                            cls = _error_class(_text(c.get("content")))
                            errors[names.get(c.get("tool_use_id"), "unknown")] += 1
                            classes[cls] += 1
                            _add_ref(class_refs[cls], session, entry)
                elif kind == "system":
                    if d.get("subtype") == "stop_hook_summary":
                        out["hooks"]["stop_evaluations"] += 1
                        for info in d.get("hookInfos") or []:
                            m = HOOK_SCRIPT_RE.search(str(info.get("command") if isinstance(info, dict) else ""))
                            if m:
                                runs[m.group(1)] += 1
                    elif d.get("subtype") == "api_error":
                        out["api_errors"]["count"] += 1
                        _add_ref(out["api_errors"]["refs"], session, entry)
                elif kind == "attachment" and isinstance(d.get("attachment"), dict):
                    a = d["attachment"]
                    if a.get("type") == "hook_non_blocking_error":
                        m = HOOK_SCRIPT_RE.search(str(a.get("command") or ""))
                        rec = crashes[m.group(1) if m else "unattributed"]
                        rec["count"] += 1
                        rec["sessions"].add(session)
                        rec["exit_codes"][str(a.get("exitCode"))[:6]] += 1
                        _add_ref(rec["refs"], session, entry)
                    elif a.get("type") == "hook_blocking_error":
                        text = str(a.get("blockingError") or "")
                        gate = next((g for g, sig in BLOCK_SIGNATURES if sig in text), "unattributed")
                        rec = blocks[gate]
                        rec["count"] += 1
                        rec["sessions"].add(session)
                        rec["events"][str(a.get("hookEvent") or "unknown")[:20]] += 1
                        _add_ref(rec["refs"], session, entry)

    def _plain(recs):
        return {k: {**v, "sessions": len(v["sessions"]),
                    **({"exit_codes": dict(v["exit_codes"])} if "exit_codes" in v else {}),
                    **({"events": dict(v["events"])} if "events" in v else {})}
                for k, v in sorted(recs.items())}

    scope.update(sessions=len(sessions), unknown_types=dict(unknown),
                 first_ts=first.isoformat() if first else None,
                 last_ts=last.isoformat() if last else None)
    out["hooks"].update(runs=dict(runs), crashes=_plain(crashes), blocks=_plain(blocks))
    out["tools"].update(calls=dict(calls), errors=dict(errors), error_classes=dict(classes),
                        refs={k: v for k, v in class_refs.items()})
    out["models"].update(by_model={k: dict(v) for k, v in sorted(by_model.items())},
                         by_agent_type={k: dict(v) for k, v in sorted(by_agent.items())})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--transcripts", default=None)
    a = ap.parse_args()
    root = Path(a.root).resolve()
    tdir = Path(a.transcripts) if a.transcripts else default_transcripts_dir(root)
    print(json.dumps(mine(tdir, days=a.days, known_agents=roster(root)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
