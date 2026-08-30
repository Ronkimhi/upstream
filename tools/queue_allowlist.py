#!/usr/bin/env python3
"""The click-queue command allowlist, as code instead of prose. Stdlib only, offline.

A Run button on the shared artifact appends a command string to the published page, and a
session drains it. That string is the ONE place where input from outside this repo becomes
something the machine executes, so it is the one place an allowlist has to be exactly
right. CLAUDE.md described the permitted shapes in a table and every draining session
re-implemented them from that description.

Two ways that goes wrong, both silent:

  * A session writes `re.match(shape, cmd)` without anchoring the end. `re.match` anchors
    only the START, so `run radar && echo pwned` matches the `run radar` shape and the
    trailing text rides along into whatever the session does with it.
  * Two sessions read the prose slightly differently and drain different sets.

So the shapes live here, fully anchored with `fullmatch`, with the newline case handled
explicitly (`$` in Python matches before a trailing newline, which is not what "exact
shape" means when the string came from a web page).

    from queue_allowlist import is_allowed, reject_reason

`tools/tests/test_pressure.py`, `tools/tests/test_campaign_commands.py`,
`tools/tests/test_cass.py` and `tools/tests/test_impact.py` hold the injection probes. Adding a shape here without a probe is
how this file stops being trustworthy.

THE SECOND CHANNEL. The page also carries an `upstream-edits` block, because Ron edits agent
contracts from the artifact and a contract is 15 KB of free text. It could not ride the queue:
`is_allowed` refuses every control character, and that refusal is the whole security property
of this file, so widening a command shape to carry a body would have deleted it. `is_allowed_edit`
below checks the other shape instead, and its rules are different in kind. A command is a string
the machine EXECUTES, so the allowlist decides whether it may run at all. An edit is a file the
machine WRITES, and the draining session never obeys a word of it: the body is data under the
injection guard, exactly like feed text. So these checks are about integrity, not authority.
The target must already be an agent file on disk (this channel cannot create agents, and cannot
address anything outside `.claude/agents/`), the body must be a plausible contract rather than a
fragment, and it must not carry the one string that would break the page it travels on.
"""
import re
from pathlib import Path

# Fragments, named so the shapes below read as the command table in CLAUDE.md does.
_SIGNAL = r"SIG-\d{8}-\d{2}"
_CANDIDATE = r"CAND-\d{8}-\d{2}"
_SLUG = r"[a-z0-9-]{2,40}"
_TICKER = r"[A-Z0-9.\-]{1,10}"
_SCENARIO = r"S[1-6]"
_CAMPAIGN = r"CAMP-\d{8}-\d{2}"
_PATH = r"data/[a-z]+/[A-Za-z0-9._\-]+\.json"
_SAFE_SEGMENT = r"(?!\.{1,2}(?:/|$))[A-Za-z0-9._-]+"
_MACHINE_PATH = (
    rf"(?:CLAUDE\.md|README\.md|"
    rf"(?:\.claude|\.github|app|docs|tools)/(?:{_SAFE_SEGMENT}/)*{_SAFE_SEGMENT})"
)
_SHA = r"[0-9a-f]{7,40}"

SHAPES = (
    r"run radar",
    r"run digest",
    r"run themes",
    r"run campaign init",
    rf"run selection {_CAMPAIGN}",
    rf"run impact (?:{_SIGNAL}|{_CANDIDATE})",
    rf"run chain {_SIGNAL}",
    rf"run universe {_SLUG}",
    rf"run universe-audit {_SLUG}",
    rf"run heat {_SLUG}",
    rf"run scenarios {_SLUG}",
    rf"run screen {_SLUG} {_SCENARIO}",
    rf"run screen {_SLUG}",
    rf"run profile {_TICKER}",
    rf"run profile --campaign {_CAMPAIGN}",
    rf"run deepdive {_TICKER} {_SLUG}",
    rf"run redteam {_TICKER} {_SLUG}",
    rf"run devil (?:{_MACHINE_PATH}|{_SHA})",
    rf"refresh {_PATH}",
    rf"request data(?: {_TICKER})+",
)

_COMPILED = tuple(re.compile(s) for s in SHAPES)


