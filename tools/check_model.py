#!/usr/bin/env python3
"""Model-tier gate: whether commands actually run on the tier lessons.md requires. Stdlib only.

`tasks/lessons.md` ("Match model cost to task shape", 2026-08-30) said mechanical work should
run on a fast model and judgment/orchestration/adversarial-review/final-acceptance work should
stay on the strongest available reasoning path. It was prose only until Ron reported that a
stock search (`run deepdive`/`run redteam`) ran on an unnecessarily expensive model -- the
second failure of the same rule, which by this repo's own two-failure criterion
(`.claude/agents/adam-gm.md`, `tasks/lessons.md`'s own header) means it is now enforced, not
remembered.

`tools/model_tiers.py` is the single source of truth for which tier each governed command
requires. This gate checks that the rest of the machine actually agrees with it, loaded from
the AUDITED root rather than this script's own directory, so a fixture root can supply its own
table:

  1. coverage    -- every agent-owned command in CLAUDE.md's table has a tier assignment
  2. frontmatter -- an agent contract that pins a static `model:` field agrees with the tier
                     of every command that agent owns
  3. ledger      -- of the governed commands run in the last N days, how many ledger lines
                     name the model they ran on, and how many of those match the required tier

ADVISORY BY DEFAULT, same posture and reason as `tools/check_machine.py`: zero ledger lines
carry `model:` on day one, and a gate that fails on its first run gets routed around. Run with
--strict to make findings blocking.

Run: python3 tools/check_model.py [--strict] [--root PATH] [--days N] [--json]
Exit 0 clean (or advisory), 1 on findings under --strict.
"""
import argparse
import datetime
import importlib.util
import json
import re
from pathlib import Path

from agent_registry import command_rows, command_key, given_name  # noqa: E402

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
LEDGER_MODEL_RE = re.compile(r"\|\s*model:\s*([A-Za-z0-9_.\-]+)")

SILENT_WINDOW_DAYS = 21


class Report:
    def __init__(self):
        self.lines, self.findings = [], []

    def say(self, msg):
        self.lines.append(msg)

    def find(self, msg):
        self.findings.append(msg)


def read(path: Path, default: str = "") -> str:
    try:
        return path.read_text()
    except Exception:  # noqa: BLE001
        return default


