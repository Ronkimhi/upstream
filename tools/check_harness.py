#!/usr/bin/env python3
"""Hitch's harness audit. Stdlib only, offline.

The harness is everything around the model: the instructions every session loads, the hooks
and gates that stop it, the tier table that picks its model, the tests that prove a gate can
fail. Adam's check_machine.py asks whether the research machine's RULES are enforced. This
asks whether the agents' RUNTIME works, using the one record of what sessions actually did
(tools/harness_traces.py) beside the files that tell them what to do.

Five audits, each printing what it EXAMINED next to what it FOUND (Rule 21):

  1. traces    -- hook crashes (a crashed gate enforces nothing), blocks, tool and API
                  errors, and transcript format drift, mined from local session logs
  2. models    -- the models repo agents actually answered on, against tools/model_tiers.py
                  (a ledger `model:` field is the agent's own report; this is the far end)
  3. budget    -- bytes of always-loaded instruction text against the BUDGET ratchet
  4. drift     -- cited repo paths that do not exist, registered hooks no test watches
                  block, CI that skips harness files, Codex mirror parity
  5. self-edit -- `run harness fix` commits that touched Hitch's own graders, fix
                  predictions that did not hold, and open findings whose evidence count
                  does not re-derive from the traces or audit counts it names

ADVISORY BY DEFAULT, the posture and reason of check_machine.py. No transcripts is SCOPE
EMPTY for the trace audits and never prints OK: CI and cloud sessions have no local session
logs, and a clean status over zero transcripts is exactly the failure Rule 21 names.

Also home to validate_report(), the schema of data/harness/YYYY-Www.json. tools/validate.py
imports it, so the report's rules live in a file a `run harness fix` may not edit.

Run: python3 tools/check_harness.py [--strict] [--root PATH] [--days N] [--transcripts DIR] [--json]
Exit 0 clean (or advisory), 1 on findings under --strict.
"""
import argparse
import datetime
import importlib.util
import json
import operator
import re
import subprocess
from pathlib import Path

from agent_registry import command_key, command_rows, given_name  # noqa: E402
from harness_traces import DEFAULT_DAYS, default_transcripts_dir, mine, roster  # noqa: E402

UTC = datetime.timezone.utc

# The bloat ratchet: byte sizes the day Hitch landed (2026-09-13, Hitch's own contract and
# CLAUDE.md rows included). Text in these files rides into every session, so growth past a
# line is a finding and new text pays for itself by deleting old text. Moved only by a dated
# decision in a commit that is not a `run harness fix`.
BUDGET = {"CLAUDE.md": 66278, ".claude/agents": 117291, ".claude/skills": 6375}

# Hitch's own graders. A fix that edits one has moved its own bar: the Darwin Goedel Machine
# removed the markers its reward function used to detect hallucination.
GRADERS = ("tools/check_harness.py", "tools/harness_traces.py", "tools/tests/test_harness.py",
           ".claude/agents/hitch-harness.md")
META_GATES = ("check_machine.py", "check_model.py", "check_harness.py")
CI_PATHS = (".claude/**", "CLAUDE.md")
PATH_RE = re.compile(
    r"(?<![\w./~-])((?:tools|docs|app|tasks|\.claude|\.github)/[A-Za-z0-9_./-]*[A-Za-z0-9_-]"
    r"\.(?:py|md|json|yml|yaml|js|html|patch|sh|toml))(?![\w/-])")
REPORT_NAME_RE = re.compile(r"^\d{4}-W\d{2}$")
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
REF_RE = re.compile(rf"^{_UUID}:{_UUID}$")
ELEMENTS = {"hook", "gate", "instruction", "tool", "model", "ci", "parity", "test"}
STATUSES = {"OPEN", "FIXED", "DECLINED"}
METRIC_ROOTS = {"hooks", "tools", "api_errors", "models", "scope", "audit"}
# An OPEN finding's evidence is re-derived while its report is this young; older transcripts
# may already be gone (Claude Code deletes them after 30 days by default).
EVIDENCE_WINDOW_DAYS = 7
OPS = {"<=": operator.le, "<": operator.lt, ">=": operator.ge, ">": operator.gt,
       "==": operator.eq}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Report:
    def __init__(self):
        self.lines, self.findings, self.empty = [], [], False
        # Static audit counts, addressable as ["audit", <section>, <key>] by a report's evidence
        # and predictions, so a coverage or drift finding is scored like a crash count.
        self.metrics = {}

    def say(self, msg):
        self.lines.append(msg)

    def find(self, msg):
        self.findings.append(msg)


