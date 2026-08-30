#!/usr/bin/env python3
"""Adam's machine audit. Stdlib only, offline.

Every other gate in this repo checks the ANALYSIS. This one checks the MACHINE: whether the
rules the constitution states are actually enforced anywhere, whether every command has an
owner and a gate, whether the gates themselves can fail, and whether the agents are alive.

It exists because of a pattern visible in this repo's own history. Every gate here was born
the same way: a rule was prose, prose got broken, someone wrote a check. check_screen.py,
check_render.py, the section 3 band partition, the section 0.2 band partition. That promotion
from promise to gate is the highest-leverage move in the system and until now it happened only
when somebody happened to notice. This makes noticing a scheduled job.

Six audits, each reporting what it EXAMINED next to what it FOUND (method section 9, Rule 21):

  1. promise ledger   -- commands whose verify column names no machine gate, plus a heuristic
                         scan of the constitution's must/never clauses
  2. unowned commands -- command-table rows naming no agent
  3. gate falsifiability -- check_*.py scripts with no test that invokes THAT gate file and
                         asserts a FAILURE against its result in the same method
  4. hook drift       -- settings.json registrations vs what is on disk, both directions
  5. silent agents    -- agents with no ledger evidence in the window
  6. routines         -- REGISTERED but never evidenced, said in those words

ADVISORY BY DEFAULT. It prints findings and exits 0, because the promise-ledger backlog on
day one is real and a gate that fails from its first run is a gate people route around. Run
with --strict to make findings blocking; the plan is to harden once the backlog is worked
down, dated in the ledger when it happens. Same posture as the citation-debt line and the
section 4 citation bar.

Run: python3 tools/check_machine.py [--strict] [--root PATH] [--days N] [--json]
Exit 0 clean (or advisory), 1 on findings under --strict.
"""
import argparse
import ast
import datetime
import json
import re
import sys
from pathlib import Path

# The command table's regexes and its walk moved to tools/agent_registry.py when the page
# started rendering agent contracts: two parsers of one table agree only on the day they
# are written. Re-exported here because this module's own name is what tests import.
from agent_registry import (  # noqa: E402
    AGENT_RE,
    COMMAND_RE,
    GATE_RE,
    command_rows,
)

SILENT_AGENT_DAYS = 21
# Obligation words the constitution uses when it is stating a rule rather than describing one.
OBLIGATION_RE = re.compile(r"\b(must|never|always|required|forbidden|refuses?|may not)\b", re.I)
GATE_REFUSAL_MARKER = re.compile(r"^\s*#\s*gate_refusal:\s*(\S+)")
GATE_STEM_RE = re.compile(r"check_[a-z_]+")
REFUSAL_ASSERTS = frozenset({
    "assertEqual", "assertNotEqual", "assertIn", "assertNotIn", "assertFalse", "assertTrue",
})


class Report:
    def __init__(self):
        self.lines, self.findings = [], []

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


def audit_promises(root: Path, r: Report):
    """A rule with no gate behind it is a promise, and promises regress."""
    claude = read(root / "CLAUDE.md")
    rows = command_rows(claude)
    if not rows:
        r.find("CLAUDE.md command table parsed to 0 rows: the audit's own scope is empty, "
               "which is a broken parser and not a clean machine")
        return
    ungated = [(cmd, verify) for cmd, _a, verify in rows if not GATE_RE.search(verify)]
    for cmd, _v in ungated:
        r.find(f"command `{cmd}` states a verify list that names no tools/check_*.py: it is "
               f"honoured by memory, not by an exit code")
    r.say(f"promise ledger (commands): {len(rows) - len(ungated)} of {len(rows)} command(s) "
          f"name a machine gate in their verify column")

    # Heuristic half, labelled as such. Both matched AND unmatched counts are printed, because
    # "18 unenforced rules" alone cannot be told apart from a matcher that failed 18 times.
    gate_text = "\n".join(read(p) for p in sorted((root / "tools").glob("check_*.py")))
    gate_text += "\n" + read(root / "tools" / "validate.py")
    obligations, linked = [], 0
    for doc in ("docs/method.md", "CLAUDE.md"):
        for i, line in enumerate(read(root / doc).splitlines(), 1):
            if not OBLIGATION_RE.search(line) or line.strip().startswith("|"):
                continue
            obligations.append((doc, i, line.strip()))
            # A crude link: does any gate mention a distinctive token from this clause?
            toks = [t for t in re.findall(r"`([a-z_]{4,})`", line)]
            if toks and any(t in gate_text for t in toks):
                linked += 1
    r.say(f"promise ledger (prose, HEURISTIC): {linked} of {len(obligations)} obligation "
          f"sentence(s) in method.md + CLAUDE.md name a backticked identifier that appears in "
          f"a gate. The other {len(obligations) - linked} are unlinked, which means EITHER "
          f"unenforced OR phrased without an identifier this matcher can see. Read them; do "
          f"not quote this number as a defect count")


