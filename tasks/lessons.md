# Lessons

**Owned and consumed by Adam** (`.claude/agents/adam-gm.md`), from 2026-08-30. Before this the
file had four hard-won lessons in it and nothing in the repo read it, which is the same failure
it documents: a rule written down is a promise, and promises regress.

**The promotion criterion is two failures.** One failure is an incident and gets a fix. The same
rule failing twice, whether by two occurrences in `data/ledger.md` or by Ron correcting the same
thing twice, means the rule is depending on memory. It then gets deterministic enforcement, a
`tools/check_*.py`, a CI step, or a condition inside the script that produces the output, and
the two failures that earned it are named here beside the artifact. A rule that genuinely cannot
be enforced deterministically is recorded here saying so, with what would have to change to make
it enforceable.


## 2026-08-29: Separate adversarial review from peer ownership

- Cass is a fresh-context reviewer of changes to the research machine. Cass is not a peer identity, permission gate, ownership tier, commit actor, or collaboration arbiter.
- Restoring Cass must not restore `ron`/`yotam` actor declarations, CRAFT/CONSTITUTION tiers, `check_collab.py`, proposal-cover requirements, commit trailers, or PRIMARY/STANDBY queue claims.
- Before restoring a removed subsystem, inspect the removal commit and preserve the reason it was removed. Rebuild only the useful behavior with a smaller boundary.
- Concurrent commits are current repository state. Re-read status and recent history before editing so local work is reconciled with what another session already landed.

## 2026-08-30: The machine had no owner

- Every gate in this repo was born from the same event: a rule was prose, prose got broken, someone happened to notice, someone wrote a check. `check_screen.py` (the verbatim-quote rule method §1 stated twice and nothing enforced), `check_render.py` (four invented-number defects that had already shipped), the §3 verdict-band partition (three links rendered as opportunities on a gap in a table), the §0.2 impact-band partition (caught before shipping, by a test that asserted the partition).
- The promotion from promise to gate is the highest-leverage act in the system and it depended entirely on somebody noticing. `tools/check_machine.py` makes noticing a scheduled job, and Adam owns it.
- Day-one findings, recorded so the backlog can only go down or be seen not going down: 11 of 22 commands name no owning agent, including the core funnel stages `run heat`, `run scenarios`, `run screen` and `run review`; 10 of 22 state a verify list that no `tools/check_*.py` enforces; 3 of 10 gates have no test that has ever watched them refuse something.
- The audit is advisory on purpose. A gate that fails from its first run is a gate people route around, so it prints and exits 0 until the backlog is worked down, and hardening it to `--strict` is a dated ledger decision.
- Adam is a role, not an identity. He holds no veto, introduces no actor concept, and owns no tier map. His authority is in the gates he ships, which bind like every other one. See the 2026-08-29 lesson above for what happens when that line is crossed.

## 2026-08-30: Match model cost to task shape

- Use a faster, cost-effective model for repetitive issuer mapping, profile scaffolding, schema migration, and mechanical source checks when the contract and expected output are explicit.
- Keep campaign selection, prioritization, orchestration, semantic evidence review, Cass adversarial review, and final acceptance on the strongest available reasoning path.
- Cheap generation never lowers the gate. Every lower-cost output must pass the same deterministic checks and a strong-model semantic review before it counts.

## 2026-08-30: Never `git checkout --` a path that carries uncommitted work

- During the P0 gate-integrity pass an agent patched `campaign-gate.py` and `profile-gate.py`, saw four tests fail, and reverted with `git checkout -- <paths>`. Both files were ` M` in a shared working tree, so the revert discarded another session's uncommitted work, not just the agent's own edit. It was recovered byte-exactly from a sibling agent transcript and verified two ways, but only because a full dump of the file happened to exist.
- The rule: to undo your own edit, re-edit it. `git checkout --`, `git restore`, `git stash` and `git clean` all destroy work that was never committed, and in a repo where several sessions write concurrently you cannot assume the diff you are discarding is yours.
- This is the same shape as the 2026-08-29 lesson that concurrent commits are current repository state. Reading `git status` before editing is not enough; the check has to happen before *discarding* too.
- Not yet enforceable deterministically. A `PreToolUse` hook refusing destructive git verbs on dirty paths would close it, and that is the promotion if this happens a second time.

