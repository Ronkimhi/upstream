# Methodology review: ron-brain v8, 2026-08-29

Written on the build of the cartographer stage, when Ron asked whether the "BCG professional"
agents in `ron-brain/the-system-v8-ron` should feed Upstream's chain builder. Recorded here so
it is never re-litigated from scratch, the same treatment `docs/sources.md` gives the two OSINT
aggregators it distilled and dropped.

## The finding: there are no BCG professionals

The string appears four times in the whole v8 tree: as a source tier for Outside Lens to read,
as a real line on a colleague's résumé, in an immigration exhibit, and as a meeting title. The
consulting-flavoured agent cluster (Management Advisor, Analyst, Chief of Staff, Devil's
Advocate, Sonic) is benched, and Management Advisor has never run.

Decisive for the question actually asked: **"profit pool" appears nowhere in that tree**, and
neither do cost curves, experience curves, advantage matrices, or capability maps. Porter's Five
Forces occurs twice, both times as a one-line name-drop with no procedure attached. Importing
"their capabilities" would have imported vocabulary.

## Adopted

The real asset is one level down: Ivy's `T-tools/01-skills/value-chain-map-skill/` plus
`A-agents/invest-playbook/decision-standards.md` §3.3, a fully specified chain-mapping method
that had **never been executed** (`B-brain/08-invest/chains/` did not exist, zero maps, a
267-byte agent log) while this repo held three live chains. Ron's call on 2026-08-29: Atlas
absorbs the method, Ivy's L1 lane is benched with a pointer here, and she keeps themes, universe,
screen, PCS and the funnel.

1. **Supplier-side EDGAR full-text search as the primary discovery motion**, with its reason:
   suppliers disclose their customers and their technology exposure, anchors publish no bill of
   materials. Anchor filings are read only for capex totals, guidance inflections, and the rare
   named supplier. Recorded in `docs/method.md` §4.
2. **The literal query sets**, which are theme-specific and already proven, now in
   `data/chains/_archetypes.md`. Note how exactly they fit: the physical-AI set is
   `"harmonic reducer"`, `"planetary roller screw"`, `"actuator" AND "humanoid"`, which is this
   repo's `humanoid-actuators` chain precisely.
3. **The citation bar, translated.** Ivy's rule is the sharpest sentence in either system: every
   edge carries its filing citation, and an article or a thread may propose a query but never be
   an edge. Upstream required evidence for heat scores and none at all for a link's existence or
   an edge. Adopted, but not in its filing-only form, which would delete any chain whose gating
   suppliers do not file with the SEC, the normal case for the physical chains this repo hunts.
   The translated rule is in `docs/method.md` §4: a dated cited source per link and per edge,
   VERIFIED for an SEC registrant's filing, INFERRED for dated research.
4. **Caps and stops** Upstream lacked: a freeze date paired with a refresh-due date, and
   per-query logging of query, forms, hits examined, and names extracted.
5. **The PCS-A scoring mechanic** as the template for any per-field points table: fixed max
   points per field, explicit band boundaries, a per-field NULL rule, renormalisation over
   *available* points, and an attempted-but-empty read earning its points while only a failed
   fetch is NULL.

## Recorded here, scored elsewhere

The best artefact in either system is not a framework, it is the COHR deep-dive
(`B-brain/08-invest/deep-dives/cohr-deep-dive-2026-08-28.md`), which reasons about where margin
actually sits along a chain better than any rubric in either tree. **Neither system has a
value-capture rubric at all.** Its four transferable tests, now the reason `capture_inputs`
exists in §4:

- Compare margin at the same chain stage. A fabless assembler out-earning the firm that owns the
  laser fab says owning the stack is not the margin advantage.
- Scarcity rent is not pricing power. Rent ends when supply arrives.
- Content per unit and unit growth are different things.
- One bill of materials wearing three tickers is a single bet, not diversification.

Atlas records these as cited facts at map time. The heat stage scores them. That seam is
deliberate: a rebuild that could rewrite scores is the hazard this whole stage is gated against.

## Refused, and why

- **BCG and McKinsey framework vocabulary.** Name-drops with no procedure attached anywhere.
- **MECE, issue trees, the Minto pyramid.** Deliverable-shaping for human readers. This repo's
  outputs are JSON with closed vocabularies and machine gates, a stricter discipline already
  enforced.