def audit_owners(root: Path, r: Report):
    rows = command_rows(read(root / "CLAUDE.md"))
    unowned = [cmd for cmd, agent, _v in rows if agent is None]
    for cmd in unowned:
        r.find(f"command `{cmd}` names no owning agent: nobody is accountable for it and "
               f"nobody reads its record before running it")
    r.say(f"owners: {len(rows) - len(unowned)} of {len(rows)} command(s) name an agent")


def _gate_stems_in_text(text: str, known: set[str] | None = None) -> set[str]:
    found = set(GATE_STEM_RE.findall(text))
    if known is None:
        return found
    return {g for g in found if g in known}


def _marker_gates(func: ast.AST, source: str) -> set[str]:
    """Explicit per-method gate_refusal marker: comment or decorator."""
    gates: set[str] = set()
    if not isinstance(func, ast.FunctionDef):
        return gates
    start = func.lineno - 1
    end = (func.decorator_list[0].lineno - 1 if func.decorator_list else start)
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
            if dec.func.id == "gate_refusal" and dec.args:
                if isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                    gates.add(dec.args[0].value)
    for line in source.splitlines()[start:end + 1]:
        m = GATE_REFUSAL_MARKER.match(line)
        if m:
            gates.add(m.group(1).strip())
    return {g for g in gates if g.startswith("check_")}


def _segment(source: str, node: ast.AST) -> str:
    return ast.get_source_segment(source, node) or ""


def _refusal_result(expr: ast.AST) -> tuple[str, str] | None:
    """Return (result variable, inspected value kind) for a refusal assertion."""
    if (isinstance(expr, ast.Attribute) and expr.attr == "returncode"
            and isinstance(expr.value, ast.Name)):
        return expr.value.id, "returncode"
    if isinstance(expr, ast.Name):
        return expr.id, "direct"
    return None


def _asserted_refusal_results(method: str, call: ast.Call, source: str) -> set[tuple[str, str]]:
    """Result variables whose return code this assertion expects to refuse."""
    results: set[tuple[str, str]] = set()
    args = call.args
    if method == "assertEqual" and len(args) >= 2:
        if isinstance(args[1], ast.Constant) and args[1].value != 0:
            result = _refusal_result(args[0])
            if result:
                results.add(result)
        if isinstance(args[0], ast.Constant) and args[0].value != 0:
            result = _refusal_result(args[1])
            if result:
                results.add(result)
    if method == "assertNotEqual" and len(args) >= 2:
        if isinstance(args[1], ast.Constant) and args[1].value == 0:
            result = _refusal_result(args[0])
            if result:
                results.add(result)
        if isinstance(args[0], ast.Constant) and args[0].value == 0:
            result = _refusal_result(args[1])
            if result:
                results.add(result)
    return results


def _compare_refusal_result(node: ast.Compare) -> tuple[str, str] | None:
    """Return result binding when a process or function result is compared to refusal."""
    if len(node.ops) != 1 or len(node.comparators) != 1:
        return None
    left, right = node.left, node.comparators[0]
    left_result, right_result = _refusal_result(left), _refusal_result(right)
    left_value = left.value if isinstance(left, ast.Constant) else None
    right_value = right.value if isinstance(right, ast.Constant) else None
    if isinstance(node.ops[0], ast.Eq):
        if left_result and isinstance(right_value, int) and right_value != 0:
            return left_result
        if right_result and isinstance(left_value, int) and left_value != 0:
            return right_result
    if isinstance(node.ops[0], ast.NotEq):
        if left_result and right_value == 0:
            return left_result
        if right_result and left_value == 0:
            return right_result
    return None


def _argv_items(expr: ast.AST, values: dict[str, ast.AST]) -> list[ast.AST] | None:
    """Resolve a literal/variable argv vector without interpreting shell text."""
    if isinstance(expr, ast.Name):
        return _argv_items(values[expr.id], values) if expr.id in values else None
    if isinstance(expr, (ast.List, ast.Tuple)):
        return expr.elts
    return None