## 2026-08-30: A gate can be blind in a wider class than its own review found

- Cass's `REV-20260830-01` named six blind gates. Re-probing all six verbatim showed five were already closed. The sixth, gate falsifiability, refused Cass's literal probe but still certified coverage for `subprocess.run(['./check_alpha.py'])` and `subprocess.run([str('check_alpha.py')])` — the review found one instance of a class it did not fully map.
- Two further defects surfaced only because the probes were re-run rather than trusted: `ember-gate.py` raised `ValueError` on the ledger's own prose headers and had therefore never enforced anything, and `check_screen.py` demanded `opportunity_tier == "O2"` on a screen row while `run selection` writes `O1`, which made screening and selection mutually exclusive across the whole campaign funnel.
- The rule: when a review names a defect, reproduce the probe, then ask what else has that shape. A named instance is a sample, not the boundary. Read the code path the probe exercises and enumerate the inputs it accepts, rather than adding one condition that refuses one string.
- Enforced today by the argv-resolution fix in `tools/check_machine.py` and by refusal tests named in the review's resolution field.

## 2026-08-30: An agent that cites without fetching fabricates at scale

- First night of running research agents at volume: 36 impact appraisals, each paired with an adversarial verifier told to fetch every cited URL. **Roughly 85% of appraisals carried at least one evidence item whose cited source does not contain the claim.** Not one of them was caught by any deterministic gate, because every gate could see a well-formed evidence object with a real source name, a plausible date, and a URL that resolves.
- The defects were specific, not vague: "4.9 mb/d, down from 21.6 mb/d" cited to an article containing neither number; a EUR 4bn valuation cited to a release stating the parties agreed to keep financial details confidential; a ticker symbol cited to an exchange notice that gives only the ISIN; "200 GW of interconnection requests" cited to an article saying 474 GW; "19 days" cited to an article saying 8; a live 404; an HTTP 500; a source misdated by five months and another by two years; and a hallucinated firm name in a rationale.
- The mechanism is always the same. The agent reads a search snippet, writes the claim, attaches a real and topically correct URL, and never opens the page. The output is indistinguishable from good work by inspection, which is exactly why it needs a machine.
- `docs/method.md` §1 already said a number exists only if a named, dated, fetchable source states it. That rule survived as long as it did because nothing had exercised it at volume. The first day it was used at scale it failed in five of six appraisals.
- **The promotion:** every evidence item now carries `source_excerpt`, a verbatim span from the fetched source containing the claim, and `tools/check_impact.py` extracts numerics from the claim and requires each to appear in the excerpt. That makes the defect catchable offline and permanently. The pattern was already in the repo: `tools/check_map.py`'s semantic audit has required `source_excerpt` plus a content-bound `record_digest` since it shipped. It was never generalized because nothing had proved the wider need.
- **The second, cheaper lesson: an adversarial verifier that actually fetches is worth more than a better writer.** Every one of these was caught by a same-model verifier whose only advantage was the instruction to open the URL. No research stage in this repo should run without one.

## 2026-08-30: The excerpt gate, and the four things it still cannot see