def _frontmatter(text: str) -> dict:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def load_tiers(root: Path):
    """Load the AUDITED root's own tools/model_tiers.py, not this script's own copy.

    Every other check in this file reads the root's own state, not this process's environment;
    the tier table is no different, and it is the only way a test fixture can exercise this
    gate without depending on the live repo's real command table.
    """
    path = root / "tools" / "model_tiers.py"
    spec = importlib.util.spec_from_file_location("model_tiers_under_audit", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def audit_coverage(root: Path, r: Report, tiers):
    """Every governed command names a tier, or it silently inherits whatever model the calling
    session happens to be running -- which is the exact failure this gate exists to close."""
    rows = command_rows(read(root / "CLAUDE.md"))
    governed = [(cmd, agent) for cmd, agent, _v in rows if agent]
    if not governed:
        r.find("CLAUDE.md command table named 0 agent-owned commands: the audit's own scope "
               "is empty, which is a broken parser and not a clean machine")
        return
    missing = []
    for cmd, _agent in governed:
        key = command_key(cmd)
        if key not in tiers.COMMAND_TIER:
            missing.append((cmd, key))
    for cmd, key in missing:
        r.find(f"command `{cmd}` (key `{key}`) names no tier in tools/model_tiers.py: it "
               f"silently inherits whatever model the calling session happens to run under")
    r.say(f"coverage: {len(governed) - len(missing)} of {len(governed)} agent-owned "
          f"command(s) carry a tier in tools/model_tiers.py")


def audit_frontmatter(root: Path, r: Report, tiers):
    """An agent contract that pins a static model must agree with every command it owns."""
    rows = command_rows(read(root / "CLAUDE.md"))
    checked, mismatched = 0, []
    for path in sorted((root / ".claude" / "agents").glob("*.md")):
        fm = _frontmatter(read(path))
        pinned = fm.get("model")
        if not pinned:
            continue
        checked += 1
        name = given_name(path.stem)
        owned_keys = {command_key(cmd) for cmd, agent, _v in rows if agent == name}
        required = {tiers.TIER_MODEL[tiers.COMMAND_TIER[k]]
                    for k in owned_keys if k in tiers.COMMAND_TIER}
        if required and required != {pinned}:
            mismatched.append((path.name, pinned, sorted(required)))
    for name, pinned, required in mismatched:
        r.find(f".claude/agents/{name} pins model: {pinned}, but its owned command(s) "
               f"require {required}: a static pin cannot serve mismatched tiers")
    r.say(f"frontmatter: {checked - len(mismatched)} of {checked} pinned agent(s) agree with "
          f"every command they own" + ("  <- no agent pins a model yet" if not checked else ""))


def audit_ledger(root: Path, r: Report, today: datetime.date, days: int, tiers):
    """Governed commands actually ran on the model tools/model_tiers.py requires -- the only
    check in this file that looks at what happened rather than what is declared."""
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
            recent.append(line)

    governed_keys = sorted(tiers.COMMAND_TIER, key=len, reverse=True)
    total_governed_lines, named, matched = 0, 0, 0
    for line in recent:
        fields = [f.strip() for f in line.split(" | ")]
        if len(fields) < 3:
            continue
        cmd_field = fields[2]
        key = next((k for k in governed_keys
                    if cmd_field == k or cmd_field.startswith(k + " ")), None)
        if key is None:
            continue
        total_governed_lines += 1
        required = tiers.TIER_MODEL[tiers.COMMAND_TIER[key]]
        m = LEDGER_MODEL_RE.search(line)
        if not m:
            r.find(f"ledger line for `{key}` names no model, tier requires {required}: "
                   f"{line.strip()[:100]}")
            continue
        named += 1
        if m.group(1) == required:
            matched += 1
        else:
            r.find(f"ledger line for `{key}` ran on {m.group(1)}, tier requires {required}: "
                   f"{line.strip()[:100]}")
    r.say(f"ledger: {named} of {total_governed_lines} governed ledger line(s) in the last "
          f"{days} days name a model, {matched} of those match the required tier"
          + ("  <- NO governed ledger lines in the window at all, so this audit passed over "
             "nothing" if not total_governed_lines else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--days", type=int, default=SILENT_WINDOW_DAYS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    today = datetime.datetime.now(datetime.timezone.utc).date()

    REQUIRED = ("CLAUDE.md", "tools/model_tiers.py", ".claude/agents", "data/ledger.md")
    missing = [p for p in REQUIRED if not (root / p).exists()]
    if missing:
        print(f"check_model: SCOPE EMPTY at {root}. Missing: {', '.join(missing)}. "
              f"This is not a pass; the audit could not see the machine.")
        return 1 if a.strict else 0

    tiers = load_tiers(root)
    r = Report()
    audit_coverage(root, r, tiers)
    audit_frontmatter(root, r, tiers)
    audit_ledger(root, r, today, a.days, tiers)

    if a.json:
        print(json.dumps({"as_of": today.isoformat(), "examined": r.lines,
                          "findings": r.findings}, indent=2))
        return 1 if (a.strict and r.findings) else 0

    print(f"check_model: {today}  (advisory)" if not a.strict
          else f"check_model: {today}  (strict)")
    for ln in r.lines:
        print(f"  {ln}")
    for f_ in r.findings:
        print(f"  FINDING  {f_}")
    if r.findings:
        print(f"check_model: {len(r.findings)} finding(s)"
              + ("" if a.strict else "; advisory, exit 0. Run --strict to block on these"))
        return 1 if a.strict else 0
    print("check_model: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