def _path_segments(expr: ast.AST, values: dict[str, ast.AST],
                   depth: int = 0) -> list[str]:
    """Ordered string segments that build one argv path. Unresolvable parts contribute none.

    Covers the forms this repo's tests actually use: a bare literal, `ROOT / "tools" /
    "check_x.py"`, and `str(...)` or `os.fspath(...)` wrapped around either. A `ROOT` this
    cannot resolve contributes nothing rather than aborting, because the caller only needs
    the TAIL of the path.
    """
    if depth > 12:
        return []
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return [expr.value]
    if isinstance(expr, ast.Name):
        return _path_segments(values[expr.id], values, depth + 1) if expr.id in values else []
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        return (_path_segments(expr.left, values, depth + 1)
                + _path_segments(expr.right, values, depth + 1))
    if isinstance(expr, ast.Call):
        segments: list[str] = []
        for arg in expr.args:
            segments += _path_segments(arg, values, depth + 1)
        return segments
    return []


def _exact_gate_argument(expr: ast.AST, known: set[str],
                         values: dict[str, ast.AST]) -> set[str]:
    """Gate whose FILE this argv item resolves to, never one it merely mentions.

    Mentioning a gate is not invoking it. `subprocess.run(["false", "check_x.py"])` names one
    executable and runs another, and a bare `["check_x.py"]` runs whatever file of that name
    the test's working directory happens to hold, which is not the audited gate and may not
    exist at all. Cass's 2026-08-30 probe was exactly this shape. So the item has to resolve
    to `tools/<gate>.py`, which is the one place the gates under audit live: the last two path
    segments must be `tools` and `<gate>.py`, and `<gate>` must be a real file on disk.
    """
    segments = _path_segments(expr, values)
    if not segments or any(re.search(r"\s", seg) for seg in segments):
        return set()
    parts = [part for part in "/".join(segments).split("/") if part not in ("", ".")]
    if len(parts) < 2 or parts[-2] != "tools" or not parts[-1].endswith(".py"):
        return set()
    stem = parts[-1][:-3]
    return {stem} if stem in known else set()


def _is_python_executable(expr: ast.AST) -> bool:
    if (isinstance(expr, ast.Attribute) and expr.attr == "executable"
            and isinstance(expr.value, ast.Name) and expr.value.id == "sys"):
        return True
    return (isinstance(expr, ast.Constant) and isinstance(expr.value, str)
            and Path(expr.value).name.startswith("python"))


def _subprocess_gate(call: ast.Call, values: dict[str, ast.AST], known: set[str]) -> set[str]:
    """Gate actually executed by a subprocess argv, under the allowed invocation forms."""
    if not call.args:
        return set()
    argv = _argv_items(call.args[0], values)
    if not argv:
        return set()
    direct = _exact_gate_argument(argv[0], known, values)
    if direct:
        return direct
    if len(argv) >= 2 and _is_python_executable(argv[0]):
        return _exact_gate_argument(argv[1], known, values)
    return set()


def _is_subprocess_run(call: ast.Call) -> bool:
    return (isinstance(call.func, ast.Attribute) and call.func.attr == "run"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in {"subprocess", "runner"})


def _imported_gate_calls(func: ast.FunctionDef, known: set[str]) -> dict[str, str]:
    """Map locally imported module/function aliases to their gate stem."""
    imported: dict[str, str] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Import):
            for alias in node.names:
                gates = _gate_stems_in_text(alias.name, known)
                if len(gates) == 1:
                    imported[alias.asname or alias.name.split(".")[-1]] = next(iter(gates))
        elif isinstance(node, ast.ImportFrom) and node.module:
            gates = _gate_stems_in_text(node.module, known)
            if len(gates) == 1:
                gate = next(iter(gates))
                for alias in node.names:
                    imported[alias.asname or alias.name] = gate
    return imported


def _called_imported_gate(call: ast.Call, imported: dict[str, str]) -> set[str]:
    if isinstance(call.func, ast.Name):
        return {imported[call.func.id]} if call.func.id in imported else set()
    if (isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)
            and call.func.value.id in imported):
        return {imported[call.func.value.id]}
    return set()