def is_allowed(cmd) -> bool:
    """True only for a string that is EXACTLY one permitted command.

    fullmatch, not match: the difference is the whole security property. Any control
    character (newline, carriage return, NUL) disqualifies the string outright rather
    than being allowed to hide a second command behind the first.
    """
    if not isinstance(cmd, str) or not cmd:
        return False
    if cmd != cmd.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in cmd):
        return False
    return any(rx.fullmatch(cmd) for rx in _COMPILED)


def reject_reason(cmd) -> str:
    """Why a command was refused, for the ledger NOTE the protocol requires.

    The rejected string is always named in the note: a drop nobody can see is
    indistinguishable from a button that never worked.
    """
    if not isinstance(cmd, str) or not cmd:
        return "not a non-empty string"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in cmd):
        return "contains a control character (a second command can hide behind the first)"
    if cmd != cmd.strip():
        return "has leading or trailing whitespace"
    for rx in _COMPILED:
        if rx.match(cmd):
            return (f"starts like {rx.pattern!r} but has trailing text: "
                    f"{cmd[rx.match(cmd).end():]!r}")
    return "matches no permitted command shape"


if __name__ == "__main__":  # tiny CLI so a draining session can check by hand
    import sys
    for arg in sys.argv[1:]:
        print(f"{'ALLOW ' if is_allowed(arg) else 'REJECT'} {arg!r}"
              + ("" if is_allowed(arg) else f"  — {reject_reason(arg)}"))


# ---------------------------------------------------------------- the edits channel

EDIT_MAX_BYTES = 200_000
# A slug, not a path. No separators, no dots, no traversal: `..` cannot be spelled and
# neither can `nell-scanner.md` or `../../CLAUDE`.
_EDIT_TARGET = re.compile(r"[a-z][a-z0-9-]{0,63}")
# The only control characters a markdown file legitimately holds. A NUL or an escape
# sequence in a contract is not formatting, it is something hiding.
_EDIT_OK_CONTROLS = frozenset("\n\r\t")
_FRONTMATTER_NAME = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)


def _edit_declared_name(body: str):
    m = _FRONTMATTER_NAME.match(body)
    if not m:
        return None
    for line in m.group(1).splitlines():
        if line.startswith("name:"):
            return line.partition(":")[2].strip()
    return None


def edit_reject_reason(rec, root) -> str:
    """Why one edit record was refused, empty string when it is acceptable.

    Named in the ledger like a rejected command is, and for the same reason: an edit that
    vanishes silently is indistinguishable from a Save button that never worked.
    """
    if not isinstance(rec, dict):
        return "not an object"
    target, body = rec.get("target"), rec.get("body")
    if not isinstance(target, str) or not _EDIT_TARGET.fullmatch(target):
        return f"target {target!r} is not an agent slug (lowercase, digits and hyphens, no path)"
    path = Path(root) / ".claude" / "agents" / f"{target}.md"
    if not path.is_file():
        return (f"target {target!r} names no file at .claude/agents/{target}.md: this channel "
                "edits contracts that exist, it does not create agents")
    if not isinstance(body, str) or not body.strip():
        return "body is empty: an agent with no instructions is worse than an unedited one"
    size = len(body.encode())
    if size > EDIT_MAX_BYTES:
        return f"body is {size} bytes, over the {EDIT_MAX_BYTES}-byte cap"
    bad = sorted({ord(ch) for ch in body if (ord(ch) < 32 or ord(ch) == 127) and ch not in _EDIT_OK_CONTROLS})
    if bad:
        return f"body carries control character(s) {bad}, which no markdown file needs"
    if "</scr" + "ipt" in body.lower():
        return "body carries a literal script-close, which would terminate the block it travels in"
    declared = _edit_declared_name(body)
    if declared is None:
        return "body has no YAML frontmatter: every agent contract in this repo opens with one"
    if declared != target:
        return (f"body frontmatter declares name {declared!r} but the edit targets {target!r}: "
                "one of the two is wrong and writing either would be a guess")
    return ""


def is_allowed_edit(rec, root) -> bool:
    """True only for an edit record safe to write verbatim to .claude/agents/<target>.md.

    Safe to WRITE. Never safe to OBEY: the session that applies this writes the file and
    reads no instruction out of it.
    """
    return edit_reject_reason(rec, root) == ""
