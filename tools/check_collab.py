#!/usr/bin/env python3
"""Two people on one repo: who did what, what changed since you looked, and what is out of lane.

Stdlib only, offline, reads git.

Upstream has two users. Attribution today is one free-text `by:` field that nothing validates,
and the collaborator value has been written zero times in 49 commits, so the silent default is
`ron`. This file is the machine-checkable half of the two-person protocol in `CLAUDE.md`.

THE HONEST LIMIT, stated here because a gate that oversells itself is worse than none:
identity inside the repo is DECLARED, not proven. A session that says "I'm Ron" writes `ron`.
The one unforgeable signal is `github.actor` on the push, the OAuth identity of the credential
that pushed, which no session holds. So the `Actor:` commit trailer is the declaration, the
pusher is the proof, and CI (`--range ... --pusher ...`) is the only place the two ever meet.
Branch protection is not available on this repo's plan, so nothing here refuses a push. It
detects, loudly, in a place a session cannot switch off from the inside.

The identity check is deliberately ASYMMETRIC. Only impersonation is a violation:
`Actor: ron` pushed by a non-Ron account is flagged; `Actor: yotam` pushed by Ronkimhi is
fine, because Ron legitimately pushes Yotam's work through the drain/rebase protocol.

Modes:
  --brief [--since 3d]           what changed since you last committed. Stateless: no cursor,
                                 no state file, writes nothing.
  --range A..B --pusher LOGIN    CI mode: reconcile declaration against the pushing account.
  --staged                       postlude mode: check what is about to be committed.
  --tiers                        print the ownership map.

Exit 0 clean, 1 on any failure.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # rebound by --root for fixture tests

# ---------------------------------------------------------------- the ownership map

# Ron only. The rules of the machine, and the checks that decide whether anything else is
# allowed. A change here is a constitutional change and needs Ron's ruling.
CONSTITUTION = (
    "CLAUDE.md",
    "README.md",
    "docs/*",
    "docs/**",
    "tools/*",
    "tools/**",
    ".claude/hooks/*",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/launch.json",
    ".github/*",
    ".github/**",
    "app/*",
    "app/**",
)

# Yotam's lane. Ships directly; every change is reviewed by the adversary and lands with a
# PROP file. This is the layer he is good at and the layer where being wrong is cheap and
# visible, because the next run's output shows it.
CRAFT = (".claude/agents/*.md",)

# Everything else under data/ is shared. The stage contracts in CLAUDE.md already govern it.
# `data/taste.md` and `data/chains/_archetypes.md` live here deliberately: they LOOK like
# rules but they are agent output. Nell writes taste.md on every radar run and Atlas writes
# _archetypes.md on every chain run, so putting them in CONSTITUTION made those commands
# impossible for anyone but ron. What governs them is their stage gate, not this map.
DATA_PREFIX = "data/"

# Per-clone local state, not machine rules, and gitignored. Named so that force-adding it
# cannot produce a baffling CONSTITUTION violation.
LOCAL_ONLY = (".claude/actor",)

ACTORS = ("ron", "yotam", "routine", "click")
RON = "ron"
RON_LOGIN = "Ronkimhi"
BOT_LOGINS = {"github-actions[bot]", "upstream-bot", "web-flow"}
BOT_EMAILS = {"actions@users.noreply.github.com"}

VERDICTS = ("ADOPT", "ADOPT_NARROWED", "REJECT")

_TRAILER = re.compile(r"^\s*Actor:\s*([A-Za-z0-9_.-]+)\s*$", re.M)

failures: list[str] = []
lines: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def report(msg: str) -> None:
    lines.append(msg)


def tier_of(path: str) -> str:
    """CRAFT, CONSTITUTION or DATA.

    An unrecognised top-level path is CONSTITUTION, not DATA: the protective default is the
    one that asks a human, and a new top-level file is exactly the case nobody anticipated.
    """
    p = path.replace("\\", "/")
    # NOT lstrip("./"): lstrip takes a character SET, so it eats the leading dot of
    # `.claude/...` and every agent contract silently reclassifies to CONSTITUTION.
    if p.startswith("./"):
        p = p[2:]
    if p in LOCAL_ONLY:
        return "DATA"
    if any(fnmatch(p, g) for g in CRAFT):
        return "CRAFT"
    if any(fnmatch(p, g) for g in CONSTITUTION):
        return "CONSTITUTION"
    if p.startswith(DATA_PREFIX):
        return "DATA"
    return "CONSTITUTION"


# ---------------------------------------------------------------- git plumbing


def git(*args: str) -> str:
    """Run git and return stdout. A failure is REPORTED, never swallowed.

    An earlier draft used NUL as the format separator, which execve rejects, and a bare
    `except: return ""` turned that into a silent empty brief. A tool that reports nothing
    and a tool that finds nothing must not look the same.
    """
    try:
        out = subprocess.run(
            ["git", "--no-pager", *args], cwd=ROOT, capture_output=True, text=True, timeout=30
        )
    except Exception as exc:  # noqa: BLE001
        fail(f"git {' '.join(args[:3])} could not run: {exc}")
        return ""
    if out.returncode != 0:
        fail(f"git {' '.join(args[:3])} exited {out.returncode}: {out.stderr.strip()[:160]}")
        return ""
    return out.stdout


def declared_actor(cli: str | None) -> str | None:
    """Who does this session say it is? CLI flag, then env, then `.claude/actor` (gitignored).

    Absent is UNDECLARED and reported as such. It is never silently resolved to `ron` -- that
    silent default is the exact defect this file exists to close.
    """
    import os

    if cli:
        return cli.strip().lower()
    env = os.environ.get("UPSTREAM_ACTOR", "").strip().lower()
    if env:
        return env
    try:
        return (ROOT / ".claude" / "actor").read_text().strip().lower() or None
    except Exception:  # noqa: BLE001
        return None


def commits_in(*revargs: str) -> list[dict]:
    """[{sha, actor, author, email, subject, files[]}] oldest first.

    Separators are US/RS, not NUL: a NUL cannot survive argv.
    """
    sep, rec = "\x1f", "\x1e"
    raw = git("log", "--reverse", f"--format={sep}%H{sep}%an{sep}%ae{sep}%s{sep}%B{rec}", *revargs)
    out: list[dict] = []
    for chunk in raw.split(rec):
        parts = chunk.split(sep)
        if len(parts) < 6:
            continue
        _, sha, author, email, subject, body = parts[:6]
        sha = sha.strip()
        if not sha:
            continue
        m = _TRAILER.search(body)
        out.append({
            "sha": sha,
            "actor": (m.group(1).lower() if m else None),
            "author": author,
            "email": email,
            "subject": subject,
            "files": [f for f in git("show", "--name-only", "--format=", sha).splitlines() if f],
        })
    return out


def ledger_result(sha: str) -> str | None:
    """The `result:` field of the ledger line this commit added, as the human summary."""
    diff = git("show", sha, "--", "data/ledger.md")
    for ln in diff.splitlines():
        if not ln.startswith("+") or ln.startswith("+++"):
            continue
        m = re.search(r"\|\s*result:\s*(.+?)\s*\|\s*health:", ln)
        if m:
            return m.group(1)
        m = re.search(r"\|\s*result:\s*(.+)$", ln)
        if m:
            return m.group(1)
    return None


def is_bot(c: dict) -> bool:
    return c["author"] in BOT_LOGINS or c["email"] in BOT_EMAILS


# ---------------------------------------------------------------- proposals


def proposals() -> list[dict]:
    d = ROOT / "data" / "proposals"
    out = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("PROP-*.json")):
        try:
            obj = json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            fail(f"{p.relative_to(ROOT)} is not readable JSON")
            continue
        obj["_path"] = str(p.relative_to(ROOT))
        out.append(obj)
    return out


def prop_covers(props: list[dict], path: str) -> dict | None:
    """The NEWEST review covering this path.

    `proposals()` is sorted oldest-first, so returning the first match let a stale ADOPT mask
    a later REJECT, and made the Stop hook block the moment a second PROP named an
    already-reviewed file.
    """
    hits = [pr for pr in props
            if pr.get("verdict") in VERDICTS and path in (pr.get("files") or [])]
    if not hits:
        return None
    return max(hits, key=lambda pr: (str(pr.get("as_of") or ""), str(pr.get("id") or "")))


# ---------------------------------------------------------------- the checks


def check_changes(files: list[str], actor: str | None, where: str, props: list[dict]) -> None:
    """Tier rules over a set of changed paths, for one declared actor."""
    by_tier: dict[str, list[str]] = {"CONSTITUTION": [], "CRAFT": [], "DATA": []}
    for f_ in files:
        by_tier[tier_of(f_)].append(f_)

    if by_tier["CONSTITUTION"]:
        if actor is None:
            fail(f"{where}: {len(by_tier['CONSTITUTION'])} CONSTITUTION-tier path(s) changed by "
                 f"an UNDECLARED actor: {', '.join(by_tier['CONSTITUTION'][:6])}. Declare who "
                 f"you are: `echo ron > .claude/actor` (or yotam). The silent default used to "
                 f"be ron and that is the defect this closes.")
        elif actor != RON:
            fail(f"{where}: {len(by_tier['CONSTITUTION'])} CONSTITUTION-tier path(s) changed by "
                 f"`{actor}`: {', '.join(by_tier['CONSTITUTION'][:6])}. This tier is Ron's. "
                 f"Raise an ESCALATION ledger NOTE plus a data/proposals/PROP-*.json and let "
                 f"Ron rule.")

    for f_ in by_tier["CRAFT"]:
        if not prop_covers(props, f_):
            fail(f"{where}: {f_} changed with no data/proposals/PROP-*.json naming it and "
                 f"carrying a verdict. Every agent-contract change is reviewed in fresh "
                 f"context before it lands (`run devil <ref>`).")

    report(f"{where}: {len(files)} path(s) changed -- "
           f"{len(by_tier['CONSTITUTION'])} CONSTITUTION, {len(by_tier['CRAFT'])} CRAFT, "
           f"{len(by_tier['DATA'])} DATA; actor {actor or 'UNDECLARED'}")


def mode_range(rev_range: str, pusher: str | None) -> None:
    commits = commits_in(rev_range)
    if not commits:
        report(f"range {rev_range}: no commits (nothing to reconcile)")
        return
    props = proposals()
    bots = [c for c in commits if is_bot(c)]
    human = [c for c in commits if not is_bot(c)]
    report(f"range {rev_range}: {len(commits)} commit(s), {len(bots)} bot (exempt), "
           f"{len(human)} human; pusher {pusher or 'UNKNOWN'}")

    untrailered = [c for c in human if not c["actor"]]
    if untrailered:
        fail(f"{len(untrailered)} of {len(human)} human commit(s) carry no `Actor:` trailer: "
             f"{', '.join(c['sha'][:8] for c in untrailered[:6])}. The trailer is CI's only "
             f"per-commit comparand against the pushing account.")

    bad_actor = [c for c in human if c["actor"] and c["actor"] not in ACTORS]
    if bad_actor:
        fail(f"{len(bad_actor)} commit(s) declare an unknown actor: "
             f"{', '.join(sorted({c['actor'] for c in bad_actor}))}. Known: {', '.join(ACTORS)}")

    # The asymmetric identity rule: only impersonation of Ron is a violation.
    if pusher and pusher not in BOT_LOGINS and pusher != RON_LOGIN:
        impostors = [c for c in human if c["actor"] == RON]
        if impostors:
            fail(f"{len(impostors)} commit(s) declare `Actor: ron` but were pushed by "
                 f"`{pusher}`: {', '.join(c['sha'][:8] for c in impostors)}. A declaration is "
                 f"not proof; the pushing account is. This is the only identity direction "
                 f"checked -- `Actor: yotam` pushed by {RON_LOGIN} is expected and fine.")

    for c in human:
        check_changes(c["files"], c["actor"], f"{c['sha'][:8]} ({c['subject'][:48]})", props)


def mode_staged(actor: str | None) -> None:
    files = [f_ for f_ in git("diff", "--cached", "--name-only").splitlines() if f_]
    if not files:
        report("staged: nothing staged")
        return
    check_changes(files, actor, "staged", proposals())


def _default_since(actor: str | None, fallback: str) -> tuple[str, str]:
    """(rev-range-or-since-arg, human label). Stateless: derived from git, never from a cursor.

    A per-actor cursor file would be written at t=0 by every session, on a shared working tree
    where three sessions run concurrently and `git add -A` has already eaten one session's
    work. It would also lie: three `ron` sessions each advancing one cursor means session B
    marks as seen what session A never read. So the window comes from git itself.
    """
    if actor:
        for c in reversed(commits_in("--max-count=400", "HEAD")):
            if c["actor"] == actor:
                return (f"{c['sha']}..HEAD", f"since your last commit ({c['sha'][:8]})")
    return (f"--since={fallback}", f"last {fallback} (no commit of yours to measure from)")


def mode_brief(actor: str | None, since: str) -> None:
    rng, label = _default_since(actor, since)
    revargs = (rng,) if ".." in rng else (rng, "HEAD")
    commits = commits_in(*revargs)

    print("=" * 72)
    print(f"UPSTREAM BRIEF -- you are `{actor or 'UNDECLARED'}`, showing {label}")
    if not actor:
        print("  Nobody declared who this session is. Run `echo ron > .claude/actor`")
        print("  (or yotam). Until then every write is attributed by guesswork.")
    print("=" * 72)

    if not commits:
        print("  Nothing new.")
    others = [c for c in commits if c["actor"] and c["actor"] != actor]
    mine = [c for c in commits if c not in others]

    for group, title in ((others, "SOMEONE ELSE"), (mine, "YOU / UNATTRIBUTED")):
        if not group:
            continue
        print(f"\n{title}: {len(group)} commit(s)")
        for c in group:
            tiers = sorted({tier_of(f_) for f_ in c["files"]})
            flag = "  <-- CONSTITUTION" if "CONSTITUTION" in tiers else ""
            print(f"  [{c['actor'] or 'no-trailer'}] {c['sha'][:8]} {c['subject'][:60]}{flag}")
            print(f"      tiers: {', '.join(tiers) or 'none'}  ({len(c['files'])} file(s))")
            res = ledger_result(c["sha"])
            if res:
                print(f"      ledger: {res[:200]}{'...' if len(res) > 200 else ''}")

    open_props = [p for p in proposals() if not p.get("ruling")]
    print(f"\nPROPOSALS AWAITING A RULING: {len(open_props)}")
    for p in open_props:
        print(f"  {p.get('id', '?')} [{p.get('verdict', 'NO VERDICT')}] by {p.get('actor', '?')} "
              f"-- {', '.join(p.get('files') or [])}")
        obj = (p.get("surviving_objection") or "").strip()
        if obj:
            print(f"      objection: {obj[:200]}{'...' if len(obj) > 200 else ''}")
    print("=" * 72)


def mode_tiers() -> None:
    print("Upstream ownership map (tools/check_collab.py is the single source)\n")
    print("CRAFT        yotam's lane, ships directly, adversary-reviewed, needs a PROP file")
    for g in CRAFT:
        print(f"             {g}")
    print("\nCONSTITUTION ron only; yotam raises an ESCALATION ledger NOTE + a PROP file")
    for g in CONSTITUTION:
        if not g.endswith("/**"):
            print(f"             {g}")
    print("             (an unrecognised top-level path defaults here, not to DATA)")
    print(f"\nDATA         both write freely; the stage contracts govern it\n             {DATA_PREFIX}**")
    print(f"\nactors: {', '.join(ACTORS)}")
    print("identity is declared (`Actor:` trailer, `.claude/actor`); only the push is proven")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--tiers", action="store_true")
    ap.add_argument("--range", dest="rev_range")
    ap.add_argument("--pusher")
    ap.add_argument("--actor")
    ap.add_argument("--since", default="3d")
    ap.add_argument("--root", help="repo root; for fixture tests")
    args = ap.parse_args()

    if args.root:
        global ROOT
        ROOT = Path(args.root).resolve()

    actor = declared_actor(args.actor)

    if args.tiers:
        mode_tiers()
        return 0
    if args.brief:
        mode_brief(actor, args.since)
        if failures:
            print("\nBRIEF COULD NOT READ EVERYTHING:")
            for f_ in failures:
                print(f"  FAIL  {f_}")
            print(f"check_collab: brief INCOMPLETE, {len(failures)} finding(s)")
            return 1
        return 0

    today = datetime.date.today().isoformat()
    if args.rev_range:
        mode_range(args.rev_range, args.pusher)
    elif args.staged:
        mode_staged(actor)
    else:
        ap.error("one of --brief, --staged, --tiers or --range is required")

    print(f"check_collab: {today}")
    for ln in lines:
        print(f"  {ln}")
    if failures:
        for f_ in failures:
            print(f"  FAIL  {f_}")
        print(f"check_collab: FAILED with {len(failures)} finding(s)")
        return 1
    print("check_collab: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
