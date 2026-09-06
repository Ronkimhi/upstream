# Upstream

A private investment research machine for Ron and the collaborators he invites. It hunts what retail has not caught up to yet, top-down:

**known occurrence → value chain (8-15 links) → heat map (impact × crowdedness × value capture) → scenarios → stock screen → deep dive → red-teamed verdict (INVESTABLE / WATCH / TOO LATE)**

Two contracts:
1. **Lazy funnel.** Nothing is analyzed until you ask. Each command runs one stage on one object.
2. **Permanent memory.** Every analysis is a JSON file in `data/` with sources, dates, and an append-only changelog. Come back in three months: it loads instantly, and `refresh` updates it with the old context intact.

## Using it

New here? Open [app/guide.html](app/guide.html) (also the **Guide** tab of the dashboard): what is inside, how to read it, and how to connect your own Claude or ChatGPT to this repository, in the browser or on your computer. The short version: accept the GitHub invitation, open Claude Code (claude.ai/code) or Codex (chatgpt.com/codex) on `Ronkimhi/upstream`, and type commands:

```
run radar                        # scan for new signals (also runs on a weekday routine)
run chain SIG-20260828-02        # build a signal's value chain
run heat ai-infrastructure       # score every link: impact, crowdedness, value capture
run scenarios ai-infrastructure  # what could move this chain
run screen ai-infrastructure S2  # stocks for a scenario, bucketed, tiered
run deepdive VRT ai-infrastructure   # full stock page (verdict is DRAFT...)
run redteam VRT ai-infrastructure    # ...until a fresh-context attack makes it FINAL
log trade VRT bought 112 "starter"   # tell the machine about your real position
note ai-infrastructure "..."     # annotate anything; shows in the UI
run review                       # book + shadow calibration + reviews due
check health                     # are all the loops actually firing?
```

The full command contract lives in [CLAUDE.md](CLAUDE.md); the scoring constitution in [docs/method.md](docs/method.md).

## The UI

`app/index.html` — one self-contained page, rebuilt by every run (`python3 app/build.py`), also published as a private claude.ai artifact shared between us. Radar board with a what-changed brief → chain flow with the money-corner map → scenarios → screens → stock pages with entry/no-entry zones → shadow book and trade book.

If a button in the UI says "run …", it is a command to paste into a Claude session — analyses take minutes of real work, not clicks.

## The machinery

- **GitHub Actions** (`.github/workflows/`): `fetch.yml` fulfills data requests (prices via stooq/yfinance, SEC EDGAR fundamentals/filings/full-text search, attention data) on push of `data/requests.json`, on a weekday cron (refresh + scenario-indicator checks + shadow repricing), and on dispatch. `smoke.yml` probes every external source weekly and watches for the radar routine going silent. `ci.yml` validates every push. No AI runs in Actions; no secrets beyond `GITHUB_TOKEN`.
- **Claude routines** (claude.ai, prompts in `docs/routines.md`): `upstream-radar` weekdays, `upstream-digest` Saturdays.
- **Data tiers, printed honestly**: T1 US/SEC filers (full machine data) · T2 ADR/OTC foreign filers (prices + 20-F facts) · T3 local-only listings (best-effort prices, web-cited fundamentals). Chains are global regardless.

## Not advice

Private research tooling. Verdicts, zones, and levels are analytical outputs from public data with stated methods and known gaps. Not investment advice; nothing here executes trades.
