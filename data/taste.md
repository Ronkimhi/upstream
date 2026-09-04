# Taste ledger

Revealed preferences, appended with evidence. Radar and screens consume these as VISIBLE filters:
a filtered name is always listed with the rule that filtered it, never silently dropped.
Rules are editable; each carries its justification and date.

**Two origins, two hardening bars** (set 2026-08-29 when the first rules landed):

- `METHOD` — the constitution (`docs/method.md`, usually the §0 retail-gap test) applied to a
  concrete case with cited evidence. Hardens on first application, because the reasoning is the
  method's, not a preference, and the evidence is on the card.
- `PREFERENCE` — inferred from Ron's notes or dismissals. Needs the same pattern **twice**, with
  both quotes and both dates, before it becomes a rule. One offhand remark is not a rule.

A rule that would suppress something genuinely new (learned in a world that has since changed) is
not silently obeyed and not silently overridden: the conflict is named in that run's ledger line
and the rule stands until Ron rules on it.

Machine-readable mirror, with the proposal queue and the spot-test record: `data/radar/scout-log.json`.

---

## TASTE-001 — no direct rare-earth-miner plays

**Rule:** Do not open a signal or advance a screen row whose thesis is direct exposure to
rare-earth miners (MP Materials, Lynas and their peers). The chain positions *behind* them
(separation chemistry, magnet-grade alloy, gear-grinding tooling) remain in scope.

**Why:** method §0 retail-gap test fails outright. The trade is already the retail story.

**Evidence:** MP Materials +165% and Lynas +159% YoY, with coverage explicitly citing
"retail enthusiasm and trading volume" [radar sweep 2026-08-29, ledger 14:55Z].

**Origin:** METHOD · **First applied:** 2026-08-29 · **Status:** LIVE
**Review:** re-test if a drawdown of 40% or more takes the attention out of these names.

## TASTE-002 — no Taiwan chip-concentration diversification thesis

**Rule:** Do not open a signal whose thesis is "TSMC concentration risk drives diversification."
Specific unmapped consequences of a *dated* event inside that world (an export ruling, a fab
qualification milestone, an equipment control) are still in scope; the standing macro story is not.

**Why:** method §0 retail-gap test fails. Exhaustively mapped by sell-side already, with a
published 2030 to 2035 diversification timeline. There is no gap left to be early to.

**Evidence:** radar sweep 2026-08-29 filtered this candidate for exactly this reason
[ledger 14:55Z, verbatim: "TSMC concentration risk already exhaustively mapped by sell-side
with a 2030-2035 diversification timeline, no retail gap"].

**Origin:** METHOD · **First applied:** 2026-08-29 · **Status:** LIVE
**Review:** re-test on a discrete geopolitical break that resets the timeline.

## TASTE-003 — every new discovery gets its own card

**Rule:** A credible new discovery is written as its own signal card by default, rather than
folded into an existing card or left to sit as a candidate. Dedupe against an existing card's
thesis still applies (a card that deepens gets an update plus a changelog entry, never a
duplicate), and the promotion bar (method §0/§1: >=2 cited dated evidence items, occurrence
block, unmappedness scored) is unchanged. This raises the default toward writing a card; it
does not lower what a card needs to clear the bar. An occurrence that cannot support that bar
stays a candidate with the reason named, exactly as before.

**Why:** Ron's direct instruction, said while approving the promotion of the rare-earth
candidate (CAND-20260830-21) to a full signal card.

**Evidence:** Ron, 2026-09-01, verbatim: "every new discovery gets its own card."

**Origin:** PREFERENCE · **First applied:** 2026-09-01 · **Status:** PROPOSED, applied
immediately. This is a direct standing instruction from Ron, not an inferred pattern from a
note or a dismissal, so it is followed starting the run it was given rather than waiting on a
second occurrence. But `data/radar/scout-log.json`'s machine record (the mirror this file
points to) enforces its two-occurrence HARDENED bar the same way for every PREFERENCE-origin
rule regardless of how the first occurrence arose, and that gate is not silently worked around
here: the log carries this rule PROPOSED with the conflict named in an `escalation_note`, for
Ron to rule on whether a direct instruction should harden on first application the way a
METHOD-origin rule does. The rule binds in practice either way, per CLAUDE.md's "working under
Ron's direction": his asking for it is the authorization.
**Review:** re-test if a run's card count against the 0-8 cap starts forcing a choice between
this rule and the cap; the cap is not raised by this rule.
