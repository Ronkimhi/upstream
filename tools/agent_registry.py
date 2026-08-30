#!/usr/bin/env python3
"""The agent-to-command map, parsed once and used by everything that needs it.

Two consumers had to agree about which agent owns which command: Adam's machine audit,
which reports a command naming no agent, and the page, which now shows Ron the contract
behind every Run button he presses. Two parsers of the same table agree on the day they
are written and nothing keeps them agreeing, which is the reason `docs/method.md`'s
scoring thresholds are SHIPPED to the UI in `D.method` rather than retyped in `app.js`
(see the comment at `app/build.py`). Same rule, applied to ownership.

So the regexes and the table walk live here. `tools/check_machine.py` imports them, and
`app/build.py` imports `agents_payload()` to inline the contracts into the page.

Nothing in this module reads the network and nothing writes. Contract bodies are read off
disk verbatim and are DATA: an agent file is an instruction to the agent that runs under
it, never to the process that displays it.
"""
import re
from pathlib import Path

from queue_allowlist import is_allowed

GATE_RE = re.compile(r"tools/(check_[a-z_]+)\.py")
# The command table's first column: `run heat <chain>` / `check health` in backticks.
COMMAND_RE = re.compile(r"^\|\s*`([a-z]+ [^`]*)`")
AGENT_RE = re.compile(r"agent:\s*\*\*([A-Z][a-z]+)\*\*")
# Frontmatter is exactly `name:` and `description:` on every contract in this repo.
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
# `run impact <SIG-id|CAND-id>` -> `run impact`. The key is the fixed head of the shape,
# which is what a concrete command like `run impact SIG-20260829-01` starts with.
_PARAM_HEAD_RE = re.compile(r"\s*[<`].*\Z")


def command_rows(claude_md: str):
    """(command, agent-or-None, verify-column-or-empty) for every command-table row.

    Parsed out of the table rather than kept as a hand-maintained list, so a command added
    tomorrow is audited tomorrow. That is the whole point: the last unowned command sat
    unowned for the repo's entire life because nothing was looking.
    """
    rows = []
    for line in claude_md.splitlines():
        m = COMMAND_RE.match(line)
        if not m:
            continue
        # Split on UNESCAPED pipes only. A cell like `<SIG-id\|CAND-id>` carries an escaped
        # pipe, and splitting on it shifted every later cell by one, which reported two
        # commands that DO name an agent as unowned. An audit's own false positives cost more
        # than most bugs: they train the reader to skim it.
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) < 4:
            continue
        agent = AGENT_RE.search(cells[0])
        rows.append((m.group(1).strip(), agent.group(1) if agent else None, cells[3]))
    return rows


def given_name(slug: str) -> str:
    """`nell-scanner` -> `Nell`.

    Derived from the SLUG, not from the description, because this is the same string
    `check_machine.audit_agents` searches the ledger for. If the page named an agent one way
    and the silence audit looked for another, one of them would be reporting about somebody
    who does not exist.
    """
    return slug.split("-")[0].capitalize()


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


def _role(description: str, slug: str) -> str:
    """The clause after the given name: `Nell, the Upstream scanner.` -> `the Upstream scanner`.

    Falls back to the slug's own tail rather than inventing one, because a description that
    stops following the house shape should read as unstyled, never as a role somebody made up.
    """
    head = description.split(".")[0]
    if "," in head:
        return head.split(",", 1)[1].strip()
    tail = slug.split("-")[1:]
    return " ".join(tail) if tail else ""


def command_key(cmd: str) -> str:
    """The fixed head of a command shape, used to resolve a concrete command to its owner.

    `run deepdive <TICKER> <chain>` -> `run deepdive`, and `run profile --campaign <CAMP-ID>`
    -> `run profile --campaign`, so the more specific shape can win a longest-prefix match.
    """
    return _PARAM_HEAD_RE.sub("", cmd).strip()


def agents(root: Path) -> list:
    """Every contract on disk, with the commands the constitution says it owns.

    An agent file with no matching table row still appears, carrying no commands. That is
    the honest rendering of an agent nothing routes work to, and it is exactly the state
    `check_machine.audit_agents` calls silent.
    """
    claude = ""
    cm = root / "CLAUDE.md"
    if cm.exists():
        claude = cm.read_text()
    rows = command_rows(claude)

    out = []
    for path in sorted((root / ".claude" / "agents").glob("*.md")):
        text = path.read_text()
        fm = _frontmatter(text)
        slug = fm.get("name") or path.stem
        name = given_name(path.stem)
        desc = fm.get("description", "")
        # `runnable` is decided by the click-queue allowlist itself, never by looking for
        # angle brackets here. A button that queues a string the allowlist will refuse is
        # indistinguishable, from the browser, from a button that does nothing.
        cmds = [
            {"cmd": cmd, "key": command_key(cmd), "verify": verify,
             "runnable": bool(is_allowed(cmd))}
            for cmd, agent, verify in rows if agent == name
        ]
        out.append({
            "slug": path.stem,
            "declared_name": slug,
            "name": name,
            "role": _role(desc, path.stem),
            "description": desc,
            "body": text,
            "bytes": len(text.encode()),
            "commands": cmds,
        })
    return out


def owners(root: Path) -> list:
    """[key, given_name, slug] for every owned command shape, longest key first.

    Longest first because `run universe-audit` and `run universe` share a head, and the page
    resolves a concrete command by the first key that matches on a word boundary.
    """
    by_name = {a["name"]: a["slug"] for a in agents(root)}
    claude = ""
    cm = root / "CLAUDE.md"
    if cm.exists():
        claude = cm.read_text()
    seen, out = set(), []
    for cmd, agent, _verify in command_rows(claude):
        if not agent or agent not in by_name:
            continue
        key = command_key(cmd)
        if key in seen:
            continue
        seen.add(key)
        out.append([key, agent, by_name[agent]])
    out.sort(key=lambda r: -len(r[0]))
    return out


def unowned(root: Path) -> list:
    """Command shapes the table leaves without an agent.

    Shipped to the page rather than hidden so a Run button with no contract behind it reads
    as a known gap and not as an oversight in the renderer. It is the same backlog
    `check_machine.audit_owners` reports.
    """
    claude = ""
    cm = root / "CLAUDE.md"
    if cm.exists():
        claude = cm.read_text()
    return [cmd for cmd, agent, _v in command_rows(claude) if not agent]


def agents_payload(root: Path) -> dict:
    """What `app/build.py` inlines. One shape, so the page never re-derives ownership."""
    return {"agents": agents(root), "owners": owners(root), "unowned": unowned(root)}


if __name__ == "__main__":  # tiny CLI so the map can be read by hand
    import json as _json
    import sys as _sys
    _root = Path(_sys.argv[1]) if len(_sys.argv) > 1 else Path(__file__).resolve().parent.parent
    _p = agents_payload(_root)
    for a in _p["agents"]:
        print(f"{a['name']:8} {a['slug']:26} {a['bytes']:>7} B  "
              + (", ".join(c["cmd"] for c in a["commands"]) or "(no command row)"))
    print(f"\nunowned rows: {', '.join(_p['unowned']) or 'none'}")