- The promotion earned by the fabrication finding above: every evidence item now carries `source_excerpt`, a verbatim span from the fetched page, and `tools/impact_score.py:audit_excerpt` extracts the numbers a claim asserts and requires each to appear in that span. `tools/check_impact.py` refuses an item on a scored leg that has no excerpt or whose numbers are not in it.
- **The matcher is generous about form and strict about digits.** Tolerance scales with the precision the writer chose: `about USD 30bn` asserts one significant figure so 29.7 supports it, while `200 gigawatts` also asserts one and 474 does not, capped at a 10% relative gap so one significant figure cannot accept anything. Scale words, comma groups, spelled-out small integers and four date formats all normalize. Calibrated over 345 evidence items and 1030 numeric tokens from the night's appraisals: zero false failures, and five reproduced real fabrications all refused.
- **Migration was deliberately refused.** A `check_screen.py`-style dated cutoff would have grandfathered the exact 36-file wave that motivated the rule, which is the one outcome that makes a gate worthless. Enforcement is on everything, with a single path-plus-committed-bytes exemption for `IMP-20260830-01` that prints a warning on every run and dissolves the moment the file is amended.
- **Four blind spots, named so they are not mistaken for coverage:**
  1. **Hallucinated names and identifiers.** "Mayer Forster" and a ticker `1SXP` are text, not quantities. Requiring identifiers verbatim would false-fail `Q2` against "the second quarter" and `FY26` against "fiscal 2026", which is the error direction that gets verifiers switched off. They are counted in the denominator and not enforced.
  2. **`source_date` accuracy**, catchable only when the excerpt happens to carry a date.
  3. **Dead URLs.** A live 404 and an HTTP 500 both appeared tonight and neither is offline-checkable.
  4. **A fabricated excerpt.** Nothing offline proves the span was on the page. The gate raises the cost of faking from "attach a plausible URL" to "invent a quotation that survives a numeric cross-check", which is the whole improvement available without a fetch store.
- **The named next promotion, if (2), (3) or (4) recurs:** a `data/sources/` cache written at fetch time, checked by the gate, exactly the way `data/edgar/docs/` already backs the verbatim earnings-quote rule in `check_screen.py`. The pattern exists; it has not yet been earned twice.
- Gate falsifiability moved from 2 of 13 to 4 of 13: `check_chain` and `check_impact` now have tests that watch them refuse. `check_chain.py` was also added to CI, because a gate that runs only in a session's postlude holds only for sessions that remember to run it.

## 2026-08-30: A field frozen at the value the funnel exists to move it out of

- Two separate deadlocks of the same shape were found in one night, both of which would have stopped the ten-theme campaign dead:
  1. `tools/check_screen.py` required a screen row's profile to be `opportunity_tier == "O2"`, while `run selection` promotes exactly those profiles to O1 and the check runs over every screen on disk on every invocation. The first selection would have permanently broken every strict screen behind it. Screening and selecting were mutually exclusive.
  2. `tools/check_campaign.py` froze a theme's `chain_id` against any change versus HEAD, while the same gate permits `chain_id` to be null at stage SELECTED and `run chain <signal-id>` is the command that fills it. No theme could ever be chained; the campaign could never leave SELECTED.
- Neither was caught by a test, and neither could have been: every test wrote its fixture in the end state, so the *transition* was never exercised. Both gates were correct about the state they checked and wrong about the state machine they sat in.
- **The rule: when a gate freezes a field, ask which command is supposed to change it and at what stage.** A freeze is against a rewrite, not against the funnel advancing. Distinguish `null -> value` (the stage doing its job) from `value -> different value` (a slate rewrite) and refuse only the second.
- **The cheaper detection method, worth more than the rule:** run the funnel end to end on real data before trusting it. Both defects surfaced within minutes of the first genuine `run campaign init` and `run chain`, after the machinery had passed 389 tests and 14 gates on a tree where those commands had never run. A gate that has never watched its own command execute is a gate with an untested state machine.
- This is the second failure of this class, so it is enforced rather than only recorded: `test_a_theme_may_acquire_its_first_chain_id` and `test_a_named_chain_id_still_cannot_be_rewritten_or_cleared` in `tools/tests/test_campaign.py` watch both directions, and the screen fix carries its own pair.
