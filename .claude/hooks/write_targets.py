#!/usr/bin/env python3
"""Which files does a tool call actually WRITE?

Every gate in this repo needs the same answer and two of them got it wrong. `radar-gate.py`
and `chain-gate.py` asked "is the tool named Write/Edit/MultiEdit/NotebookEdit?", which is a
tool-name check wearing a path check's clothes. Measured across this project's transcripts:
Bash 413 calls, Edit 156, Write 59 — and 94 of those Bash calls carry a python heredoc that
writes a file. `CLAUDE.md` was rewritten that way three times, `tools/validate.py` five, and
at least one `data/signals/SIG-*.json`, which walked straight past the radar gate.

So the question is answered once, here, and the gates import it.

The other half of the contract matters just as much: this reports what a command WRITES, never
what it MENTIONS. The postlude's own line is

    echo '... | wrote: CLAUDE.md | ...' >> data/ledger.md

and a naive substring matcher blocks it. That command writes `data/ledger.md` and nothing else.
15 Bash calls in these transcripts mention CLAUDE.md; almost all of them are that echo.

Unparseable constructs are RETURNED, not swallowed (`unresolved`). A gate that cannot see
its own blind spot is worse than no gate, so callers print what could not be read.
"""
from __future__ import annotations

import re

STRUCTURED_WRITERS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# A redirection target. Excludes fd dups (`2>&1`, `>&2`) and process substitution (`>(cmd)`).
_REDIRECT = re.compile(r"(?<![0-9&>])>{1,2}\s*(?![&(])([A-Za-z0-9_./$~{}-]+)")

# python: Path("x").write_text(...) / Path('x').open('w') / open("x", "w")
_PY_WRITE_TEXT = re.compile(r"""Path\(\s*["']([^"']+)["']\s*\)\s*\.\s*(?:write_text|write_bytes|open)\b""")
_PY_OPEN = re.compile(r"""\bopen\(\s*["']([^"']+)["']\s*,\s*["'][wax]""")
# json.dump(obj, open("x","w")) is covered by _PY_OPEN; the Path(...).open("w") form by the above.

_TEE = re.compile(r"\btee\b((?:\s+-\S+)*)\s+((?:[A-Za-z0-9_./$~{}-]+\s*)+)")
_TOUCH = re.compile(r"\btouch\b((?:\s+-\S+)*)\s+((?:[A-Za-z0-9_./$~{}-]+\s*)+)")
_DD_OF = re.compile(r"\bdd\b[^|;&]*\bof=([A-Za-z0-9_./$~{}-]+)")
_CP_MV = re.compile(r"\b(?:cp|mv|install|rsync)\b((?:\s+-\S+)*)\s+(\S+)\s+(\S+)")
_GIT_RESTORE = re.compile(r"\bgit\s+(?:checkout|restore)\b[^|;&]*?--\s+((?:[A-Za-z0-9_./$~{}-]+\s*)+)")

# `sed -i` (GNU: -i / -i.bak) and BSD (-i '' / -i ""). Operands are the trailing paths.
_SED_INPLACE = re.compile(r"\bsed\b[^|;&]*?\s-i\b")

# Constructs that write but whose target we cannot resolve statically.
_OPAQUE = (
    (re.compile(r"\bshutil\.(?:copy|copy2|copyfile|move)\b"), "shutil copy/move"),
    (re.compile(r"\bos\.(?:rename|replace|remove|unlink)\b"), "os.rename/replace/remove"),
    (re.compile(r"\bxargs\b[^|;&]*\b(?:tee|rm|mv|cp)\b"), "xargs into a writer"),
    (re.compile(r">\s*\$\{?\w"), "redirect into a shell variable"),
    (re.compile(r"\bwrite_text\s*\(", ), "write_text on a non-literal path"),
)


def _split_words(blob: str) -> list[str]:
    return [w for w in blob.split() if w]


def _sed_targets(command: str) -> list[str]:
    """`sed -i [ext] EXPR... FILE...` — the operands after the script, minus flags.

    BSD needs `-i ''`, GNU takes `-i` bare or `-i.bak`. Rather than model both dialects, take
    every trailing token that survives flag-stripping, quote-stripping and the -e/-f options,
    then keep the ones that look like paths (contain a dot or a slash) and are not the script.
    """
    out: list[str] = []
    for segment in re.split(r"[|;&]+", command):
        if not _SED_INPLACE.search(segment):
            continue
        toks = _split_words(segment)
        try:
            start = toks.index("sed")
        except ValueError:
            continue
        rest = toks[start + 1:]
        skip_next = False
        seen_script = False
        for tok in rest:
            if skip_next:
                skip_next = False
                continue
            if tok in {"-e", "-f", "--expression", "--file"}:
                skip_next = True
                seen_script = True
                continue
            if tok.startswith("-"):
                continue
            stripped = tok.strip("'\"")
            if stripped == "":  # the BSD `-i ''` argument
                continue
            if not seen_script:
                seen_script = True  # first bare operand is the script
                continue
            out.append(stripped)
    return out


def from_bash(command: str) -> tuple[list[str], list[str]]:
    """(write targets, unresolved constructs) for one shell command."""
    targets: list[str] = []
    unresolved: list[str] = []

    for m in _REDIRECT.finditer(command):
        targets.append(m.group(1))
    for rx in (_PY_WRITE_TEXT, _PY_OPEN):
        targets.extend(m.group(1) for m in rx.finditer(command))
    for m in _DD_OF.finditer(command):
        targets.append(m.group(1))
    for rx in (_TEE, _TOUCH):
        for m in rx.finditer(command):
            targets.extend(_split_words(m.group(2)))
    for m in _GIT_RESTORE.finditer(command):
        targets.extend(_split_words(m.group(1)))
    for m in _CP_MV.finditer(command):
        targets.append(m.group(3))  # destination only
    targets.extend(_sed_targets(command))

    for rx, label in _OPAQUE:
        if rx.search(command):
            unresolved.append(label)

    return _clean(targets), sorted(set(unresolved))


def _clean(paths: list[str]) -> list[str]:
    out: list[str] = []
    for p in paths:
        p = p.strip().strip("'\"")
        if not p or p in {"-", "/dev/null", "/dev/stdout", "/dev/stderr"}:
            continue
        if p.startswith("./"):
            p = p[2:]
        if p not in out:
            out.append(p)
    return out


def from_tool_use(name: str, tool_input: dict) -> tuple[list[str], list[str]]:
    """(write targets, unresolved constructs) for one tool_use block.

    Reads are not writes: Read, Grep, Glob and every non-writing tool return ([], []).
    """
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if name in STRUCTURED_WRITERS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        return (_clean([path]) if path else [], [])
    if name == "Bash":
        return from_bash(str(tool_input.get("command") or ""))
    return ([], [])


def touches(targets: list[str], pattern: re.Pattern[str]) -> bool:
    """Does any write target match this path pattern? Absolute paths are matched tail-first."""
    return any(pattern.search(t) for t in targets)
