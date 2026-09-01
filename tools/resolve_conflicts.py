#!/usr/bin/env python3
"""The repo's merge-conflict rules as code, one resolver for every venue. Stdlib only.

Every writer here (local sessions sharing one worktree, cloud sessions, the Actions
fetcher) pushes with `git pull --rebase` and then resolves conflicts from PROSE rules in
CLAUDE.md. Prose resolution has now failed twice in one day:

  * run 33457972947: the fetch workflow's recovery clause knew how to resolve exactly one
    path (app/index.html), so a peer's concurrent EDGAR push left conflict markers in ~29
    data/edgar/docs/*.json, validate counted 61 "unreadable JSON" errors, the build
    refused, and a successful 385-item feed batch was discarded with the run.
  * data/ledger.md conflicts were being union-merged BY HAND by each session (the rule:
    both sides record something that actually happened, never choose).

So the rules live here, per file class, side-agnostic. During a rebase git calls the
branch being rebased ONTO "ours" (stage :2:) and the commit being replayed "theirs"
(stage :3:); those names invert depending on venue, so nothing below trusts them. Every
rule is stated in terms of CONTENT:

  data/ledger.md              UNION of lines, both sides kept, never choose. (Normally
                              .gitattributes `merge=union` resolves this before we are
                              ever called; this is the belt to that suspender.)
  data/requests.json          union of rows by id; on the same id, a transitioned status
                              (FULFILLED/FAILED) beats PENDING, because Actions is the
                              only status-transitioner and its write is the later fact.
  data/market/*.json,
  data/edgar/docs|fts/*.json  newer `fetched_at` wins. Both sides are the same fetcher
                              writing the same public document.
  data/feeds/latest.json      newer `last_run.ts` (fallback `as_of`) wins.
  data/health/actions.json    per-key newest: fetch, feeds and smoke each taken from the
                              side whose `last_run` is later.
  data/health/sessions.json   last_commands unioned (later timestamp string wins a key);
                              routine_status LIVE beats REGISTERED.
  app/index.html              either side (stage :2: taken), then REBUILT from data/ by
                              the caller; the page is generated whole and is never merged.

Anything else conflicted: refuse, exit 1, name the path. A file this table does not know
is a file whose merge semantics nobody has decided, and guessing is how live data dies.

Usage:
  python3 tools/resolve_conflicts.py                    resolve what is conflicted, stage it
  python3 tools/resolve_conflicts.py --continue-rebase  loop: resolve, `git rebase --continue`,
                                                        until the rebase finishes
  --rebuild      after resolving, regenerate app/index.html (required in the fetch
                 workflow, where a stale page must never be pushed); exit 1 if the build
                 refuses
  --root PATH    repo root (default: this file's grandparent)
"""
import json
import subprocess
import sys
from pathlib import Path


def sh(root, *args, check=True):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r


def stage(root, spec, path):
    """Contents of one side of a conflicted path, or None when that side deleted it."""
    r = sh(root, "show", f":{spec}:{path}", check=False)
    return r.stdout if r.returncode == 0 else None


def jparse(text):
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None


def union_lines(a: str, b: str) -> str:
    """Ledger rule: every line from both sides, a's order first, b's new lines after."""
    a_lines = a.splitlines()
    seen = set(a_lines)
    out = a_lines + [ln for ln in b.splitlines() if ln not in seen]
    return "\n".join(out) + "\n"


def union_requests(a: str, b: str):
    da, db = jparse(a), jparse(b)
    if da is None or db is None:
        return None
    rows = {}
    order = []

    def rank(row):
        # A transitioned row is the later fact; among transitioned, more attempts is later.
        return (0 if row.get("status") == "PENDING" else 1, row.get("attempts") or 0)

    for src in (da.get("requests", []), db.get("requests", [])):
        for row in src:
            rid = row.get("id")
            if rid not in rows:
                rows[rid] = row
                order.append(rid)
            elif rank(row) > rank(rows[rid]):
                rows[rid] = row
    return json.dumps({"version": max(da.get("version", 1), db.get("version", 1)),
                       "requests": [rows[i] for i in order]}, indent=1) + "\n"


def newer_by(a: str, b: str, *keys):
    """Whole-file rule: the side whose nested timestamp is later. Ties/unparseable: a."""
    da, db = jparse(a), jparse(b)
    if da is None:
        return b
    if db is None:
        return a

    def get(d):
        for k in keys:
            cur = d
            ok = True
            for part in k.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
                if cur is None:
                    ok = False
                    break
            if ok and isinstance(cur, str):
                return cur
        return ""
    return b if get(db) > get(da) else a


