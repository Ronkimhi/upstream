# Working on Upstream with Ron — Yotam's guide

Upstream is a private research machine for two people. You run any stage you like, annotate
anything, and disagree in place. What is tiered is not what you can *read* or *run* — it is
what you can *change about the machine itself*. Your lane is the agent contracts, which is
the part you are best at and the part where being wrong is cheap and visible: the next run's
output shows it.

## One-time setup

1. Accept the GitHub invite to `Ronkimhi/upstream` (private repo).
2. In **your own** claude.ai account: connect **your own** GitHub (Settings → Connectors),
   then open a Claude Code session and add this repo. Your sessions run on your subscription.
   Use your own account for real — see "Attribution" below; pushing through Ron's account
   erases the only part of the attribution that is not just a claim.
3. Open the shared Upstream artifact link Ron sent you. That is the UI.
4. **Every session, first thing**: `echo yotam > .claude/actor`. The file is gitignored and
   per-clone, so a cloud session that clones fresh needs it again each time. Until you do,
   the session is UNDECLARED and the gate will not let it touch anything above your lane.
   This exists because the old default was to silently assume `ron`.

## The three tiers

`python3 tools/check_collab.py --tiers` prints the live map. In short:

| Tier | What | You |
|---|---|---|
| **CRAFT** — `.claude/agents/*.md` | Nell, Atlas, Stocky, Cass: what each agent reads, owns, refuses, and how it grades itself | **Yours.** Change it directly, no permission asked. |
| **CONSTITUTION** — `CLAUDE.md`, `docs/**`, `tools/**`, `.claude/hooks/**`, `.claude/settings.json`, `.github/**`, `app/**`, `data/taste.md`, `data/chains/_archetypes.md` | the scoring rules, the session protocol, and the gates that decide whether anything else is allowed | **Ron's.** Raise it (below); don't edit it. |
| **DATA** — the rest of `data/**` | signals, chains, screens, stocks, the ledger | **Both.** The stage contracts in `CLAUDE.md` already govern these. |

An unrecognised new top-level file counts as CONSTITUTION, not DATA. The protective default
is the one that asks a human.

## Changing an agent contract (your lane)

Edit it, then have it attacked before it lands:

```
run devil .claude/agents/nell-scanner.md
```

Cass reads the diff in fresh context — never your rationale, never your ledger line, never the
transcript — and writes `data/proposals/PROP-YYYYMMDD-NN.json` with a verdict and the
surviving objection. This is not a permission step; a REJECT does not stop you. It is a
receipt, and it is the only thing that makes an instruction change reviewable at all, because
an instruction change is the one edit whose effect nobody sees until a later run quietly
produces worse work.

Two gates enforce it: the `Stop` hook will not let a session end with an unreviewed contract
change, and CI fails the push. Ron sees the PROP at the top of his next session brief and
rules KEEP / REVERT / NARROW.

## Needing something above your lane

Don't edit it and don't ask in chat, where it gets lost. Do both of these:

1. Append an ESCALATION line to `data/ledger.md` (the existing convention — Nell and Atlas
   already route out-of-lane findings there):
   `... | NOTE | ESCALATION: <one line> | by: yotam | wrote: - | result: <what you need and why> | health: n/a | artifact: n/a`
2. Write a `data/proposals/PROP-*.json` for it, so there is something concrete to rule on.

Ron sees both in his next session brief.

## Attribution, honestly

Everything you do is attributed: `by: yotam` in ledger lines and changelogs, and an
`Actor: yotam` trailer on every commit (postlude step 5).

**Inside the repo that is a claim, not proof.** A session types whatever it is told. The part
that cannot be faked is the GitHub account that pushes: CI reads `github.actor` and compares
it against the trailer. The check is one-directional — a commit claiming `Actor: ron` pushed
by your account is flagged; `Actor: yotam` pushed by Ron is normal, because Ron pushes your
work when the drain protocol rebases it through his tree. So the practical rule is just: push
as yourself.

Nothing here refuses a push (branch protection isn't available on this repo's plan). It
detects and it shouts. That is deliberate, and it means the system trusts you and checks
anyway.

## Working in it

- Read `CLAUDE.md` (commands + the postlude) and `docs/method.md` (scoring) once.
- The UI's ▶ Run buttons queue a command into the shared page; any live session picks it up.
  The same command works typed into your own session, e.g. `run heat ai-infrastructure`.
- First screen or dive on a new ticker usually ends "data pending, re-run in ~5 minutes" —
  that is the GitHub Action fetching. Re-run the same command after it lands.
- One stage per chain at a time. `python3 tools/check_collab.py --brief` at session start
  shows what Ron changed since your last commit and what is awaiting a ruling.
- Disagree in place: `note <object> "the crowdedness score is too low because ..."`. Notes
  render on the object's page. Nothing is ever overwritten — re-runs amend with history, so
  disagreement is visible, not destructive.

## What not to do

- Don't hand-edit `app/index.html` (regenerated whole) or anything under `data/market/` and
  `data/edgar/` (Actions-owned).
- Don't paste numbers from memory into analyses — the rule is fetch-or-NULL, and the
  validator will fight you.
- Don't force-push. Ever.
- Don't route around the gates. If one is wrong, that is worth an ESCALATION on its own; a
  gate nobody trusts is worse than no gate.
