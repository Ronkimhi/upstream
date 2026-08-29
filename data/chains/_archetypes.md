# Archetype library

Link concepts that recur across chains. Atlas reads this before building a map: if the chain in
front of him is an instance of an archetype already mapped, he inherits its shape, its EDGAR
query set, and what it historically turned out to be worth, instead of re-deriving it.

This is the map-side equivalent of `data/taste.md`, and it uses the same two hardening bars:

- `METHOD` — the constitution applied to concrete evidence across the corpus. Hardens on first
  application, because the reasoning is method §3 and §4 and the evidence is on the map.
- `PREFERENCE` — inferred from Ron's notes or corrections. Needs the same pattern **twice**, with
  both quotes and both dates.

An archetype is a prior, not a rule. If applying one would flatten something genuinely new about
the chain in hand, the conflict is named in that run's ledger line and the archetype stands until
Ron rules. Machine-readable mirror, with recurrence counts recomputed each run:
`data/chains/_map-log.json`.

Query sets marked *(from v8)* were adopted 2026-08-29 from `value-chain-map-skill` in
`ron-brain/the-system-v8-ron`, whose L1 lane was benched into this repo on the same date. That
skill was fully specified and never executed; the query strings are its most concrete asset.

---

## ARCH-001 — precision manufacturing capacity as the gating step

**Shape:** the tool or process capacity that gates the ramp, not the product being ramped. Always
scores CHOKE_POINT. The tell is a role sentence of the form "X capacity, not Y output, has
repeatedly gated Z."

