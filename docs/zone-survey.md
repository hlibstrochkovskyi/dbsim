# M3.1.0 — Microscopic zone coverage survey

*Front-load the project's single biggest external-data risk: pick the micro zone
from **evidence of what OpenStreetMap actually maps**, not from which station is
famous.* This survey measures micro-feature coverage across candidate zones and
chooses the one that lets us **prove the microscopic machinery works**, cheaply.

## What was pulled (OSM / OpenRailwayMap)

Via Overpass (`fetch_railway_features`), the point features a signal/block model
needs: `railway=signal` (with `railway:signal:*` type + direction),
`railway=switch` (turnouts), `railway=buffer_stop`, and flat
`crossing`/`railway_crossing` nodes — plus `railway=rail` ways (from M2.1) for
track topology. Raw responses are cached under `data/raw/osm/` for reproducibility.

## Candidate zones — measured coverage

| Zone | switches | signals | signal direction known | role |
|---|---|---|---|---|
| **Ammertalbahn** (Tübingen–Herrenberg, single-track) | 97 | 247 | **99 %** | a whole line |
| **Pfäffingen** (one passing loop) | 4 | 15 | 93 % | minimal micro zone |
| Frankfurt(Main)Hbf throat | **267** | 377 | 96 % | a single big node |

Signals are richly mapped everywhere (direction known for ≳ 93 %); ~50 % carry a
*functional* type (main/distant/combined — the block boundaries), the rest being
speed/whistle/position markers.

## Passing-loop evidence (the analytic payoff)

On a single-track line, trains can only **meet** where there is a passing loop
(2 tracks + switches). Measured along the Ammertalbahn:

| Station | switches | parallel tracks | role |
|---|---|---|---|
| Tübingen Hbf | 7 | 13 | terminus |
| Unterjesingen Mitte | 1 | 1 | halt — no meet |
| **Pfäffingen** | 4 | 3 | **passing loop** |
| Entringen | 1 | 8 | halt |
| **Altingen (Württ)** | 3 | 3 | **passing loop** |
| Gültstein | 0 | 1 | halt |
| Herrenberg | 6 | 11 | terminus |

So meets are forced to Pfäffingen or Altingen — exactly where deadlock-avoidance
(M3.3) earns its keep.

## Decision criteria & verdict

1. **Signals + switches both well-mapped** (hard gate) — all candidates pass; the
   Ammertalbahn passes with near-complete signal direction.
2. **Passing loops cleanly represented** — yes, at Pfäffingen and Altingen.
3. **Tractable over brutal** — the Ammertalbahn has 97 switches over ~26 km; the
   Frankfurt throat alone has **267** in one node. A single loop has ~4 switches.
4. **Interesting operational question** — single-track meets/deadlock, the whole
   drama of the line.

**Chosen zone: the Ammertalbahn, scoped to one passing loop (Pfäffingen) plus the
single-track sections to its neighbouring loops.** It is well-mapped, minimal, and
the meet/deadlock question lives right there. The Frankfurt throat is **rejected**
as a first micro target — the data confirms it is well-mapped but ~3× denser and
computationally brutal; revisit it only after the machinery is proven on the loop.

## How Phase 3 proceeds from here

- **M3.1** — curate the Pfäffingen-loop micro-infrastructure from this OSM data
  (tracks, switches, signals/blocks, platform tracks), validated on inspection.
- **M3.2** — microscopic movement + blocking-time stairways, with a determinism
  test for the micro engine **from its first commit**.
- **M3.3** — deadlock avoidance (lookahead/reservation) at the loop.
- **M3.4** — macro–micro coupling, with a boundary-consistency test.
- **M3.5 (added)** — a **micro-validation harness** mirroring M1.4: does simulated
  zone throughput/timing match observed train counts on a real day? Built in, not
  bolted on — micro errors compound fast.

Beyond v1 (roadmap, not specified yet): stochastic microscopic operation → a
microscopic Monte Carlo → **multiple coupled micro zones** handing trains off
through the macro network (the research-grade backbone).
