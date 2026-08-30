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