def read(path: Path, default=""):
    try:
        return path.read_text()
    except Exception:  # noqa: BLE001
        return default


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


def load_tiers(root: Path):
    """The AUDITED root's tier table, so a fixture can supply its own (as check_model.py does)."""
    try:
        spec = importlib.util.spec_from_file_location(
            "model_tiers_under_harness_audit", root / "tools" / "model_tiers.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ 1. traces

def audit_traces(r: Report, traces: dict):
    s = traces["scope"]
    if not s["transcripts_dir_found"] or not (s["main_files"] + s["subagent_files"]):
        r.empty = True
        r.say("traces: SCOPE EMPTY, 0 transcripts examined in the window (this venue has no "
              "local session logs). This is not a pass")
        return
    r.say(f"traces: {s['sessions']} session(s), {s['main_files']} main and "
          f"{s['subagent_files']} subagent transcript(s), {s['lines']} lines "
          f"({s['parsed']} parsed, {s['unparsed']} unparseable), {s['first_ts']} to {s['last_ts']}")
    if s["lines"] and not s["assistant_messages"]:
        r.find(f"traces: {s['lines']} transcript lines read but 0 assistant messages "
               f"recognised: the transcript format has likely changed, so every trace metric "
               f"in this report is blind")
    if s["unknown_types"]:
        r.say(f"traces: unknown entry types counted, not parsed: {s['unknown_types']}")
    h = traces["hooks"]
    for script, rec in h["crashes"].items():
        runs = h["runs"].get(script)
        r.find(f"hook {script} crashed {rec['count']} time(s)"
               + (f" (stop summaries list it in {runs} evaluation(s))" if runs else "")
               + f" across {rec['sessions']} session(s), exit codes {rec['exit_codes']}: a hook "
               f"that crashes enforces nothing, so those sessions ended as if it had passed. "
               f"refs {rec['refs']}")
    blocks = {g: rec["count"] for g, rec in h["blocks"].items()}
    r.say(f"hooks: {h['stop_evaluations']} stop evaluation(s), "
          f"{sum(rec['count'] for rec in h['crashes'].values())} crash(es), blocks by gate "
          f"(heuristic attribution): {blocks or 'none'}")
    t = traces["tools"]
    r.say(f"tools: {sum(t['calls'].values())} call(s), {sum(t['errors'].values())} error(s) "
          f"by class {t['error_classes'] or 'none'}; api errors {traces['api_errors']['count']}")


# ------------------------------------------------------------------ 2. models

def required_models(root: Path, tiers) -> dict:
    """agent slug -> the models its owned commands require, from the audited root's table."""
    if tiers is None:
        return {}
    rows = command_rows(read(root / "CLAUDE.md"))
    out = {}
    for path in sorted((root / ".claude" / "agents").glob("*.md")):
        keys = {command_key(c) for c, agent, _v in rows if agent == given_name(path.stem)}
        need = {tiers.TIER_MODEL[tiers.COMMAND_TIER[k]] for k in keys if k in tiers.COMMAND_TIER}
        if need:
            out[path.stem] = need
    return out


def audit_models(root: Path, r: Report, traces: dict, tiers):
    if r.empty:
        r.say("models: SCOPE EMPTY, no transcripts to observe")
        return
    required = required_models(root, tiers)
    checked = 0
    for agent, models in sorted(traces["models"]["by_agent_type"].items()):
        if agent not in required:
            continue
        checked += 1
        # `other` is what the miner calls a non-Claude model id, which Claude Code writes on
        # its own synthetic messages. No model answered those, so no tier was broken.
        off = {m: n for m, n in models.items() if m not in required[agent] and m != "other"}
        if off:
            r.find(f"agent {agent} answered {sum(off.values())} message(s) on {sorted(off)}, but "
                   f"its commands require {sorted(required[agent])} (tools/model_tiers.py). The "
                   f"transcript is the far end; a ledger model: field is the agent's own report")
    out_tokens = {m: v.get("output_tokens", 0) for m, v in traces["models"]["by_model"].items()}
    r.say(f"models: {checked} repo agent type(s) observed against a required tier; output "
          f"tokens by model {out_tokens or 'none'}"
          + ("" if tiers else "  <- tools/model_tiers.py unreadable, tier check skipped"))


# ------------------------------------------------------------------ 3. budget

def _bytes(root: Path, rel: str) -> int:
    p = root / rel
    if p.is_file():
        return len(p.read_bytes())
    if p.is_dir():
        return sum(len(f.read_bytes()) for f in p.rglob("*.md") if f.is_file())
    return 0


def audit_budget(root: Path, r: Report, budget=None) -> dict:
    budget = BUDGET if budget is None else budget
    sizes = {k: _bytes(root, k) for k in budget}
    for k, limit in budget.items():
        if sizes[k] > limit:
            r.find(f"{k} is {sizes[k]} bytes, {sizes[k] - limit} over its {limit}-byte budget: "
                   f"text every session loads grew without a matching deletion. Delete before "
                   f"adding, or move the line by a dated decision")
    r.metrics["budget"] = {"over": sum(1 for k in budget if sizes[k] > budget[k])}
    r.say("budget: " + ", ".join(f"{k} {sizes[k]} of {budget[k]} B" for k in budget))
    return sizes


# ------------------------------------------------------------------ 4. drift

def registered_hooks(root: Path) -> set:
    settings = load_json(root / ".claude" / "settings.json", {}) or {}
    names = set()
    for entries in (settings.get("hooks") or {}).values():
        for entry in entries or []:
            for h in (entry.get("hooks") or []) if isinstance(entry, dict) else []:
                m = re.search(r"hooks/([A-Za-z0-9_.\-]+\.py)", str(h.get("command") or ""))
                if m:
                    names.add(m.group(1))
    return names


def audit_drift(root: Path, r: Report):
    sources = [root / "CLAUDE.md", *sorted((root / ".claude" / "agents").glob("*.md")),
               *sorted((root / ".claude" / "skills").rglob("*.md"))]
    cited = {}
    for src in sources:
        for m in PATH_RE.finditer(read(src)):
            cited.setdefault(m.group(1), set()).add(src.relative_to(root).as_posix())
    missing = sorted(p for p in cited if not (root / p).exists())
    for p in missing:
        r.find(f"{p} is cited by {sorted(cited[p])} but does not exist: an instruction that "
               f"points at a file that is gone")
    r.metrics["drift"] = {"cited": len(cited), "missing": len(missing)}
    r.say(f"drift: {len(cited) - len(missing)} of {len(cited)} cited repo path(s) exist")

    hooks = sorted(registered_hooks(root))
    tests = [read(p) for p in sorted((root / "tools" / "tests").glob("test_*.py"))]

    def names_hook(text: str, hook: str) -> bool:
        # Tests spell a hook three ways: `campaign-gate.py`, `campaign-gate`, or a case table
        # keyed "campaign" beside an f"{name}-gate.py" path.
        stem = hook.removesuffix(".py")
        bare = stem.removesuffix("-gate")
        return stem in text or ("-gate.py" in text and (f'"{bare}"' in text or f"'{bare}'" in text))

    untested = [h for h in hooks if not any(names_hook(t, h) and "block" in t for t in tests)]
    for h in untested:
        r.find(f"hook {h} is registered but no file under tools/tests/ names it beside a block "
               f"assertion (heuristic): nothing has watched this gate refuse")
    r.metrics["hook_tests"] = {"registered": len(hooks), "untested": len(untested)}
    r.say(f"hook tests: {len(hooks) - len(untested)} of {len(hooks)} registered hook(s) named "
          f"by a test that asserts a block (heuristic)")

    ci = read(root / ".github" / "workflows" / "ci.yml")
    if not ci:
        r.metrics["ci"] = {"missing_paths": len(CI_PATHS), "missing_meta_gates": len(META_GATES)}
        r.find(".github/workflows/ci.yml is missing or unreadable: no gate runs where a session "
               "cannot switch it off")
    else:
        m = re.search(r"paths:\s*\[([^\]]*)\]", ci)
        paths = {s.strip().strip("'\"") for s in m.group(1).split(",")} if m else set()
        missing_paths = [need for need in CI_PATHS if m and need not in paths]
        for need in missing_paths:
            r.find(f"ci.yml push paths {sorted(paths)} omit {need}: a change there triggers "
                   f"no gate")
        missing_gates = [gate for gate in META_GATES if f"tools/{gate}" not in ci]
        for gate in missing_gates:
            r.find(f"ci.yml never runs tools/{gate}")
        r.metrics["ci"] = {"missing_paths": len(missing_paths),
                           "missing_meta_gates": len(missing_gates)}
        r.say(f"ci: push paths {sorted(paths) if m else 'unfiltered'}; "
              f"{len(META_GATES) - len(missing_gates)} of {len(META_GATES)} meta-gate(s) run")

    agents_md, codex = root / "AGENTS.md", root / ".codex"
    if not agents_md.exists() and not codex.exists():
        r.metrics["parity"] = {"divergences": 0}
        r.say("parity: no Codex mirror in this tree (examined 0)")
        return
    problems = []
    if ".Codex/" in read(agents_md):
        problems.append("AGENTS.md carries mangled `.Codex/` paths")
    if re.search(r"(?:/Users/|/home/)", read(codex / "hooks.json")):
        problems.append(".codex/hooks.json registers absolute paths")
    for src in sorted((root / ".claude" / "hooks").glob("*.py")):
        twin = codex / "hooks" / src.name
        if not twin.is_file() or twin.read_bytes() != src.read_bytes():
            problems.append(f".codex/hooks/{src.name} is missing or differs from .claude/hooks/")
    n_claude = len(list((root / ".claude" / "agents").glob("*.md")))
    n_codex = len(list((codex / "agents").glob("*.toml")))
    if n_codex < n_claude:
        problems.append(f".codex/agents mirrors {n_codex} of {n_claude} agent contract(s)")
    for p in problems:
        r.find(f"parity: {p}")
    r.metrics["parity"] = {"divergences": len(problems)}
    r.say(f"parity: Codex mirror present, {len(problems)} divergence(s)")


# ------------------------------------------------------------------ 5. self-edit and predictions

def _git(root: Path, *args):
    try:
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                           timeout=20)
        return p.stdout if p.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def audit_self_edit(root: Path, r: Report, commits: int = 60):
    log = _git(root, "log", f"-n{commits}", "--name-only", "--format=%x1e%h%x1f%s")
    if log is None:
        r.say("self-edit: git history unreadable (examined 0 commits)")
        return
    examined = 0
    for block in log.split("\x1e"):
        if not block.strip():
            continue
        examined += 1
        head, _, files = block.partition("\n")
        sha, _, subject = head.partition("\x1f")
        if not subject.startswith("[run harness fix"):
            continue
        touched = sorted(set(files.split()) & set(GRADERS))
        if touched:
            r.find(f"commit {sha} is a harness fix that edited Hitch's own grader(s) {touched}: "
                   f"an improver that moves its own bar has graded itself")
    r.say(f"self-edit: {examined} recent commit(s) examined for fixes touching "
          f"{len(GRADERS)} grader path(s)")