def _method_certifies_gates(func: ast.FunctionDef, source: str,
                            known: set[str]) -> dict[str, tuple[str, str]]:
    """Certify a gate only from invocation result to same-method refusal assertion."""
    if not func.name.startswith("test_"):
        return {}
    values: dict[str, ast.AST] = {}
    invoked: dict[str, dict[str, set[str]]] = {}
    imported = _imported_gate_calls(func, known)
    refusal_results: dict[str, set[str]] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in REFUSAL_ASSERTS:
                for result, kind in _asserted_refusal_results(node.func.attr, node, source):
                    refusal_results.setdefault(result, set()).add(kind)
        elif isinstance(node, ast.Compare):
            result = _compare_refusal_result(node)
            if result:
                refusal_results.setdefault(result[0], set()).add(result[1])
    # Walk the whole method so a direct witness inside `with tempfile...` or a sub-block
    # remains a witness. Nested function definitions are not test methods and are ignored.
    for stmt in ast.walk(func):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and stmt is not func:
            continue
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            value = stmt.value
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            target_names = [target.id for target in targets if isinstance(target, ast.Name)]
            if not isinstance(value, ast.Call):
                for target in target_names:
                    values[target] = value
                continue
            gates = (_subprocess_gate(value, values, known)
                     if _is_subprocess_run(value) and value.args else _called_imported_gate(value, imported))
            for target in target_names:
                if gates:
                    kind = "process" if _is_subprocess_run(value) else "function"
                    invoked.setdefault(target, {}).setdefault(kind, set()).update(gates)
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            # An unbound call cannot supply the return value a refusal assertion must inspect.
            continue
    marked = _marker_gates(func, source)
    gates = set()
    for result, inspected_kinds in refusal_results.items():
        for invocation_kind, result_gates in invoked.get(result, {}).items():
            compatible = ((invocation_kind == "process" and "returncode" in inspected_kinds)
                          or (invocation_kind == "function" and "direct" in inspected_kinds))
            if compatible and len(result_gates) == 1:
                gates |= result_gates
    if marked:
        gates &= {g for g in marked if g in known}
    rel = ""  # filled by caller
    return {g: (rel, func.name) for g in gates}


def gate_refusal_certifications(root: Path) -> dict[str, list[tuple[str, str]]]:
    """Per gate: test methods that both reference it and assert a refusal in that method."""
    certs: dict[str, list[tuple[str, str]]] = {}
    test_dir = root / "tools" / "tests"
    if not test_dir.is_dir():
        return certs
    known = {p.stem for p in (root / "tools").glob("check_*.py")}
    for path in sorted(test_dir.glob("test_*.py")):
        source = read(path)
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if not isinstance(item, ast.FunctionDef) or not item.name.startswith("test_"):
                    continue
                method_certs = _method_certifies_gates(item, source, known)
                for gate, (_rel, method) in method_certs.items():
                    certs.setdefault(gate, []).append((path.name, method))
    return certs


def audit_gate_falsifiability(root: Path, r: Report):
    """A gate no test has ever seen FAIL may not be able to fail.

    Rule 21 on the gates themselves. Each gate must have at least one test METHOD that
    invokes that gate and asserts a refusal against its return value inside the same method.
    A marker, file-wide name match, or synthetic return code cannot certify a gate, and
    neither can an argv that merely MENTIONS the gate: the invoked item must resolve to
    tools/<gate>.py on disk.
    """
    gates = sorted(p for p in (root / "tools").glob("check_*.py"))
    test_files = sorted((root / "tools" / "tests").glob("test_*.py"))
    certs = gate_refusal_certifications(root)
    covered, uncovered = [], []
    for g in gates:
        stem = g.stem
        if stem in certs:
            covered.append(stem)
        else:
            uncovered.append(stem)
            named_files = sorted(
                tf.name for tf in test_files if stem in read(tf))
            if not named_files:
                why = "is named in no test file"
            else:
                why = (f"is named in {', '.join(named_files)}, but no test method both "
                       f"references it and asserts a refusal in that same method")
            r.find(f"tools/{stem}.py {why}. A gate nobody has watched refuse something is a "
                   f"gate that may not be able to")
    r.say(f"gate falsifiability: {len(covered)} of {len(gates)} gate(s) have a test method "
          f"that references the gate and asserts a refusal"
          + ("  <- no test files found at all, so this passed over nothing"
             if not test_files else ""))


