# Co-pilot guide

You have full co-pilot rights: run any stage, add notes, log your own trades. Everything you do is attributed (`by: friend` in ledger lines and changelogs) and nothing is ever overwritten — re-runs amend with history, so disagreement is visible, not destructive.

## One-time setup

1. Accept the GitHub invite to `Ronkimhi/upstream` (private repo).
2. In your own claude.ai account: connect GitHub (Settings → Connectors), then open a Claude Code session and add this repo. Your sessions run on your subscription; nothing you run costs Ron anything.
3. Open the shared Upstream artifact link Ron sent you — that is the UI. It refreshes when either of us runs something (each run republishes it; the always-current copy is `app/index.html` in the repo).

## Working in it

- Read `CLAUDE.md` (the command list) and `docs/method.md` (how scoring works) once.
- Type commands in your Claude session exactly as the UI's "run" pills show them, e.g. `run heat ai-infrastructure`.
- First screen or dive on a new ticker usually ends with "data pending, re-run in ~5 minutes" — that is the GitHub Action fetching prices/filings. Re-run the same command after it lands.
- Convention: one stage per chain at a time. Check `data/ledger.md` (or the Recent activity box on the UI home) before starting something big, so we don't run the same stage twice concurrently.
- Disagree in place: `note <object> "I think the crowdedness score is too low because ..."` — notes render on the object's page.
- Your identity: append `by: friend` in ledger lines (sessions do this when you tell them who you are once per session, or set it in your first message: "I'm <name>, log me as friend").

## What not to do

- Don't hand-edit `app/index.html` (regenerated whole) or anything under `data/market/` and `data/edgar/` (Actions-owned).
- Don't paste numbers from memory into analyses — the machine's rule is fetch-or-NULL, and the validator will fight you.
- Don't force-push. Ever.