def metric_value(traces: dict, path: list):
    node = traces
    for i, key in enumerate(path):
        if not isinstance(node, dict):
            return None
        node = node.get(key, 0 if i == len(path) - 1 else {})
    return node if isinstance(node, (int, float)) and not isinstance(node, bool) else None


def _day(value):
    """The UTC midnight a YYYY-MM-DD (or longer ISO) string starts with, or None."""
    try:
        return datetime.datetime.fromisoformat(str(value)[:10]).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _rederive_evidence(path, f, report, as_of, r, now, traces_since) -> bool | None:
    """Whether an OPEN finding's evidence count re-derives from the data it names.

    An ["audit", ...] count must equal this run's audit on the day the report was written. A
    trace count may not exceed what the traces hold since its window's first day: more lines
    are counted than the run saw, never fewer, so only an overclaim fails. None when this
    venue cannot check it.
    """
    ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
    metric, count = ev.get("metric") or [], ev.get("count")
    if not (isinstance(count, int) and metric and isinstance(metric[0], str)):
        return None
    if metric[0] == "audit":
        if as_of.date() != now.date() or len(metric) < 2 or metric[1] not in r.metrics:
            return None
        value = metric_value({"audit": r.metrics}, metric)
        if value != count:
            r.find(f"{path.name} {f.get('id')}: evidence {metric} records {count} but the audit "
                   f"reads {value} today: a finding's count must re-derive from the data it names")
            return False
        return True
    window = report.get("window") if isinstance(report.get("window"), dict) else {}
    since = _day(window.get("from"))
    traces = traces_since(since) if since else None
    if not traces or not traces["scope"]["assistant_messages"]:
        return None
    value = metric_value(traces, metric)
    if value is None or count > value:
        r.find(f"{path.name} {f.get('id')}: evidence {metric} records {count} but the traces "
               f"since {since.date()} hold {value}: a finding may not claim more than its data "
               f"shows")
        return False
    return True