- **The seven-section decision brief and the options matrix.** `run deepdive` and `run redteam`
  already reach a closed verdict with entry zones and invalidation signs. A prose options memo
  would be a second, weaker verdict surface.
- **Porter's Five Forces as such.** The two legs that matter, supplier power and substitutes, are
  already the capture score and the `bottleneck` field.
- **The positioning 2x2 and "find the empty quadrant".** Already built: it is the heat map,
  impact against inverted crowdedness, with the money corner as the empty quadrant.

## Carried back to v8 (2026-08-30)

The reciprocal channel, used for the first time. Four practices went the other way, recorded in
`M-memory/learning-log.md` when the tree was reachable: advisory-first hardening for any audit
landing on an existing backlog; CI over Stop hooks for enforcement a session cannot switch off;
Rule 21 turned on the gates themselves (is there a test that has watched this gate FAIL);
and the rule that a heuristic audit must print matched and unmatched counts together, because an
unenforced-rule count cannot otherwise be told apart from a matcher that failed that many times.

## The reciprocal finding

The traffic is not one way. Upstream's three per-link scores are the thing v8 does not have:
every rubric in the invest playbook scores a ticker or a thesis, never a link, and there is no
moat, pricing-power, or value-capture rubric anywhere in that tree. Noted, not acted on.

## Adopted 2026-08-30: mission focus and the deferred-work backlog

Ron's problem: a session given a mission stops to fix every defect it trips over, so one mission
becomes ten and finishes none. The system's own doctrine caused it. In Ron's v8 tree the
`autonomous-bug-fix` skill fires "the moment a bug is found... end to end without checking in" and
treats a deferred phase as a skipped one. In this repo the same reflex showed up as ad-hoc "outside
this build's scope" notes buried in `data/ledger.md` result fields, with no id, status, or owner.

External survey, procedure adopted and vocabulary refused, the same standard as the BCG review
above. Nothing was installed; the pattern was copied.

- **Adopted, primary: the Ralph loop information architecture** (Anthropic's `ralph-wiggum` plugin,
  and the Geocodio file-convention write-up). Its shape is a frozen mission spec, a priority-ordered
  backlog file, and a rolling learnings log, driven by "read the backlog, do the single
  highest-priority open item, log, loop." This repo already runs a loop, so its bash-and-Stop-hook
  engine was refused and only the architecture was taken: the lazy funnel is the frozen mission,
  `tasks/backlog.md` is the backlog file, `tasks/lessons.md` and `data/ledger.md` are the rolling
  log.
- **Adopted, the in-cycle scope rule** from FerroxLabs/agents-md: a change that does not serve the
  current mission is reverted or, here, captured. Its "notice it, mention it in the summary"
  disposition was rewired to "append it to the backlog", because a summary line is not a tracked
  item.
- **Adopted, the bucketing unit** from aihero's triage skill: one entry per CONCEPT, not per issue.
  This is what makes Ron's fourth want work, ten findings with one root cause promote as one
  structural fix, not ten patches that collide.

- **Refused: the loop engine, the Stop hook, and `--max-iterations`.** This repo is not a
  fixed-prompt bash loop, it is many sessions under command contracts. The engine would duplicate
  what the funnel and the gates already do.
- **Refused: a new claude.ai routine for triage.** The Saturday `run digest` already fires and is
  evidenced LIVE, so grooming rides inside it. A third routine is one more thing that can silently
  stop firing.
- **Refused: a `tools/check_backlog.py` on day one.** Gates-not-promises: this ships as a rule, a
  file, and a triage duty, and hardens into a gate only when the discipline has failed twice.

Landed in: the `CLAUDE.md` section "Mission focus and the deferred-work backlog", `tasks/backlog.md`,
`.claude/agents/adam-gm.md` duty 6, and the additive `machine.deferred_backlog` block validated in
`tools/validate.py:v_digest`.

## Change protocol

Same as `docs/sources.md`: adopting or refusing a method edits this file in the same commit as
the code that implements it. A refusal recorded here is not re-evaluated from scratch.

**Owner, from 2026-08-30: Adam** (`.claude/agents/adam-gm.md`). This file was written without
one, which is why the reciprocal finding above sat recorded and unacted-on. Adam reads it before
any command that touches method, carries practice in both directions, and states in his ledger
line when the v8 tree was unreachable rather than reporting a clean port ledger over a tree he
never opened. He imports the procedure, never the vocabulary; the review above is the standard.
