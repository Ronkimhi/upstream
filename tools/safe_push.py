#!/usr/bin/env python3
"""The push queue. Stdlib only.

Ron, 2026-09-01: "we have many collisions... maybe we need to create some sort of queue
to push." This is that queue, in the smallest form that actually removes the collisions:

  1. SERIALIZE. Every session on this machine shares one worktree, and today two of them
     ran `git pull --rebase` at the same moment: FETCH_HEAD picked up three duplicate
     branch entries and git refused with "Cannot rebase onto multiple branches"; a third
     session's autostash orphaned a peer's stash. So the whole sync-and-push critical
     section takes an exclusive flock on `.git/upstream-push.lock`. A queue IS a lock
     with waiters: sessions line up instead of colliding.

  2. RESOLVE BY CODE. When the rebase does conflict (a cross-venue race with the Actions
     fetcher, which has its own clone and cannot share the lock), the per-file-class
     rules in tools/resolve_conflicts.py apply: ledger UNION, requests union-by-id,
     fetcher stores newest-fetched, index.html either-side-then-rebuild. A path with no
     coded rule stops the machine and waits for a human, exactly as before.

  3. BOUNDED RETRIES. Three rounds of pull-rebase-push, matching the fetch workflow.

Commit first, then call this instead of hand-rolling `git pull --rebase && git push`:

  python3 tools/safe_push.py [--root PATH] [--lock-timeout SECONDS]

Exit 0: pushed. Exit 1: not pushed, with the reason printed (lock timeout, refused
conflict, or three failed rounds). It never forces, never deletes, never resets.
"""
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from resolve_conflicts import resolve_round  # noqa: E402

ROUNDS = 3
DEFAULT_LOCK_TIMEOUT = 600


def sh(root, *args):
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True,
                          env={**os.environ, "GIT_EDITOR": "true"})


def in_rebase(root):
    return (root / ".git" / "rebase-merge").exists() or \
           (root / ".git" / "rebase-apply").exists()


def rebuild_page(root) -> bool:
    b = subprocess.run([sys.executable, str(root / "app" / "build.py")],
                       capture_output=True, text=True)
    if b.returncode != 0:
        # A peer's half-finished data can make validate refuse. The push must not die
        # for it: origin's page was taken during resolution and stays canonical until
        # the next clean rebuild. Say so instead of failing the queue.
        tail = (b.stdout or b.stderr).strip().splitlines()
        print("safe_push: rebuild refused (" + (tail[-1] if tail else "no output")
              + "); keeping the resolved page, next clean build supersedes it")
        return False
    sh(root, "add", "app/index.html")
    r = sh(root, "commit", "--amend", "--no-edit")
    if r.returncode != 0:
        print("safe_push: could not fold the rebuilt page into the last commit; "
              "it will ride the next one")
    return True


def sync_and_push(root: Path) -> int:
    for attempt in range(1, ROUNDS + 1):
        if sh(root, "push").returncode == 0:
            print(f"safe_push: pushed on attempt {attempt}")
            return 0
        print(f"safe_push: push attempt {attempt} rejected, rebasing")
        pull = sh(root, "pull", "--rebase")
        needs_rebuild = False
        guard = 0
        while in_rebase(root):
            guard += 1
            if guard > 50:
                print("safe_push: 50 resolution rounds without finishing; stopping "
                      "for a human, rebase left in place")
                return 1
            _, refused, rebuild = resolve_round(root)
            needs_rebuild = needs_rebuild or rebuild
            if refused:
                for path, reason in refused:
                    print(f"REFUSED {path}: {reason}")
                print("safe_push: conflict with no coded rule; rebase left in place "
                      "for a human. Nothing was forced")
                return 1
            sh(root, "rebase", "--continue")
        if pull.returncode != 0 and not in_rebase(root) and \
                "Cannot rebase onto multiple branches" in (pull.stderr or ""):
            # Concurrent pulls corrupt FETCH_HEAD (seen 2026-09-01); one clean fetch
            # repairs it. Inside the lock this cannot recur from THIS machine.
            sh(root, "fetch", "origin", "main")
        if needs_rebuild:
            rebuild_page(root)
        time.sleep(attempt * 2)
    print(f"safe_push: {ROUNDS} rounds did not land the push; try again or read "
          f"`git status`")
    return 1


def main() -> int:
    argv = sys.argv[1:]
    root = Path(argv[argv.index("--root") + 1]).resolve() if "--root" in argv \
        else Path(__file__).resolve().parent.parent
    timeout = int(argv[argv.index("--lock-timeout") + 1]) if "--lock-timeout" in argv \
        else DEFAULT_LOCK_TIMEOUT

    if in_rebase(root):
        print("safe_push: a rebase is already in progress in this worktree; finish or "
              "abort it first. Refusing to stack a second one")
        return 1

    lock_path = root / ".git" / "upstream-push.lock"
    started = time.time()
    with open(lock_path, "w") as lk:
        while True:
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                waited = time.time() - started
                if waited > timeout:
                    print(f"safe_push: queue wait exceeded {timeout}s; another session "
                          f"holds the push lock. Not an error in the repo, try again")
                    return 1
                if int(waited) % 15 == 0:
                    print(f"safe_push: queued behind another session ({int(waited)}s)")
                time.sleep(1)
        waited = time.time() - started
        if waited > 2:
            print(f"safe_push: acquired the lock after {int(waited)}s in the queue")
        return sync_and_push(root)


if __name__ == "__main__":
    sys.exit(main())