def audit_predictions(root: Path, r: Report, tdir, now, known) -> list:
    """Score FIXED findings' predictions and re-derive OPEN findings' evidence counts.

    A metric under ["audit", ...] reads this run's static audit counts (r.metrics, so
    audit_budget and audit_drift run first); every other root reads the traces.
    """
    folder = root / "data" / "harness"
    reports = sorted(folder.glob("20*-W*.json")) if folder.is_dir() else []
    mined = {}

    def traces_since(day):
        if day not in mined:
            mined[day] = mine(tdir, since=day, known_agents=known, now=now)
        return mined[day]

    verdicts, scored, held, pending, rederived, overclaimed = [], 0, 0, 0, 0, 0
    for path in reports:
        report = load_json(path, {}) or {}
        as_of = _day(report.get("as_of"))
        for f in report.get("findings") or []:
            if not isinstance(f, dict):
                continue
            if (f.get("status") == "OPEN" and as_of
                    and (now - as_of).days <= EVIDENCE_WINDOW_DAYS):
                ok = _rederive_evidence(path, f, report, as_of, r, now, traces_since)
                rederived += ok is not None
                overclaimed += ok is False
            if f.get("status") != "FIXED":
                continue
            pred = f.get("prediction") or {}
            metric = pred.get("metric") or []
            fixed = _day(f.get("fixed_on"))
            try:
                due = fixed + datetime.timedelta(days=int(pred.get("window_days")))
            except (ValueError, TypeError):
                r.find(f"{path.name} {f.get('id')}: FIXED without a readable fixed_on and "
                       f"window_days, so its prediction can never be scored")
                continue
            if metric[:1] == ["audit"]:
                if len(metric) < 2 or metric[1] not in r.metrics:
                    r.find(f"{path.name} {f.get('id')}: prediction {metric} names no audit "
                           f"section this run computed, so it cannot be scored")
                    continue
                source = {"audit": r.metrics}
            else:
                source = traces_since(fixed) if now >= due else None
                if source is not None and not source["scope"]["assistant_messages"]:
                    source = None
            if now < due or source is None:
                pending += 1
                verdicts.append({"finding": f.get("id"), "verdict": "PENDING"})
                continue
            value, op = metric_value(source, metric), OPS.get(pred.get("op"))
            if value is None or op is None or not isinstance(pred.get("value"), (int, float)):
                r.find(f"{path.name} {f.get('id')}: prediction is not scoreable "
                       f"(metric {pred.get('metric')}, op {pred.get('op')})")
                continue
            scored += 1
            if op(value, pred["value"]):
                held += 1
                verdicts.append({"finding": f.get("id"), "verdict": "HELD", "observed": value})
            else:
                verdicts.append({"finding": f.get("id"), "verdict": "DID_NOT_HOLD", "observed": value})
                r.find(f"{path.name} {f.get('id')}: fix {f.get('fix_commit')} predicted "
                       f"{pred['metric']} {pred['op']} {pred['value']}, observed {value} since "
                       f"{fixed.date()}. DID_NOT_HOLD: revert or re-diagnose")
    r.say(f"predictions: {scored} scored ({held} held), {pending} pending, across "
          f"{len(reports)} report(s); evidence: {rederived} open finding count(s) re-derived, "
          f"{overclaimed} did not")
    return verdicts


