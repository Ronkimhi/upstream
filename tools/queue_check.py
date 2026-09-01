#!/usr/bin/env python3
"""Can this artifact-republish notification be attributed to our own build? Stdlib only.

A republish notification carries a version id like `1788220232-3a5e`, whose first field is
the unix second the page was published. It does NOT say whether anything is in the queue,
so the only certain answer has been to fetch the whole 1.7 MB page and look. On 2026-08-31
six consecutive notifications were peer rebuilds and six full reads found six empty queues.

There is a sound local shortcut, and it rests on one fact: `app/build.py` writes
`{"v":1,"queue":[]}` and `{"v":1,"edits":[]}` into every page it assembles (they are
literals in `app/templates/shell.html`). So a publish made FROM A BUILD has empty blocks by
construction. A click, by contrast, is published by the page itself splicing into the block
it already had, and never comes from a build.

So the question "is there a click waiting" reduces to "did our own build produce these
bytes", which is answerable from the repo:

  1. the committed `app/index.html` carries a `built_at` stamped by the same build, and
  2. a `data/ledger.md` line records that republish at that minute.

Both true means the published bytes are the build's bytes and the blocks are empty. Either
false means read the page: an unattributed publish is exactly the shape a click has.

The check is deliberately CONSERVATIVE. It never says "empty", it says "attributed" or
"read the page", and the fallback is the thing that was always correct anyway. A minute of
slack is allowed between the build stamp and the publish because the two happen in that
order with a commit and a push between them.

Run: python3 tools/queue_check.py <version-id> [--root PATH] [--slack-seconds N]
Exit 0 attributed (the read may be skipped), 1 unattributed (read the live page).
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

BUILT_AT_RE = re.compile(r'"built_at":"([^"]+)"')
DEFAULT_SLACK = 240


def publish_time(version_id: str):
    head = str(version_id).split("-", 1)[0]
    if not head.isdigit():
        return None
    try:
        return datetime.datetime.fromtimestamp(int(head), datetime.timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def page_built_at(root: Path):
    """The `built_at` inside the committed page, as a UTC datetime, or None."""
    p = root / "app" / "index.html"
    try:
        m = BUILT_AT_RE.search(p.read_text(errors="replace"))
    except OSError:
        return None
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%MZ").replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def ledger_republish_times(root: Path) -> list:
    """Every minute at which our ledger says an artifact republish happened, as UTC times.

    Matched against the PUBLISH time within a window, not against the build stamp exactly.
    The ledger line and the build carry different minutes routinely: the protocol writes
    the line, commits, pushes, and republishes last, so a page built at 23:50 is announced
    by a line stamped 23:49 and published at 23:50:32. An exact match refused a republish
    that was plainly ours.
    """
    out = []
    try:
        text = (root / "data" / "ledger.md").read_text(errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        if "artifact: republished" not in line:
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2})Z", line)
        if not m:
            continue
        try:
            out.append(datetime.datetime.strptime(
                f"{m.group(1)} {m.group(2)}:{m.group(3)}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=datetime.timezone.utc))
        except ValueError:
            continue
    return out


def check(version_id: str, root: Path, slack: int):
    pub = publish_time(version_id)
    if pub is None:
        return False, f"version id {version_id!r} carries no unix timestamp; read the live page"
    built = page_built_at(root)
    if built is None:
        return False, "app/index.html has no readable built_at; read the live page"
    delta = (pub - built).total_seconds()
    stamp = built.strftime("%Y-%m-%d %H:%M")
    in_ledger = any(abs((pub - t).total_seconds()) <= slack
                    for t in ledger_republish_times(root))
    when = pub.strftime("%Y-%m-%d %H:%M:%SZ")
    if not (0 <= delta <= slack):
        return False, (f"published {when}, but the committed page was built {stamp}Z "
                       f"({int(delta)}s apart, outside the {slack}s window): the bytes on the "
                       f"artifact are not the bytes this repo built. Read the live page")
    if not in_ledger:
        return False, (f"published {when} and the committed page was built {stamp}Z, but no "
                       f"ledger line within {slack}s of the publish records a republish. An "
                       f"unattributed publish is the shape a click has. Read the live page. "
                       f"(If a line IS there, check its artifact: field: only the canonical "
                       f"'artifact: republished' counts. 'pending', 'republishing' and "
                       f"'republish after push' are intentions, and an append-only ledger "
                       f"never comes back to say whether an intention happened.)")
    return True, (f"ATTRIBUTED: published {when}, {int(delta)}s after this repo built "
                  f"app/index.html at {stamp}Z, and the ledger records that republish. "
                  f"app/build.py writes an empty queue and an empty edits block into every "
                  f"page it assembles, so both are empty by construction. The read may be "
                  f"skipped; say so rather than claiming the page was checked")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version_id")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--slack-seconds", type=int, default=DEFAULT_SLACK)
    a = ap.parse_args()
    ok, msg = check(a.version_id, Path(a.root), a.slack_seconds)
    print(("queue_check: " if ok else "queue_check: NOT ATTRIBUTED -- ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