def audit_hooks(root: Path, r: Report):
    settings = load_json(root / ".claude" / "settings.json", {}) or {}
    registered = set()
    for key in ("Stop", "SubagentStop", "PreToolUse", "PostToolUse", "SessionStart"):
        for entry in (settings.get("hooks") or {}).get(key, []):
            for h in entry.get("hooks", []):
                m = re.search(r"hooks/([a-z_\-]+\.py)", str(h.get("command") or ""))
                if m:
                    registered.add(m.group(1))
    on_disk = {p.name for p in (root / ".claude" / "hooks").glob("*.py")
               if p.name != "write_targets.py"}  # a helper module, never registered itself
    for name in sorted(registered - on_disk):
        r.find(f"hook {name} is registered in settings.json but absent from .claude/hooks/: "
               f"it fires nothing and reports nothing")
    for name in sorted(on_disk - registered):
        r.find(f"hook {name} exists on disk but is registered nowhere in settings.json: it is "
               f"a gate that never runs, which looks identical to a gate that always passes")
    r.say(f"hooks: {len(registered & on_disk)} of {len(registered | on_disk)} hook(s) both "
          f"registered and present")


def audit_agents(root: Path, r: Report, today: datetime.date, days: int):
    agents = sorted(p.stem for p in (root / ".claude" / "agents").glob("*.md"))
    ledger = read(root / "data" / "ledger.md")
    recent = []
    for line in ledger.splitlines():
        if len(line) < 10 or not line[:4].isdigit():
            continue
        try:
            d = datetime.date.fromisoformat(line[:10])
        except ValueError:
            continue
        if (today - d).days <= days:
            recent.append(line.lower())
    blob = "\n".join(recent)
    live = []
    for a in agents:
        # Match the agent's given name (nell-scanner -> nell), which is how the ledger and the
        # command table refer to them.
        name = a.split("-")[0]
        if name in blob or a in blob:
            live.append(a)
        else:
            r.find(f"agent {a} has no ledger evidence in {days} days. Either the command is "
                   f"not being run or its ledger line does not name it")
    r.say(f"agents: {len(live)} of {len(agents)} agent(s) evidenced in the last {days} days"
          + ("  <- NO ledger lines in the window at all, so this audit passed over nothing"
             if not recent else ""))


def audit_routines(root: Path, r: Report):
    sess = load_json(root / "data" / "health" / "sessions.json", {}) or {}
    status = sess.get("routine_status") or {}
    evidence = sess.get("routine_evidence") or {}
    for name, state in sorted(status.items()):
        if state == "REGISTERED" and not evidence.get(name):
            r.find(f"routine {name} is REGISTERED but has never fired. REGISTERED IS NOT LIVE")
        elif state == "LIVE" and not evidence.get(name):
            r.find(f"routine {name} is marked LIVE with no routine_evidence entry: the flip to "
                   f"LIVE requires an OBSERVED fire, not an assertion")
    r.say(f"routines: {len(status)} declared, "
          f"{sum(1 for n in status if evidence.get(n))} with evidenced first fires")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--days", type=int, default=SILENT_AGENT_DAYS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    today = datetime.datetime.now(datetime.timezone.utc).date()

    r = Report()
    # The audit's own scope check, first. An empty tree must not read as a healthy one: that
    # is the exact failure Rule 21 exists to catch, and this file has no standing to report on
    # anyone else's blind spots if it cannot see its own.
    REQUIRED = ("CLAUDE.md", "docs/method.md", "tools", ".claude/agents",
                ".claude/settings.json", "data/ledger.md")
    missing = [p for p in REQUIRED if not (root / p).exists()]
    if missing:
        print(f"check_machine: SCOPE EMPTY at {root}. Missing: {', '.join(missing)}. "
              f"This is not a pass; the audit could not see the machine.")
        return 1 if a.strict else 0

    audit_promises(root, r)
    audit_owners(root, r)
    audit_gate_falsifiability(root, r)
    audit_hooks(root, r)
    audit_agents(root, r, today, a.days)
    audit_routines(root, r)

    if a.json:
        print(json.dumps({"as_of": today.isoformat(), "examined": r.lines,
                          "findings": r.findings}, indent=2))
        return 1 if (a.strict and r.findings) else 0

    print(f"check_machine: {today}  (advisory)" if not a.strict
          else f"check_machine: {today}  (strict)")
    for ln in r.lines:
        print(f"  {ln}")
    for f_ in r.findings:
        print(f"  FINDING  {f_}")
    if r.findings:
        print(f"check_machine: {len(r.findings)} finding(s)"
              + ("" if a.strict else "; advisory, exit 0. Run --strict to block on these"))
        return 1 if a.strict else 0
    print("check_machine: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