# ------------------------------------------------------------------ report schema

def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _finding_errors(f, ctx: str, stem: str) -> list:
    if not isinstance(f, dict):
        return [f"{ctx} must be an object"]
    need = ("id", "rank", "element", "evidence", "first_failure", "diagnosis", "smallest_change",
            "prediction", "regression_case", "status")
    missing = [k for k in need if k not in f]
    if missing:
        return [f"{ctx} missing keys: {', '.join(missing)}"]
    errs = []
    if not re.fullmatch(rf"{re.escape(stem)}-F[1-3]", str(f["id"])):
        errs.append(f"{ctx} id {f['id']!r} must be {stem}-F1, -F2 or -F3")
    if f["element"] not in ELEMENTS:
        errs.append(f"{ctx}.element {f['element']!r} not in {sorted(ELEMENTS)}")
    ev = f["evidence"]
    if not (isinstance(ev, dict) and isinstance(ev.get("metric"), list)
            and isinstance(ev.get("count"), int) and isinstance(ev.get("denominator"), int)
            and isinstance(ev.get("refs"), list)):
        errs.append(f"{ctx}.evidence needs metric (list), count and denominator (integers) and "
                    f"refs (list): a finding without a denominator cannot be falsified")
    else:
        if any(not isinstance(x, str) or not REF_RE.match(x) for x in ev["refs"]):
            errs.append(f"{ctx}.evidence refs must be <session-uuid>:<entry-uuid>")
        if not (ev["metric"] and all(isinstance(k, str) for k in ev["metric"])
                and ev["metric"][0] in METRIC_ROOTS):
            errs.append(f"{ctx}.evidence metric must start with one of {sorted(METRIC_ROOTS)}, "
                        f"so its count can be re-derived")
        if any(len(s) > 300 or "\n" in s for s in _strings(ev)):
            errs.append(f"{ctx}.evidence carries text: evidence is metrics and refs, never a transcript")
    pred = f["prediction"]
    metric = pred.get("metric") if isinstance(pred, dict) else None
    if not (isinstance(metric, list) and metric and all(isinstance(k, str) for k in metric)
            and metric[0] in METRIC_ROOTS and pred.get("op") in OPS
            and isinstance(pred.get("value"), (int, float))
            and isinstance(pred.get("window_days"), int) and pred["window_days"] >= 1):
        errs.append(f"{ctx}.prediction needs metric [{'|'.join(sorted(METRIC_ROOTS))}, ...], op "
                    f"in {sorted(OPS)}, a numeric value and window_days >= 1")
    if f["status"] not in STATUSES:
        errs.append(f"{ctx}.status {f['status']!r} not in {sorted(STATUSES)}")
    if f["status"] == "FIXED" and not (
            re.fullmatch(r"[0-9a-f]{7,40}", str(f.get("fix_commit") or ""))
            and DATE_RE.match(str(f.get("fixed_on") or "")) and str(f.get("fail_before") or "").strip()):
        errs.append(f"{ctx} is FIXED without fix_commit, fixed_on and fail_before: a fix whose "
                    f"test never failed first proves nothing")
    return errs