def merge_actions_health(a: str, b: str):
    da, db = jparse(a), jparse(b)
    if da is None or db is None:
        return None
    out = {}
    for key in sorted(set(da) | set(db)):
        va, vb = da.get(key), db.get(key)
        if not isinstance(va, dict):
            out[key] = vb
        elif not isinstance(vb, dict):
            out[key] = va
        else:
            out[key] = vb if str(vb.get("last_run") or "") > str(va.get("last_run") or "") else va
    return json.dumps(out, indent=1) + "\n"


def merge_sessions_health(a: str, b: str):
    da, db = jparse(a), jparse(b)
    if da is None or db is None:
        return None
    out = dict(da)
    lc = dict(da.get("last_commands") or {})
    for k, v in (db.get("last_commands") or {}).items():
        if str(v) > str(lc.get(k) or ""):
            lc[k] = v
    out["last_commands"] = lc
    rs = dict(da.get("routine_status") or {})
    for k, v in (db.get("routine_status") or {}).items():
        if rs.get(k) != "LIVE":          # LIVE is sticky; REGISTERED never demotes it
            rs[k] = v
    out["routine_status"] = rs
    for k, v in db.items():
        if k not in ("last_commands", "routine_status") and k not in out:
            out[k] = v
    return json.dumps(out, indent=1) + "\n"


def resolve_one(root: Path, path: str):
    """(merged_content_or_None, needs_rebuild, reason). None content = refuse."""
    a, b = stage(root, 2, path), stage(root, 3, path)
    if a is None or b is None:
        return None, False, "one side deleted it; never delete-then-rebuild live data"
    if path == "data/ledger.md":
        return union_lines(a, b), False, "UNION, both sides kept"
    if path == "data/requests.json":
        m = union_requests(a, b)
        return m, False, "union of rows by id, transitioned status wins"
    if path.startswith(("data/market/", "data/edgar/docs/", "data/edgar/fts/")):
        return newer_by(a, b, "fetched_at"), False, "same fetcher, newer fetched_at wins"
    if path == "data/feeds/latest.json":
        return newer_by(a, b, "last_run.ts", "as_of"), False, "newer feed batch wins"
    if path == "data/health/actions.json":
        return merge_actions_health(a, b), False, "per-key newest last_run"
    if path == "data/health/sessions.json":
        return merge_sessions_health(a, b), False, "last_commands unioned, LIVE sticky"
    if path == "app/index.html":
        return a, True, "either side; the page is regenerated whole, never merged"
    return None, False, "no coded merge rule for this path; a human decides"


def resolve_round(root: Path):
    r = sh(root, "diff", "--name-only", "--diff-filter=U")
    conflicted = [p for p in r.stdout.splitlines() if p.strip()]
    needs_rebuild, refused = False, []
    for path in conflicted:
        content, rebuild, reason = resolve_one(root, path)
        if content is None:
            refused.append((path, reason))
            continue
        (root / path).write_text(content)
        sh(root, "add", path)
        needs_rebuild = needs_rebuild or rebuild
        print(f"resolved {path}: {reason}")
    return conflicted, refused, needs_rebuild


def main() -> int:
    argv = sys.argv[1:]
    root = Path(argv[argv.index("--root") + 1]).resolve() if "--root" in argv \
        else Path(__file__).resolve().parent.parent
    loop = "--continue-rebase" in argv
    rebuild_wanted = "--rebuild" in argv
    any_rebuild = False
    rounds = 0

    while True:
        conflicted, refused, needs_rebuild = resolve_round(root)
        any_rebuild = any_rebuild or needs_rebuild
        if refused:
            for path, reason in refused:
                print(f"REFUSED {path}: {reason}")
            print("resolve_conflicts: leaving the rebase in place for a human")
            return 1
        rounds += 1
        in_rebase = (root / ".git" / "rebase-merge").exists() or \
                    (root / ".git" / "rebase-apply").exists()
        if not (loop and in_rebase):
            break
        r = subprocess.run(["git", "-C", str(root), "rebase", "--continue"],
                           capture_output=True, text=True,
                           env={**__import__("os").environ, "GIT_EDITOR": "true"})
        if r.returncode == 0 and not ((root / ".git" / "rebase-merge").exists()
                                      or (root / ".git" / "rebase-apply").exists()):
            print(f"rebase finished after {rounds} resolution round(s)")
            break
        if rounds > 50:
            print("resolve_conflicts: 50 rounds without finishing; stopping for a human")
            return 1

    if any_rebuild and rebuild_wanted:
        b = subprocess.run([sys.executable, str(root / "app" / "build.py")],
                          capture_output=True, text=True)
        print(b.stdout.strip()[-400:] if b.stdout else "")
        if b.returncode != 0:
            print("resolve_conflicts: app/build.py refused; the page must not ship stale")
            return 1
        sh(root, "add", "app/index.html")
    elif any_rebuild:
        print("NOTE: app/index.html was taken from one side and needs a rebuild "
              "(caller's job; pass --rebuild to do it here)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
