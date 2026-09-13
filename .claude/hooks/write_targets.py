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

# Redirection. Two passes, because one regex cannot both find `cat > "CLAUDE.md"` and ignore
# `awk '$x > 5'`: the difference is quoting, not shape.
#   pass 1 finds a QUOTED target: `> "path"` / `>> 'path'`
#   pass 2 blanks every quoted region, then finds UNQUOTED targets in what is left
# The lookbehind kills `->` (a python type hint is not a redirect) and fd dups (`2>&1`).
_REDIRECT_QUOTED = re.compile(r"""(?<![-0-9&>])>{1,2}\|?\s*(["'])([^"'\n]+)\1""")
_REDIRECT_BARE = re.compile(r"""(?<![-0-9&>])>{1,2}\|?\s*(?![&(])([A-Za-z0-9_./$~{}-]+)""")
_QUOTED_REGION = re.compile('\'[^\'\\n]*\'|"[^"\\n]*"')

# python: Path("x").write_text(...) / Path('x').open('w') / open("x", "w")
_PY_WRITE_TEXT = re.compile(r"""Path\(\s*["']([^"']+)["']\s*\)\s*\.\s*(?:write_text|write_bytes|open)\b""")
_PY_OPEN = re.compile(r"""\bopen\(\s*["']([^"']+)["']\s*,\s*["'][wax]""")

_TEE = re.compile(r"""\btee\b((?:\s+-\S+)*)\s+((?:["']?[A-Za-z0-9_./$~{}-]+["']?\s*)+)""")
_TOUCH = re.compile(r"""\btouch\b((?:\s+-\S+)*)\s+((?:["']?[A-Za-z0-9_./$~{}-]+["']?\s*)+)""")
_DD_OF = re.compile(r"\bdd\b[^|;&]*\bof=([A-Za-z0-9_./$~{}-]+)")
_CP_MV = re.compile(r"\b(?:cp|mv|install|rsync)\b((?:\s+-\S+)*)\s+(\S+)\s+(\S+)")
_GIT_RESTORE = re.compile(r"\bgit\s+(?:checkout|restore)\b[^|;&]*?--\s+((?:[A-Za-z0-9_./$~{}-]+\s*)+)")

# In-place editors. `sed -i` we resolve; the rest we resolve where we can and REPORT where
# we cannot, because a silent miss in a write detector is the failure mode that matters.
_SED_INPLACE = re.compile(r"\bsed\b[^|;&]*?\s-i\b")
_PERL_INPLACE = re.compile(r"\bperl\b[^|;&]*?\s-\w*i\w*\b")

# Writers whose target this module does not resolve. Named, not swallowed: a caller prints
# these so the gate's blind spot is visible instead of looking like a clean pass.
_OPAQUE = (
    (re.compile(r"\bshutil\.(?:copy|copy2|copyfile|move)\b"), "shutil copy/move"),
    (re.compile(r"\bos\.(?:rename|replace|remove|unlink)\b"), "os.rename/replace/remove"),
    (re.compile(r"\bxargs\b[^|;&]*\b(?:tee|rm|mv|cp)\b"), "xargs into a writer"),
    (re.compile(r">\s*\$\{?\w"), "redirect into a shell variable"),
    (re.compile(r"\bopen\(\s*[A-Za-z_]"), "open() on a non-literal path"),
    (re.compile(r"Path\(\s*[A-Za-z_][^)]*\)\s*\.\s*write_"), "write_text on a computed path"),
    (re.compile(r"\bpatch\b\s+-"), "patch(1)"),
    (re.compile(r"\bgit\s+apply\b"), "git apply"),
    (re.compile(r"\b(?:ed|ex)\s+-s\b"), "ed/ex script"),
)

_NUMERIC = re.compile(r"^\d+$")


def _split_words(blob: str) -> list[str]:
    return [w for w in blob.split() if w]


def _inplace_targets(command: str, verb: str, rx) -> list[str]:
    """`sed -i` / `perl -i` — the operands after the script, minus flags.

    BSD needs `-i ''`, GNU takes `-i` bare or `-i.bak`. Rather than model both dialects, take
    every trailing token that survives flag-stripping, quote-stripping and the -e/-f options,
    then keep the ones that look like paths (contain a dot or a slash) and are not the script.
    """
    out: list[str] = []
    for segment in re.split(r"[|;&]+", command):
        if not rx.search(segment):
            continue
        toks = _split_words(segment)
        try:
            start = toks.index(verb)
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

    # pass 1: quoted redirect targets, read from the original text
    for m in _REDIRECT_QUOTED.finditer(command):
        targets.append(m.group(2))
    # pass 2: unquoted redirect targets, read from text with quoted regions blanked, so that
    # `echo "a -> b"` and `awk '$x > 5'` contribute nothing.
    blanked = _QUOTED_REGION.sub(lambda m: " " * len(m.group(0)), command)
    for m in _REDIRECT_BARE.finditer(blanked):
        targets.append(m.group(1))
    # the python forms are read from the ORIGINAL text: their paths are quoted by nature
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
    targets.extend(_inplace_targets(command, "sed", _SED_INPLACE))
    targets.extend(_inplace_targets(command, "perl", _PERL_INPLACE))

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
        if _NUMERIC.match(p):
            continue  # `[ $n > 3 ]` is a comparison, not a file called 3
        # A capture carrying quotes, parens or commas is a fragment of code or prose that
        # happens to name a path, not a path. Test harnesses in this repo are full of lines
        # like `pre("Edit",{"file_path":".claude/agents/nell-scanner.md"})`, and one of them
        # was captured whole and reported as a write.
        if any(ch in p for ch in '"\'(),;'):
            continue
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


def in_repo(target: str, root, subdir: str, pattern: str):
    r"""Return the matched filename only when `target` really lands in <root>/<subdir>/.

    The gates used to test `re.search(r"data/signals/[^/]+\.json$", target)`, which is
    unanchored: ANY path ending that way matched, wherever it lived. On 2026-08-30 a
    session wrote a throwaway probe card to
    `/private/tmp/.../scratchpad/gate/data/signals/SIG-20260901-01.json` while testing the
    evidence gate, and this hook read it as a real radar run and demanded a RADAR ledger
    line and a fresh scout calibration for a sweep that never happened.

    That is the worst failure a gate of this kind can have. Refusing to close is meant to
    stop a session forgetting to record work it DID; here it pressures a session into
    recording work it did NOT do, into an append-only ledger, and into regenerating a
    calibration over unchanged data so the scout log would claim a sweep occurred. A gate
    that manufactures evidence is worse than no gate.

    Relative paths resolve against the repo root (that is the session cwd); absolute paths
    must already be inside it. Anything outside is another tree and is not this gate's
    business.

    chain-gate.py and radar-gate.py each carried a private copy of this function, pasted in
    below their `__main__` guard, which is why both crashed on every chain or signal write
    until 2026-09-13 (harness findings 2026-W37-F1 and F2). It lives here once so a change to
    path anchoring cannot land in one gate and be forgotten in the other.
    """
    from pathlib import Path as _Path
    try:
        p = _Path(target)
        p = (root / p) if not p.is_absolute() else p
        p = p.resolve()
        rel = p.relative_to(_Path(root).resolve())      # raises if outside the repo
    except Exception:  # noqa: BLE001
        return None
    parts = rel.parts
    want = tuple(subdir.strip("/").split("/"))
    if parts[:len(want)] != want or len(parts) != len(want) + 1:
        return None
    return parts[-1] if re.fullmatch(pattern, parts[-1]) else None