**Evidence:** `ai-infrastructure/advanced-packaging` ("Packaging capacity, not wafer starts, has
repeatedly gated accelerator output"), `humanoid-actuators/precision-machine-tools`
("machine-tool availability can therefore cap component capacity expansion"),
`humanoid-actuators/harmonic-reducers` ("lead times run 26+ weeks and precision-manufacturing
limits make it structurally harder to scale than electronics"). Three chains, three phrasings of
one structure.

**What to check when it fires:** lead time in weeks, whether capacity is being added and by whom,
and how thin the listed coverage is. Two of the three instances are thin: `precision-machine-tools`
carries one ticker.

**Queries:** `"lead time" AND "capacity"`, `"qualification" AND "capacity"`, `"machine tool"`,
`"capacity expansion" AND "backlog"`

**Origin:** METHOD · **First applied:** 2026-08-29 · **Status:** LIVE · **Chains:** 2 of 3

## ARCH-002 — EPC and civil contractor: volume without capture

**Shape:** the link that does the most visible work and keeps the least of the money. Competitively
bid or regulated-return. High impact, low capture, and it is the trap the capture score exists to
catch.

**Evidence:** `ai-infrastructure/grid-interconnect`, whose own capture rationale reads "THE TRAP
EXAMPLE: much of this link is regulated-return or competitively bid EPC, volume without
proportional profit capture" (impact 70, capture 40). `tibet-mega-dam/civil-construction`
(impact 75, capture 30).

**What to check when it fires:** default capture ceiling near 40 unless the evidence overrides it,
and the trap is named explicitly in the rationale rather than left for the reader.

**Queries:** `"engineering, procurement and construction"`, `"backlog" AND "book-to-bill"`,
`"fixed-price contract"`

**Origin:** METHOD · **First applied:** 2026-08-29 · **Status:** LIVE · **Chains:** 2 of 3

## ARCH-003 — the uninvestable order-book owner

**Shape:** the link that sets everyone else's demand and that you cannot buy. Always high impact,
always zero or near-zero listed coverage, and it is where the chain's real schedule lives.

**Evidence:** `tibet-mega-dam/yajiang-developer` (UNINVESTABLE, CHOKE_POINT, zero tickers, "the
order book every supplier link sells into"), `ai-infrastructure/frontier-models` (MOSTLY_PRIVATE,
"the largest single buyers of compute"), `humanoid-actuators/dexterous-hands` (MOSTLY_PRIVATE,
zero tickers). Three chains, three instances.

**What to check when it fires:** it is never the trade; it is the schedule. Map it so the links
that sell into it can be timed, and say plainly in `map_limitation` that it publishes nothing.

**Queries:** anchor-side only, and only for capex totals and guidance inflections. Do not attempt
supplier discovery here, there is no filer.

**Origin:** METHOD · **First applied:** 2026-08-29 · **Status:** LIVE · **Chains:** 3 of 3

## ARCH-004 — critical material input, criticality set by substitutability times logistics

**Shape:** a bulk or critical material feeding the chain. The archetype does **not** fix the
criticality: across the corpus the same shape spans the whole enum, and the differentiator is
substitutability multiplied by logistics.

**Evidence:** `humanoid-actuators/ndfeb-magnets` (CHOKE_POINT: no drop-in substitute),
`tibet-mega-dam/cement-materials` (HIGH: commodity, but hauling to Medog is prohibitive, so
pricing power is regional), `tibet-mega-dam/copper-conductors` (LOW: "marginal but global").

**What to check when it fires:** state substitutability and the logistics constraint explicitly in
`capture_inputs`. A material link scored without both is guessing. This archetype is the clearest
argument for recording capture inputs at map time.

**Queries:** `"rare earth" AND "supply agreement"`, `"long-term supply agreement"`,
`"raw material" AND "price increase"`

**Origin:** METHOD · **First applied:** 2026-08-29 · **Status:** LIVE · **Chains:** 2 of 3

## ARCH-005 — power and grid equipment behind someone else's story

**Shape:** transformers, switchgear, converter valves, interconnection. The quiet industrial layer
behind a louder headline. Reliably HIGH criticality and reliably under-owned relative to the anchor
it serves.

**Evidence:** `ai-infrastructure/power-equipment` (this chain's money corner),
`ai-infrastructure/grid-interconnect`, `tibet-mega-dam/uhvdc-transmission` ("the quieter industrial
layer behind the turbine story"). Ticker-provable, not just thematically similar: `GEV` appears in
three links across two chains and `ENR.DE` in two.

**What to check when it fires:** the same names recur across unrelated chains, so check
`data/chains/` for an existing scored instance before scoring a new one, and check whether a
position would be a second expression of a bet already held.

**Queries:** `"transformer" AND "lead time"`, `"switchgear"`, `"interconnection queue"`,
`"grid" AND "backlog"`

**Origin:** METHOD · **First applied:** 2026-08-29 · **Status:** LIVE · **Chains:** 2 of 3

## ARCH-006 — geopolitical counter-response as a first-class link

**Shape:** the adversary's or the bystander's reaction to the occurrence, wired in as its own link
rather than left as narrative. Enters the graph as an extra topological root, not into the physical
flow.

**Evidence:** `tibet-mega-dam/india-counter-buildout` and `tibet-mega-dam/global-hydro-spillover`.
The second one **is that chain's money corner**.

**What to check when it fires:** this archetype appears on one chain of three and produced that
chain's best link, which makes its absence the corpus's most likely systematic miss. On any chain
with a geopolitical occurrence, ask who reacts and whether the reaction is investable somewhere
other than where the event happened.

**Queries:** session-side judgment beats rather than EDGAR: the reaction is usually announced
before it is filed.

**Origin:** METHOD · **First applied:** 2026-08-29 · **Status:** LIVE · **Chains:** 1 of 3
**Watch:** flagged as a suspected coverage gap, not a confirmed win. One instance.

## ARCH-007 — the semis layer as a leaf in someone else's chain

**Shape:** compute or semiconductors appearing inside a chain that is not about semiconductors.
Almost always LOW criticality and already crowded. Include it for completeness; it is not the
candidate.

**Evidence:** `humanoid-actuators/compute-vla` (LOW, NVDA/QCOM/GOOGL) against
`ai-infrastructure/ai-accelerators` (HIGH, NVDA) where the same layer is the story. `NVDA` and
`GOOGL` cross both chains.

**What to check when it fires:** resist scoring it as an opportunity because the ticker is famous.
QUIET or CROWDED is the usual honest answer.

**Origin:** METHOD · **First applied:** 2026-08-29 · **Status:** LIVE · **Chains:** 2 of 3

---

## Inherited query sets (from v8 `value-chain-map-skill`, adopted 2026-08-29)

Kept verbatim because they are theme-specific and already proven to be the right strings, and
because their lane was benched into this repo rather than run there.

**AI datacenter optics and interconnect** (maps to `ai-infrastructure`):
`"800G"`, `"1.6T"`, `"co-packaged optics"`, `"CPO"`, `"optical transceiver"`,
`"silicon photonics"`, `"NVIDIA" AND "% of revenue"`, `"hyperscale" AND "design win"`,
`"qualification" AND "data center"`, `"liquid cooling"`, `"backlog" AND "data center"`, `"RPO"`

**Physical AI** (maps to `humanoid-actuators`):
`"harmonic reducer"`, `"planetary roller screw"`, `"actuator" AND "humanoid"`,
`"robot" AND "design win"`

Per query, log what was run, the forms, the hits examined, and the names extracted. The direction
that works is supplier-side: suppliers disclose their customers, anchors publish no bill of
materials.
