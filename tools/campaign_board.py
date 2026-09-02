#!/usr/bin/env python3
"""The campaign board: what is done, what is next, what is blocked. Stdlib only, offline.

A ten-theme campaign runs across many sessions and credits run out mid-way. The next
session needs one command that answers three questions from DISK, not from a handover
paragraph someone remembered to write:

  * which stage is each theme actually at,
  * what is the exact next command for it,
  * and what stops that command from running.

Everything here is derived. Nothing is hand-maintained, because a hand-maintained resume
point is wrong on exactly the day it matters — the day the session that maintained it
ended early.

Two rules make it trustworthy:

  * **The stage ladder is not reimplemented.** `check_campaign._actual_theme_stage` and
    `check_campaign.canonical_mapped_placements` are imported. A board that computed its
    own stage would be a second implementation of the gate's rule, free to disagree with
    it, and the disagreement would surface as a session running a stage the gate then
    refuses. One ladder, one denominator.
  * **Every command it prints is run through `queue_allowlist.is_allowed`.** The board's
    output is meant to be pasted or clicked; a command it emits that the click queue would
    refuse is a dead end printed as an instruction. A command that cannot be formed
    legally becomes a stated blocker instead of a silent omission.

It is read-only. `--write` is the only path that writes, and it writes exactly one file,
`data/health/board.json`, which `app/build.py` inlines for the Campaign view.

Denominators are reported the way every gate here reports them: an empty tree says
SCOPE EMPTY rather than showing a clean board over nothing.

Run: python3 tools/campaign_board.py [--root PATH] [--json] [--write] [--strict]
Exit 0 always, except --strict over an empty tree.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from check_campaign import (  # noqa: E402
    LOCKED_TARGETS,
    STAGE_ORDER,
    THEME_STAGES,
    _actual_theme_stage,
    _inventory,
    _mapped_profile_placements,
    _profile_handoff,
    _stock_matches,
    canonical_mapped_placements,
    validated_public_listings,
)
from check_map import mapping_fingerprint, read_json  # noqa: E402
from market_paths import safe_name  # noqa: E402
from queue_allowlist import is_allowed, reject_reason  # noqa: E402

BOARD_PATH = ("data", "health", "board.json")
# Everything a board needs before it may claim to have seen the machine. A chain-less,
# campaign-less tree is not a finished campaign; it is a tree the board cannot read.
SCOPE_REQUIRED = ("CLAUDE.md", "docs/method.md", "data")


def _identity(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _checked(cmd):
    """A command the click queue would accept, or None plus the reason it would not.

    The board never prints an unrunnable command. A slug, ticker, or id that cannot form
    a legal command is a finding about the data, and it is surfaced as one.
    """
    if cmd is None:
        return None, None
    if is_allowed(cmd):
        return cmd, None
    return None, f"{cmd!r} is not a runnable command: {reject_reason(cmd)}"


def _blocker(kind, detail):
    return {"kind": kind, "detail": detail}


# ---------------------------------------------------------------- disk projections

def _load_campaign(root: Path):
    folder = root / "data" / "campaigns"
    manifests = sorted(folder.glob("CAMP-*.json")) if folder.is_dir() else []
    docs = [doc for doc in (read_json(path) for path in manifests) if isinstance(doc, dict)]
    if not docs:
        return None
    docs.sort(key=lambda c: (str(c.get("as_of") or ""), str(c.get("id") or "")))
    active = [c for c in docs if c.get("status") == "ACTIVE"]
    return (active or docs)[-1]


def _provisional_themes(inventory) -> list:
    """Before `run campaign init`, every chain on disk is an unranked provisional theme.

    The board has to be useful on the day the campaign has not been initialized, which is
    precisely the day someone is deciding what to run first.
    """
    themes = []
    for chain_id in sorted(inventory["chains"]):
        chain = inventory["chains"][chain_id] or {}
        themes.append({
            "theme_id": chain_id,
            "chain_id": chain_id,
            "signal_id": chain.get("signal_id"),
            "title": chain.get("title") or chain_id,
            "rank": None,
            "provisional": True,
        })
    return themes


def _campaign_themes(campaign: dict) -> list:
    themes = []
    for theme in campaign.get("themes") or []:
        if not isinstance(theme, dict):
            continue
        row = dict(theme)
        row.setdefault("theme_id", theme.get("chain_id"))
        row["title"] = theme.get("title") or theme.get("theme_id") or theme.get("chain_id")
        row["provisional"] = False
        themes.append(row)
    return themes


def _unauditable_links(mapping) -> list:
    """link_coverage rows a fresh-context audit has nothing to sample yet.

    Mirrors check_map.py's own requirement that `audit.sampled_checks` cover every
    link_coverage row exactly: a row with zero placements and no EXHAUSTED search record
    can never appear in that sample, so `run universe-audit` fails on it every time,
    deterministically. Recommending the audit anyway reproduces that failure instead of
    naming the real prerequisite, `run universe`, which is what actually closes the row.
    """
    if not isinstance(mapping, dict):
        return []
    coverage = [row for row in mapping.get("link_coverage") or [] if isinstance(row, dict)]
    placed_links = {
        row.get("link_id") for row in mapping.get("placements") or []
        if isinstance(row, dict) and row.get("link_id")
    }
    out = []
    for row in coverage:
        link_id = row.get("link_id")
        if link_id in placed_links:
            continue
        if row.get("status") == "EXHAUSTED" and row.get("searches"):
            continue
        if link_id:
            out.append(str(link_id))
    return sorted(out)


def _mapping_state(mapping) -> dict:
    """Status, audit state, and whether the PASS audit still covers current content."""
    if not isinstance(mapping, dict):
        return {"present": False, "status": None, "audit": None, "fingerprint_current": None}
    audit = mapping.get("audit") if isinstance(mapping.get("audit"), dict) else None
    current = None
    if audit is not None:
        try:
            current = audit.get("mapping_fingerprint") == mapping_fingerprint(mapping)
        except Exception:  # noqa: BLE001 - an unhashable mapping is a data defect, not a crash
            current = False
    return {
        "present": True,
        "status": mapping.get("status"),
        "audit": audit.get("status") if audit else None,
        "amendments": len(audit.get("amendments_required") or []) if audit else 0,
        "fingerprint_current": current,
    }


def _chain_link_order(chain) -> dict:
    """link_id -> (money-corner first, choke point next, then position), method 6A order."""
    order = {}
    for link in (chain or {}).get("links") or []:
        if not isinstance(link, dict):
            continue
        link_id = _identity(link.get("id"))
        if not link_id:
            continue
        heat = link.get("heat") if isinstance(link.get("heat"), dict) else {}
        bottleneck = link.get("bottleneck") if isinstance(link.get("bottleneck"), dict) else {}
        position = link.get("position")
        order[link_id] = (
            0 if heat.get("money_corner") else 1,
            0 if bottleneck.get("criticality") == "CHOKE_POINT" else 1,
            position if isinstance(position, int) else 10 ** 6,
        )
    return order


def _chain_tickers(mapping, chain_id) -> dict:
    """issuer_id -> ticker, for issuers with a validated public listing on this chain."""
    out = {}
    if not isinstance(mapping, dict):
        return out
    for listing in validated_public_listings(mapping).values():
        issuer_id = _identity(listing.get("issuer_id"))
        ticker = _identity(listing.get("ticker"))
        if issuer_id and ticker:
            out.setdefault(issuer_id, ticker)
    return out


def _pending_rows(root: Path) -> list:
    doc = read_json(root / "data" / "requests.json")
    rows = (doc or {}).get("requests") if isinstance(doc, dict) else doc
    return [row for row in rows or []
            if isinstance(row, dict) and row.get("status") == "PENDING"]


def _pending_by_issuer(pending, tickers_by_issuer) -> dict:
    """PENDING request ids keyed by the issuer they unblock.

    Resolution is by explicit `issuer_id` first, then by a ticker that resolves to exactly
    one issuer. An ambiguous ticker is left unattributed rather than guessed onto a name.
    """
    by_ticker = {}
    for issuer_id, ticker in tickers_by_issuer.items():
        by_ticker.setdefault(ticker, set()).add(issuer_id)
    out = {}
    for row in pending:
        issuer_id = _identity(row.get("issuer_id"))
        if not issuer_id:
            candidates = by_ticker.get(_identity(row.get("ticker")) or "", set())
            if len(candidates) == 1:
                issuer_id = next(iter(candidates))
        if issuer_id:
            out.setdefault(issuer_id, []).append(row.get("id"))
    return out


def _market_quality(root: Path, ticker) -> str:
    """`quality` presence on the market file a dive needs. Never invents a state."""
    path = root / "data" / "market" / f"{safe_name(ticker)}.json"
    doc = read_json(path)
    if not isinstance(doc, dict):
        return "NO_MARKET_FILE"
    return "PRESENT" if isinstance(doc.get("quality"), dict) else "NO_QUALITY_BLOCK"


# ---------------------------------------------------------------- per-theme worklist

def _theme_profiles(inventory, chain_id) -> list:
    return [
        profile for profile in inventory["profiles"]
        if any(placement_chain == chain_id for placement_chain, _, _
               in _mapped_profile_placements(profile, inventory["placements"]))
    ]


def _theme_row(root: Path, theme: dict, inventory, campaign, targets, pending) -> dict:
    chain_id = _identity(theme.get("chain_id"))
    chain = inventory["chains"].get(chain_id)
    mapping = inventory["maps"].get(chain_id)
    mapping_state = _mapping_state(mapping)
    stage = _actual_theme_stage(theme, inventory, targets)
    campaign_id = _identity((campaign or {}).get("id"))

    profiles = _theme_profiles(inventory, chain_id)
    complete = {_identity(p.get("issuer_id")) for p in profiles
                if p.get("status") == "COMPLETE"
                and p.get("opportunity_tier") in {"O1", "O2"}}
    blocked = [p for p in profiles if p.get("status") in {"BLOCKED", "DRAFT"}]
    placements = {p for p in inventory["placements"] if p[0] == chain_id}
    tickers = _chain_tickers(mapping, chain_id)
    pending_by_issuer = _pending_by_issuer(pending, tickers)

    theme_o1 = [
        profile for profile in inventory["profiles"]
        if profile.get("opportunity_tier") == "O1"
        and (_profile_handoff(profile, inventory) or {}).get("chain_id") == chain_id
    ]
    final_issuers = {
        _identity(profile.get("issuer_id")) for profile in theme_o1
        if any(_stock_matches(profile, stock, inventory)
               for stock in inventory["stocks"])}
    drafts = [
        (profile, stock) for profile in theme_o1 for stock in inventory["stocks"]
        if stock.get("status") == "DRAFT"
        and _stock_matches(profile, stock, inventory, require_final=False)
    ]

    blockers = []
    command = None
    reason = None

    # --- mapping health, reported at every stage that rests on it -------------------
    if mapping_state["present"]:
        if mapping_state["status"] == "ACTIVE" and mapping_state["audit"] == "FAIL":
            blockers.append(_blocker(
                "MAPPING_AUDIT_FAIL",
                f"mapping audit FAIL with {mapping_state['amendments']} amendment(s) "
                f"required; `run universe` corrects it, the audit cannot"))
        elif mapping_state["status"] == "ACTIVE" and _unauditable_links(mapping):
            untouched = _unauditable_links(mapping)
            blockers.append(_blocker(
                "MAPPING_LINK_UNTOUCHED",
                f"link_coverage row(s) {untouched} have no placement and no EXHAUSTED "
                "search; a fresh-context audit has nothing to sample there, so `run "
                "universe` must close them before `run universe-audit` can pass"))
        elif mapping_state["status"] == "ACTIVE":
            blockers.append(_blocker(
                "MAPPING_AWAITING_AUDIT",
                "mapping is ACTIVE and needs a fresh-context `run universe-audit` before "
                "profiles or screens may consume it"))
        elif mapping_state["status"] == "COMPLETE" and (
                mapping_state["audit"] != "PASS"
                or mapping_state["fingerprint_current"] is not True):
            blockers.append(_blocker(
                "MAPPING_FINGERPRINT_STALE",
                "mapping is COMPLETE but its PASS audit no longer covers current material "
                "content (fingerprint mismatch); screens resting on it fail the screen gate"))

    if any(screen.get("chain_id") == chain_id for screen in inventory["screens"]) and \
            mapping_state["present"] and not (
                mapping_state["status"] == "COMPLETE"
                and mapping_state["audit"] == "PASS"
                and mapping_state["fingerprint_current"] is True):
        blockers.append(_blocker(
            "SCREEN_MAPPING_STALE",
            "a screen exists on this chain while its mapping has no current PASS audit; "
            "tools/check_screen.py refuses those rows"))

    # --- profiles blocked on data --------------------------------------------------
    stuck = sorted(
        (issuer_id, pending_by_issuer[issuer_id])
        for profile in blocked
        if (issuer_id := _identity(profile.get("issuer_id"))) in pending_by_issuer
    )
    if stuck:
        named = ", ".join(f"{issuer_id} ({'/'.join(str(r) for r in reqs)})"
                          for issuer_id, reqs in stuck[:5])
        blockers.append(_blocker(
            "PROFILE_PENDING_DATA",
            f"{len(stuck)} profile(s) BLOCKED behind PENDING request rows: {named}"
            + (" …" if len(stuck) > 5 else "")))

    # --- the next command ----------------------------------------------------------
    if stage == "SELECTED":
        signal_id = _identity(theme.get("signal_id"))
        command, why = _checked(f"run chain {signal_id}" if signal_id else None)
        reason = "no chain exists for this theme yet"
        if command is None:
            blockers.append(_blocker(
                "NO_SIGNAL",
                why or "the theme names no signal_id, so no chain command can be formed"))
    elif stage == "CHAINED":
        command, why = _checked(f"run heat {chain_id}" if chain_id else None)
        reason = "no link carries a heat verdict yet"
        if why:
            blockers.append(_blocker("BAD_CHAIN_ID", why))
    elif stage == "HEATED":
        command, why = _checked(f"run scenarios {chain_id}" if chain_id else None)
        reason = "every link is scored; no scenarios yet"
        if why:
            blockers.append(_blocker("BAD_CHAIN_ID", why))
    elif stage == "SCENARIOS":
        unauditable = _unauditable_links(mapping) if mapping_state["present"] else []
        if mapping_state["present"] and mapping_state["status"] == "ACTIVE" and \
                mapping_state["audit"] != "FAIL" and not unauditable:
            command, why = _checked(f"run universe-audit {chain_id}")
            reason = "a fresh-context audit decides COMPLETE"
        else:
            command, why = _checked(f"run universe {chain_id}")
            if mapping_state["audit"] == "FAIL":
                reason = "author correction after a FAIL audit"
            elif unauditable:
                reason = (f"link_coverage row(s) {unauditable} have no placement or "
                          "EXHAUSTED search yet; not audit-ready")
            else:
                reason = "no COMPLETE issuer mapping for this chain"
        if why:
            blockers.append(_blocker("BAD_CHAIN_ID", why))
    elif stage == "MAPPED":
        minimum = targets.get("profiles_per_theme_min",
                              LOCKED_TARGETS["profiles_per_theme_min"])
        blockers.append(_blocker(
            "PROFILES_BELOW_MINIMUM",
            f"{len(complete)}/{minimum} COMPLETE O1+O2 profiles on this theme"))
        if campaign_id:
            command, why = _checked(f"run profile --campaign {campaign_id}")
            reason = "bounded batch, at most 15 issuers per run"
            if why:
                blockers.append(_blocker("BAD_CAMPAIGN_ID", why))
        else:
            profiled = {_identity(p.get("issuer_id")) for p in inventory["profiles"]}
            order = _chain_link_order(chain)
            queue = sorted(
                (order.get(link_id, (9, 9, 10 ** 6)), tickers[issuer_id], issuer_id)
                for _, link_id, issuer_id in placements
                if issuer_id in tickers and issuer_id not in profiled
            )
            command, why = _checked(f"run profile {queue[0][1]}" if queue else None)
            reason = "no manifest: one ticker at a time, money corner first"
            if why:
                blockers.append(_blocker("UNRUNNABLE_TICKER", why))
            elif not queue:
                blockers.append(_blocker(
                    "NO_UNPROFILED_ISSUER",
                    "every mapped issuer on this chain already has a profile, yet the "
                    "COMPLETE count is short: the gap is profile quality, not coverage"))
    elif stage == "PROFILED":
        command, why = _checked(f"run screen {chain_id}")
        reason = "profiles are in place; no screen yet"
        if why:
            blockers.append(_blocker("BAD_CHAIN_ID", why))
    elif stage == "SCREENED":
        ready = sorted(
            (profile for profile in theme_o1
             if _identity(profile.get("issuer_id")) not in final_issuers),
            key=lambda profile: str(_identity(profile.get("issuer_id")) or ""))
        if ready:
            handoff = _profile_handoff(ready[0], inventory) or {}
            listing = inventory["listings"].get(
                (chain_id, handoff.get("listing_id"))) or {}
            ticker = _identity(listing.get("ticker"))
            quality = _market_quality(root, ticker) if ticker else "NO_MARKET_FILE"
            if ticker and quality != "PRESENT":
                command, why = _checked(f"request data {ticker}")
                reason = f"{ticker} is queued for a dive; its market file is not ready"
                missing = ("no data/market file at all"
                           if quality == "NO_MARKET_FILE"
                           else "market file carries no quality block")
                blockers.append(_blocker(
                    "DIVE_DATA_MISSING",
                    f"{ticker}: {missing}; tools/check_analyst.py requires the "
                    f"series AND the quality block"))
            else:
                command, why = _checked(
                    f"run deepdive {ticker} {chain_id}" if ticker else None)
                reason = "an O1 name on this theme has no dive"
                if why or not ticker:
                    blockers.append(_blocker(
                        "UNRUNNABLE_TICKER",
                        why or "the O1 handoff resolves no listing ticker"))
        elif campaign_id:
            command, why = _checked(f"run selection {campaign_id}")
            reason = "screen exists; no profile here is O1 yet"
            if why:
                blockers.append(_blocker("BAD_CAMPAIGN_ID", why))
        else:
            reason = "screen exists; no profile here is O1 yet"
            blockers.append(_blocker(
                "SELECTION_NEEDS_CAMPAIGN",
                "O1 selection runs as `run selection <CAMP-ID>` and no campaign manifest "
                "exists; run `run campaign init` first"))
    elif stage == "DIVED":
        pairs = sorted(drafts, key=lambda pair: str(pair[1].get("ticker") or ""))
        ticker = _identity(pairs[0][1].get("ticker")) if pairs else None
        command, why = _checked(
            f"run redteam {ticker} {chain_id}" if ticker else None)
        reason = "a DRAFT dive awaits its fresh-context red team"
        if why or not ticker:
            blockers.append(_blocker(
                "UNRUNNABLE_TICKER",
                why or "the DRAFT dive names no ticker"))
    else:  # COMPLETE
        reason = "every O1 name on this theme has a FINAL dive"

    return {
        "theme_id": _identity(theme.get("theme_id")) or chain_id,
        "chain_id": chain_id,
        "title": theme.get("title") or chain_id,
        "rank": theme.get("rank") if isinstance(theme.get("rank"), int)
        and not isinstance(theme.get("rank"), bool) else None,
        "provisional": bool(theme.get("provisional")),
        "stage": stage,
        "stage_written": theme.get("stage"),
        "next_command": command,
        "next_reason": reason,
        "blockers": blockers,
        "mapping": mapping_state,
        "counts": {
            "links": len((chain or {}).get("links") or []),
            "mapped_issuers": len({issuer_id for _, _, issuer_id in placements}),
            "placements": len(placements),
            "complete_profiles": len(complete),
            "o1": len(theme_o1),
            "o1_final": len(final_issuers),
            "pending_profiles": len(stuck),
        },
    }


# ---------------------------------------------------------------- the board

def build_board(root: Path) -> dict:
    root = Path(root)
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    missing = [rel for rel in SCOPE_REQUIRED if not (root / rel).exists()]
    inventory = _inventory(root)
    campaign = _load_campaign(root)
    themes = (_campaign_themes(campaign) if campaign
              else _provisional_themes(inventory))

    if missing or not themes:
        why = (f"missing {', '.join(missing)}" if missing
               else "no campaign manifest and no chain on disk")
        return {
            "as_of": today,
            "generated_by": "tools/campaign_board.py",
            "scope": "SCOPE_EMPTY",
            "scope_note": (f"SCOPE EMPTY at {root}: {why}. This is not a clean board; "
                           f"the board could not see any campaign work."),
            "campaign_id": _identity((campaign or {}).get("id")),
            "campaign_status": (campaign or {}).get("status"),
            "targets": dict(LOCKED_TARGETS),
            "denominators": {
                "themes_total": 0,
                "themes_by_stage": {},
                "distinct_mapped_issuers": 0,
                "complete_profiles": 0,
                "o1": 0,
                "final_dives": 0,
                "pending_requests": len(_pending_rows(root)),
                "pending_requests_blocking": 0,
            },
            "next_command": None,
            "worklist": [],
        }

    targets = (campaign or {}).get("targets") or LOCKED_TARGETS
    if not isinstance(targets, dict):
        targets = LOCKED_TARGETS

    pending = _pending_rows(root)
    rows = [_theme_row(root, theme, inventory, campaign, targets, pending)
            for theme in themes]
    # rank (Tally's impact ordering, frozen in the manifest), then funnel stage so
    # breadth beats depth, then id so two identical boards are identical.
    rows.sort(key=lambda row: (
        row["rank"] is None,
        row["rank"] if row["rank"] is not None else 10 ** 6,
        STAGE_ORDER.get(row["stage"], len(THEME_STAGES)),
        str(row["theme_id"]),
    ))

    theme_chains = {row["chain_id"] for row in rows if row["chain_id"]}
    placements = canonical_mapped_placements(inventory["maps"], theme_chains)
    mapped_issuers = {issuer_id for _, _, issuer_id in placements}
    complete_profiles = {
        _identity(profile.get("issuer_id")) for profile in inventory["profiles"]
        if profile.get("status") == "COMPLETE"
        and profile.get("opportunity_tier") in {"O1", "O2"}
        and _mapped_profile_placements(profile, placements)
    }
    o1_profiles = [
        profile for profile in inventory["profiles"]
        if profile.get("opportunity_tier") == "O1"
        and (_profile_handoff(profile, inventory) or {}).get("chain_id") in theme_chains
    ]
    final_dives = sum(
        1 for profile in o1_profiles
        if any(_stock_matches(profile, stock, inventory)
               for stock in inventory["stocks"]))

    all_tickers = {}
    for chain_id in theme_chains:
        all_tickers.update(_chain_tickers(inventory["maps"].get(chain_id), chain_id))
    blocking = _pending_by_issuer(pending, all_tickers)

    by_stage = {}
    for row in rows:
        by_stage[row["stage"]] = by_stage.get(row["stage"], 0) + 1

    next_command = None
    if campaign is None:
        next_command = _checked("run campaign init")[0]
    else:
        for row in rows:
            if row["next_command"]:
                next_command = row["next_command"]
                break

    return {
        "as_of": today,
        "generated_by": "tools/campaign_board.py",
        "scope": "CAMPAIGN" if campaign else "PROVISIONAL",
        "scope_note": (
            "campaign manifest drives theme identity and rank"
            if campaign else
            "no campaign manifest: every chain on disk is shown as an unranked "
            "provisional theme, and `run campaign init` freezes the real slate"),
        "campaign_id": _identity((campaign or {}).get("id")),
        "campaign_status": (campaign or {}).get("status"),
        "targets": dict(targets),
        "denominators": {
            "themes_total": len(rows),
            "themes_by_stage": {stage: by_stage[stage]
                                for stage in THEME_STAGES if stage in by_stage},
            "distinct_mapped_issuers": len(mapped_issuers),
            "complete_profiles": len(complete_profiles),
            "o1": len(o1_profiles),
            "final_dives": final_dives,
            "pending_requests": len(pending),
            "pending_requests_blocking": sum(len(v) for v in blocking.values()),
        },
        "next_command": next_command,
        "worklist": rows,
    }


# ---------------------------------------------------------------- human report

def render(board: dict) -> str:
    out = []
    if board["scope"] == "SCOPE_EMPTY":
        out.append(f"campaign_board: SCOPE EMPTY  ({board['as_of']})")
        out.append(f"  {board['scope_note']}")
        return "\n".join(out)

    d = board["denominators"]
    t = board["targets"]
    stages = " · ".join(f"{stage} {count}"
                        for stage, count in d["themes_by_stage"].items()) or "none"
    out.append(f"campaign_board: {board['as_of']}  scope {board['scope']}"
               + (f"  {board['campaign_id']} ({board['campaign_status']})"
                  if board["campaign_id"] else ""))
    out.append(f"  {board['scope_note']}")
    out.append("")
    out.append(f"  themes            {d['themes_total']}   ({stages})")
    out.append(f"  mapped issuers    {d['distinct_mapped_issuers']} distinct "
               f"across {len(board['worklist'])} theme(s)")
    if t.get("mode") == "DEPTH":
        out.append(f"  mode              DEPTH: {t.get('issuers_per_link')} issuers per "
                   f"in-scope link ({', '.join(t.get('links_in_scope') or [])}), "
                   f"{t.get('o1_per_theme_min')}-{t.get('o1_per_theme_max')} O1 per theme")
        out.append(f"  complete profiles {d['complete_profiles']}")
        out.append(f"  O1                {d['o1']}")
        out.append(f"  FINAL dives       {d['final_dives']} / {d['o1']} O1 "
                   f"(campaign done at {t.get('verdicts_min')}-{t.get('verdicts_max')})")
    else:
        out.append(f"  complete profiles {d['complete_profiles']} / "
                   f"{t.get('completed_profiles_min', '?')}")
        out.append(f"  O1                {d['o1']} / "
                   f"{t.get('o1_min', '?')}-{t.get('o1_max', '?')}")
        out.append(f"  FINAL dives       {d['final_dives']} / {d['o1']} O1")
    out.append(f"  pending requests  {d['pending_requests']} "
               f"({d['pending_requests_blocking']} attributable to a theme issuer)")
    out.append("")
    out.append("  worklist (manifest rank, then funnel stage, then id)")
    for i, row in enumerate(board["worklist"], 1):
        rank = row["rank"] if row["rank"] is not None else "-"
        counts = row["counts"]
        out.append(f"   {i:>2}. [{rank}] {row['theme_id']}   {row['stage']}")
        out.append(f"       {counts['mapped_issuers']} mapped issuers · "
                   f"{counts['complete_profiles']} complete profiles · "
                   f"{counts['o1']} O1 · {counts['o1_final']} FINAL")
        out.append(f"       next:    {row['next_command'] or '(nothing to run)'}"
                   f"   — {row['next_reason']}")
        if row["blockers"]:
            for blocker in row["blockers"]:
                out.append(f"       blocked: {blocker['kind']}: {blocker['detail']}")
        else:
            out.append("       blocked: nothing")
    out.append("")
    out.append(f"  run next: {board['next_command'] or '(nothing to run)'}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--json", action="store_true",
                    help="print the machine form to stdout")
    ap.add_argument("--write", action="store_true",
                    help="write data/health/board.json (the ONLY path that writes)")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 on SCOPE EMPTY, the same posture as check_machine.py")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    board = build_board(root)
    print(json.dumps(board, indent=1) if args.json else render(board))

    if args.write:
        path = root.joinpath(*BOARD_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(board, indent=1) + "\n")
        if not args.json:
            print(f"\ncampaign_board: wrote {path.relative_to(root)}")

    if board["scope"] == "SCOPE_EMPTY" and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
