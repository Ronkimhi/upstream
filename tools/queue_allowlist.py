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

`tools/tests/test_pressure.py` holds the injection probes. Adding a shape here without a
probe there is how this file stops being trustworthy.
"""
import re

# Fragments, named so the shapes below read as the command table in CLAUDE.md does.
_SIGNAL = r"SIG-\d{8}-\d{2}"
_SLUG = r"[a-z0-9-]{2,40}"
_TICKER = r"[A-Z0-9.\-]{1,10}"
_SCENARIO = r"S[1-6]"
_PATH = r"data/[a-z]+/[A-Za-z0-9._\-]+\.json"

SHAPES = (
    r"run radar",
    r"run digest",
    rf"run chain {_SIGNAL}",
    rf"run heat {_SLUG}",
    rf"run scenarios {_SLUG}",
    rf"run screen {_SLUG} {_SCENARIO}",
    rf"run screen {_SLUG}",
    rf"run deepdive {_TICKER} {_SLUG}",
    rf"run redteam {_TICKER} {_SLUG}",
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
    if cmd != cmd.strip() or any(ch in cmd for ch in "\n\r\t\x00"):
        return False
    return any(rx.fullmatch(cmd) for rx in _COMPILED)


def reject_reason(cmd) -> str:
    """Why a command was refused, for the ledger NOTE the protocol requires.

    The rejected string is always named in the note: a drop nobody can see is
    indistinguishable from a button that never worked.
    """
    if not isinstance(cmd, str) or not cmd:
        return "not a non-empty string"
    if any(ch in cmd for ch in "\n\r\t\x00"):
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
