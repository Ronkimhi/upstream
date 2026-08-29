# Vendored third-party code

The only external code in this repo's UI. Inlined verbatim into `app/index.html` by `app/build.py`
(the page is a single self-contained artifact under a strict CSP; nothing loads from a network).

## What and why

The cortex view (`#/`) runs a force-directed layout over ~400 bodies. `d3-force` gives Barnes-Hut
many-body approximation, link/collide/positioning forces, and a velocity-Verlet integrator that a
hand-rolled O(n^2) loop cannot match at that node count.

Only the four modules actually needed are vendored. The full d3 bundle (~280 KB) is not.

| file | package | version | bytes | sha256 (first 16) |
|---|---|---|---|---|
| `d3-quadtree.min.js` | d3-quadtree | 3.0.1 | 5279 | `57e2ad12824ed828` |
| `d3-dispatch.min.js` | d3-dispatch | 3.0.1 | 1901 | `94b3bbdb6b98dc13` |
| `d3-timer.min.js` | d3-timer | 3.0.1 | 1947 | `911ceda305f014b6` |
| `d3-force.min.js` | d3-force | 3.0.0 | 8300 | `1e07b473241328795` |

Total 17,427 bytes. **Load order is required** (quadtree, dispatch, timer, force): each UMD attaches
to `window.d3`, and `d3-force` reads the other three off that object at define time. `build.py`'s
`VENDOR_FILES` list encodes this order; do not reorder it.

## Licence

All four are **ISC, Copyright 2010-2021 Mike Bostock** (`LICENSE-d3.txt`, byte-identical across the
four upstream repos). The ISC licence requires the copyright notice to appear in all copies, so:

- each minified file keeps its `// https://d3js.org/... Copyright 2010-2021 Mike Bostock` banner;
- `build.py` **refuses the build** if a vendor file no longer contains the word `Copyright`.

That makes the obligation a toolchain gate rather than something anyone has to remember.

## Vetting (Rule 20, performed 2026-08-29 before the files entered the repo)

Source: `https://cdn.jsdelivr.net/npm/<pkg>@<version>/dist/<pkg>.min.js`, each byte-compared against
the same path on `unpkg.com` — all four **identical across both hosts**.

| check | result |
|---|---|
| network / workers (`fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, `sendBeacon`, `importScripts`, `Worker`, `postMessage`) | **0 hits, all four files** |
| dynamic code (`eval`, `new Function`, `document.write`) | **0 hits** |
| DOM / storage / ambient (`document.*`, `localStorage`, `sessionStorage`, `indexedDB`, `cookie`, `location`, `parent.`) | **0 hits** |
| entire browser-global surface | `window.requestAnimationFrame` only, twice, both in d3-timer |
| prototype pollution (`__proto__`, `Object.prototype.x=`, `globalThis.x=`, `window.x=`) | **0 hits** |
| globals written | `d3` only (`(...).d3 = x.d3 \|\| {}` in each UMD preamble) |
| export surface | `quadtree` · `dispatch` · `now`/`timer`/`timerFlush`/`timeout`/`interval` · `forceSimulation`/`forceManyBody`/`forceLink`/`forceCollide`/`forceCenter`/`forceRadial`/`forceX`/`forceY` — nothing else |
| literal `</script` | **0 hits** (also enforced at build time) |
| non-ASCII bytes | **0** |
| full read | `d3-timer.min.js` read end to end (it is the only file touching ambient APIs): a pure scheduler over `performance.now`/`Date` and rAF with a `setTimeout`/`setInterval` fallback. No I/O of any kind. |

**Verdict: PASS.** These are pure computation modules. Note the vetting is load-bearing rather than
ceremonial: the published artifact's CSP would block a phone-home, but the local preview server on
`:8137` would not.

Runtime note: the app calls `simulation.stop()` immediately and drives `.tick()` from its own
`requestAnimationFrame` loop, so d3-timer's scheduler never starts. That is deliberate — a d3-owned
timer would keep running after the SPA swaps routes, since the simulation holds no DOM reference.

## Re-vendoring

Changing a version is a decision, not a refresh. Re-run every check above, update this table's
versions and hashes, and record the change in `data/ledger.md`.
