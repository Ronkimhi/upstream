---
name: pull-data
description: How any Upstream agent gets market/EDGAR data onto disk, exactly. Use whenever a stage needs a price, fundamentals figure, quality score, PCS block, insider data, filing document, or full-text search hit that is not already in data/market/ or data/edgar/. Covers the request-row schema, id format, kind ordering, the ADR cik override, verification after the fetch, and every known failure mode.
---

# pull-data — the data bridge, exactly

**The one rule (venue rule, CLAUDE.md):** sessions NEVER fetch market or EDGAR data
themselves. You queue rows in `data/requests.json`; GitHub Actions
(`tools/fetch/fetch.py`) fetches on the next push, ~2-4 minutes. Every number you use
comes from disk, or is NULL with a basis. A remembered number is a defect.

## Step 1 — Check disk BEFORE queuing (and re-check before claiming a gap)

```bash
git pull --rebase   # stash/pop around it if your tree is dirty
```

| You need | Look in | The field |
|---|---|---|
| price, series, 52wk | `data/market/<T>.json` | `prints`, `series`, `week52`, `price_status` |
| fundamentals | same file | `fundamentals` (`source`: `sec-companyfacts` or `yfinance-statements`) |
| Piotroski/Beneish/Altman/rDCF | same file | `quality` |
| positioning/crowdedness | same file | `pcs` |
| insider (Form 4) | same file | `insider` |
| filing text (for verbatim nuggets) | `data/edgar/docs/<T>.json` | whole file |
| full-text search | `data/edgar/fts/<slug>.json` | whole file |

`<T>` in filenames: dots become dashes (`ENR.DE` → `ENR-DE.json`; `safe_name()` in fetch.py).

**Incident to not repeat (2026-08-31):** an agent declared TSM/AMKR/MU/INTC "blocked, no
data" while all five files sat complete on disk — it read a stale checkout. Pull, then
read the actual file, then claim.

## Step 2 — Queue rows

Pull-rebase immediately before writing. Append to `requests` in `data/requests.json`:

```json
{"id": "REQ-YYYYMMDD-NN", "kind": "prices", "ticker": "AMKR",
 "query": null, "forms": null, "lookback_days": null,
 "requested_by": "<command and one line of why>",
 "requested_at": "<UTC ISO>", "by": "<agent or ron>",
 "status": "PENDING", "note": null}
```

- **id**: `REQ-` + today UTC as `YYYYMMDD` + `-NN`, NN = highest existing NN for today
  + 1, zero-padded to 2. `REQ-20260901-17`, not `REQ-2026-09-01-17` (that malformed id
  shipped once; do not copy it).
- **kinds**: `prices` · `fundamentals` · `quality` · `pcs` · `insider` (takes
  `lookback_days`, default 365) · `edgar_doc` (takes `lookback_days`, default 200) ·
  `edgar_fts` (takes `query` + optional `forms` instead of `ticker`; may carry `link_id`) ·
  `web_doc` (takes `url` instead of `ticker`; stores the page text at `data/web/<id>.json`,
  see `tools/evidence_store.py`; a 403/404 is stored as the page's status, so cite elsewhere).
- **Order inside one batch matters and one batch is fine**: the fetcher runs rows in file
  order, and `quality` is pure computation over what `fundamentals` wrote — so per ticker
  queue `prices`, then `fundamentals`, then `quality` (then `pcs`, `insider`, `edgar_doc`
  as needed).
- **Foreign/ADR (`cik` override)**: a dotted ticker (`BP.L`, `ENR.DE`) never resolves to
  a US SEC CIK on its own (by design, since the wrong-company incident of 2026-08-31). If
  the issuer really files with the SEC (an ADR's 20-F), assert it: add `"cik": <int>` to
  the row, with the link evidenced in your stage's own record. No `cik` → the vendor leg
  (`yfinance-statements`, tagged INFERRED) serves fundamentals.
- Never edit or delete existing rows, never change a `status` — Actions is the only
  status-transitioner. Sessions only append.

## Step 3 — Commit, push, wait, re-read

```bash
git add data/requests.json && git commit -m "[request data] <tickers>" && git pull --rebase && git push
```

The push triggers the fetch. Then either end your run with exactly
`data pending, re-run <command> in ~5 minutes.`, or if you must wait in-run:

```bash
gh run list --workflow=fetch.yml --limit 1 --json status,conclusion
git pull --rebase   # after it completes — the results are COMMITS, not local files
```

## Step 4 — Read the outcome honestly

- Row `status`: `FULFILLED` (data is on disk) · `FAILED` + `note` (the note now carries
  per-leg reasons; `terminal: true` means retries are exhausted — do not blind-requeue).
- `price_status: VERIFIED_ZERO` = genuinely no price, certified by a same-plane control
  probe. `SINGLE_SOURCE` = one leg answered (normal for foreign listings).
- `tier`: `T1` US SEC filer · `T2` ADR with asserted CIK · `T3` local-only/vendor.
- `fundamentals.source: "yfinance-statements"` + `tag: INFERRED` +
  `official_source: false`: feeds the `quality` block and, since Ron's 2026-09-01
  decision (method §6A), may back a T2/T3 canonical profile metric directly when the
  metric's source block names the vendor and says so. Never a filing quote; O1 promotion
  still needs an official cross-check on revenue and cash conversion.
- `fundamentals.sec_attempted`: the SEC leg was fetched for this CIK, found zero annual
  fields under both `us-gaap` and `ifrs-full`, and the vendor leg served the block instead
  (fetch.py `do_fundamentals`, 2026-09-01). `fundamentals.taxonomy` and
  `statement_currency` say which taxonomy and currency a SEC block was read in.

## Known failure modes — recognize, don't fight

| Symptom | It means | Do |
|---|---|---|
| IFRS 20-F filer, companyfacts ~empty (TSM, ASX) | real gap in SEC XBRL, probe passing | don't requeue; profile stays BLOCKED until the foreign-data decision |
| dotted ticker has no CIK | by design | vendor leg, or assert `cik` with evidence |
| bare local code (`005930`) unfetchable | needs suffixed `market_ticker` in the mapping | backlog row `listing-identity-normalization`; don't invent a spelling |
| stooq robots/JS-challenge noise in logs | stooq is dead to non-browsers | ignore; yfinance is the working plane |
| row would fulfill empty forever | nothing exists to fetch | don't queue it; write NULL with basis instead |

## Never

Fetch market data with WebFetch/curl yourself · invent or "remember" a number · write to
`data/market/` or `data/edgar/` (Actions-owned) · copy another issuer's file as a
template (the Vertiv contamination, 2026-08-31) · claim "no data" without a pull and a
fresh read of the exact file.
