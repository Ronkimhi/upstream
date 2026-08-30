# Deferred-work backlog

**Owned and groomed by Adam** (`.claude/agents/adam-gm.md`), from 2026-08-30. The within-mission
companion to `tasks/lessons.md`: lessons records what broke and earned a gate, this records what a
mission tripped over and deferred rather than chased.

**Why this exists.** A session on a mission that stops to fix every defect it finds turns one
mission into ten and finishes none. The rule in `CLAUDE.md` ("Mission focus and the deferred-work
backlog") sends every incidental, non-blocking finding here instead. Here it gets an id (its line),
a bucket, and a status, so it is visible the day it is found rather than buried in a `result:` field,
and so `run digest` can triage it. This replaces the old habit of writing "outside this build's
scope" into ledger prose, where a finding had no status and nobody owned the follow-up.

**How a row is written.** One line, appended, never rewritten except to advance its `status`:

`YYYY-MM-DD | found-under: <command or mission> | finding: <one line, concrete> | bucket: <concept-tag> | status: OPEN | note: <optional>`

- `found-under` is the mission the session was actually on when it tripped over this, so the
  interruption it would have cost is legible.
- `bucket` is a concept tag, not a per-item label. Two findings with the same root cause carry the
  same bucket, because the promotion works on buckets, not items: one concept, not one issue.
- `status` only advances, a row is never deleted: `OPEN` (captured, untriaged) then `BUCKETED`
  (grouped, waiting) then `PROMOTED` (a fix or gate is being built) then `DONE` or `WONTFIX`.

**How it is groomed.** `run digest` reads this file, groups OPEN rows by concept and root cause, and
promotes a bucket to a real fix or a gate only when it has earned it: the two-failure criterion in
`tasks/lessons.md`. Ten findings that share one cause become one structural fix, which is why the
small fixes never collide, they were never made separately. The digest `machine.deferred_backlog`
block reports the open count, the buckets, and the oldest OPEN row's age every week, so the backlog
can only go down or be seen not going down (Rule 21). Nothing is promoted on first sighting.

**No gate yet, on purpose.** This ships as a rule, a file, and a triage step, not a
`tools/check_*.py`. Hardening into a `tools/check_backlog.py` (rows well-formed, statuses monotonic,
append-only against git HEAD) or a derived `tools/backlog_board.py` view (mirroring
`tools/campaign_board.py`: read-only, `--write` emits one JSON, `app/build.py` inlines it, SCOPE
EMPTY over nothing) is a later dated decision, earned the first time this discipline fails twice: a
capture that should have happened and did not, or a promoted bucket that regressed. Building the gate
before then is the premature-enforcement mistake `check_machine.py` exists to avoid.

## Backlog

2026-08-29 | found-under: build agent 3 of 3 (Stocky, analyst plane) | finding: edgar_fts certifies an empty result (verified_zero) using the stooq price control probe, so a genuinely empty full-text search cannot be told apart from a dead EDGAR path whenever stooq is up | bucket: cross-plane-zero-verification | status: OPEN | note: ledger 2026-08-29 20:50Z; the insider path had the same shape and was fixed that day, the class fix is that each plane verifies its OWN zero, never stooq
2026-08-29 | found-under: build agent 3 of 3 (Stocky, analyst plane) | finding: companyfacts certifies its zero with the same stooq price probe, the same defect as edgar_fts, deferred with it as outside that build's scope | bucket: cross-plane-zero-verification | status: OPEN | note: ledger 2026-08-29 20:50Z; same bucket as the edgar_fts row above, so the promotion is one fix for both planes, not two
2026-08-30 | found-under: build the mission-focus deferred-work backlog | finding: a fresh app/build.py page is 2.63MB against build.py's own 2.0MB warn threshold, so tools/tests/test_campaign_ui.py::test_scale_stays_inside_single_html_budget fails; the single-HTML page inlines the full campaign UI, cortex 3D and all accumulated data | bucket: page-size-budget | status: OPEN | note: pre-existing and non-blocking, already 2.59MB at HEAD before this build and it is a warn threshold, the page still works; the class decision is trim the inlined payload or raise the threshold, a separate mission, captured here per this repo's own new rule rather than chased inline