def validate_report(obj, stem: str) -> list:
    """Every error in one data/harness/YYYY-Www.json report, empty when it is well formed."""
    if not isinstance(obj, dict):
        return ["report must be a JSON object"]
    errs = [] if REPORT_NAME_RE.match(stem) else [f"file name {stem!r} is not YYYY-Www"]
    required = ("id", "as_of", "generated_by", "window", "scope", "audits", "findings",
                "deletion_candidate", "prior_predictions", "changelog")
    missing = [k for k in required if k not in obj]
    if missing:
        return errs + [f"missing keys: {', '.join(missing)}"]
    if obj["id"] != f"HAR-{stem}":
        errs.append(f"id {obj['id']!r} must be HAR-{stem}")
    if obj["generated_by"] != "hitch-harness":
        errs.append("generated_by must be hitch-harness")
    if not DATE_RE.match(str(obj["as_of"])):
        errs.append("as_of must be YYYY-MM-DD")
    strings = list(_strings(obj))
    if any(re.search(r"/Users/|/home/|[A-Za-z]:\\", s) for s in strings):
        errs.append("carries an absolute path: this repo is public, and a report holds counts "
                    "and refs, never a machine's paths")
    if any(len(s) > 600 for s in strings):
        errs.append("carries a string over 600 characters: a report is counts and short prose, "
                    "never pasted transcript text")
    findings = obj["findings"]
    if not isinstance(findings, list) or len(findings) > 3:
        errs.append("findings must be a list of at most 3")
    else:
        ids = [f.get("id") for f in findings if isinstance(f, dict)]
        if len(ids) != len(set(ids)):
            errs.append("finding ids must be unique")
        for i, f in enumerate(findings):
            errs += _finding_errors(f, f"findings[{i}]", stem)
    dc = obj["deletion_candidate"]
    if not (isinstance(dc, dict) and str(dc.get("path") or "").strip()
            and str(dc.get("reason") or "").strip()):
        errs.append("deletion_candidate needs a path and a reason: every report names one thing "
                    "to remove")
    for key in ("audits", "prior_predictions"):
        if not isinstance(obj[key], list):
            errs.append(f"{key} must be a list")
    cl = obj["changelog"]
    if not (isinstance(cl, list) and cl
            and all(isinstance(c, dict) and c.get("ts") and c.get("change") for c in cl)):
        errs.append("changelog must be a non-empty list of {ts, change}")
    return errs


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--transcripts", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    now = datetime.datetime.now(UTC)

    missing = [p for p in ("CLAUDE.md", ".claude/agents", ".claude/settings.json")
               if not (root / p).exists()]
    if missing:
        print(f"check_harness: SCOPE EMPTY at {root}. Missing: {', '.join(missing)}. This is "
              f"not a pass; the audit could not see the harness.")
        return 1 if a.strict else 0

    tdir = Path(a.transcripts) if a.transcripts else default_transcripts_dir(root)
    known = roster(root)
    traces = mine(tdir, days=a.days, known_agents=known, now=now)
    r = Report()
    audit_traces(r, traces)
    audit_models(root, r, traces, load_tiers(root))
    sizes = audit_budget(root, r)
    audit_drift(root, r)
    audit_self_edit(root, r)
    verdicts = audit_predictions(root, r, tdir, now, known)

    if a.json:
        print(json.dumps({"as_of": now.date().isoformat(), "examined": r.lines,
                          "findings": r.findings, "traces_scope_empty": r.empty,
                          "sizes": sizes, "predictions": verdicts, "traces": traces,
                          "audit_metrics": r.metrics},
                         indent=2, sort_keys=True))
        return 1 if (a.strict and r.findings) else 0

    print(f"check_harness: {now.date()}  ({'strict' if a.strict else 'advisory'})")
    for ln in r.lines:
        print(f"  {ln}")
    for f_ in r.findings:
        print(f"  FINDING  {f_}")
    if r.findings:
        print(f"check_harness: {len(r.findings)} finding(s)"
              + ("" if a.strict else "; advisory, exit 0. Run --strict to block on these"))
        return 1 if a.strict else 0
    if r.empty:
        print("check_harness: no static findings, but traces are SCOPE EMPTY, so this is not a "
              "clean harness")
        return 0
    print("check_harness: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
