# Deutsche Bahn Simulation — Implementation Plan

A multi-scale, event-driven railway simulation built as a **study tool** for inspecting the German rail network: network-wide delay propagation, capacity bottlenecks, disruption response, and timetable robustness.

---

## How to use this plan

- **Work top-down.** Each phase produces a tool that *runs and is validated* on its own. Don't start Phase 2 until Phase 1 is green.
- **Every milestone ends runnable.** A milestone is done only when its acceptance criterion passes — not when the code "mostly works."
- **Validation is not optional.** Milestone 1.4 (validation against real data) is the make-or-break of the whole project. If you skip it, you have a sandbox, not a study tool.
- **The Rust port is a gated decision, not a default.** It only happens if profiling at M1.5 says Python is too slow. Don't pre-optimize.
- Check boxes as you go: `- [x]`.

---

## Guiding principles (non-negotiable)

1. **Deterministic per seed.** Same inputs + same seed → byte-identical run. This is required for debugging and for fair A/B comparison of strategies. Stochasticity comes only from running *many* seeded replications.
2. **Headless core + recording.** The engine never renders live. It consumes (infrastructure + timetable + scenario + dispatcher policy) and emits a recording file. Visualization reads the recording afterward. This deletes the entire "live IPC / streaming" problem.
3. **Multi-scale on purpose.** Macroscopic everywhere (data is open and national); microscopic only for one hand-curated zone (signal-level data is not free nationally). The two are coupled at the zone boundary.
4. **Swappable dispatcher.** The conflict-resolution / rescheduling logic is a pluggable module so you can compare strategies — that comparison is one of the tool's main research outputs.
5. **Grounded in the real frameworks.** Blocking-time theory, UIC 406 capacity, the alternative-graph dispatching model. Read Pachl, *Railway Operation and Control* and Hansen & Pachl, *Railway Timetabling & Operations*.

---

## Tech stack (single source of truth)

| Layer | Primary tools | Notes |
|---|---|---|
| Language (v1) | **Python 3.12+** | Correctness + ecosystem first |
| Env / packaging | `uv` (or poetry) | Reproducible envs |
| Testing | `pytest` + a determinism harness | Hash-compare runs |
| GTFS parsing | `gtfs-kit` / `partridge` | Static timetable |
| Storage / query | **DuckDB** + **Parquet** | Analytics on big tables; recordings |
| Geometry / OSM | `pyrosm` / `osmnx`, `shapely`, `geopandas`, `pyproj` | Track topology |
| Graph | `networkx` (proto) → **`rustworkx`** | Rust-backed graph algos, Python API |
| DES core | hand-rolled `heapq` event loop (or SimPy) | Event-driven |
| Optimization | **OR-Tools** (CP-SAT), **HiGHS** / PuLP / Pyomo | Dispatching, MILP baselines |
| Analysis / data | `polars` / pandas, Jupyter | Experiments |
| Analytical viz | matplotlib / plotly | Bildfahrplan, blocking-time stairways |
| Spatial viz | **MapLibre GL** + **deck.gl** (via `pydeck`), or `kepler.gl` | Replay over a real map |
| Perf core (conditional) | **Rust + PyO3 / maturin** | Only if M1.5 profiling justifies it |

---

## Data sources

| Data | Source | Scale | Status |
|---|---|---|---|
| National timetable | DELFI via **gtfs.de** (GTFS) | Macro (all DE rail) | Open, current |
| Real-time delays | gtfs.de **GTFS-RT** | Macro | Open — used for validation |
| Station infrastructure | DB **OpenStation** (NeTEx/SIRI) | Meso | Open |
| Track topology, signals, switches | **OpenStreetMap / OpenRailwayMap** | Micro (per zone) | Open, community-mapped, uneven coverage |
| National infra register (ISR) | DB API Marketplace | Micro | **Paid + access-restricted** — not usable |

> **Fidelity ceiling:** macro nationally, micro only for a curated zone. Plan around this; don't fight it.

---

# Phase 0 — Foundation & Data

*Goal: be able to load and inspect Germany's timetable. The unglamorous bedrock — budget real time here.*

### M0.1 — Project skeleton & determinism harness · `S` ✅
- [x] Repo structure, `uv` env, `pytest`, CI, central seed control, data-versioning convention.
- [x] Empty engine loop that runs and is tested.
- **Deliverable:** a `hello-world` sim loop.
- **Acceptance:** `pytest` passes; running the empty engine twice with the same seed produces an identical output hash.

### M0.2 — GTFS ingestion · `M` ✅
- [x] Download DELFI GTFS (gtfs.de `fv` long-distance feed), load into DuckDB, build canonical timetable tables (agency, stops, routes, trips, stop_times, calendar, calendar_dates).
- **Deliverable:** query "all trains through Frankfurt Hbf on a given Tuesday." → `dbsim query trains "Frankfurt(Main)Hbf" --date 20260616` (253 ICE/IC trains).
- **Acceptance:** can reconstruct a specific train's full scheduled stop sequence + times (e.g. ICE 22 trip 124021, Frankfurt→Hamburg→north); row counts sanity-checked against the feed. ✅
- *Notes:* used DuckDB-native CSV ingestion (not `gtfs-kit`) — lighter & deterministic. Loader is feed-agnostic; swap to the full national feed via `--feed free` at M1.5. GTFS clock times parsed to seconds-since-midnight (handles >24:00:00).

### M0.3 — Macroscopic timetable graph · `M` ✅
- [x] Build the network graph with `rustworkx`: a **time-expanded** graph (arrival/departure events = nodes; ride/dwell/station-timeline edges) for routing, plus a station-level graph (stations = nodes) for connectivity stats.
- **Deliverable:** `TimetableGraph` object + `dbsim graph` stats (event nodes/edges, stations, weakly-connected components). On the fv feed for a Tuesday: 500 stations, 497 in the largest component.
- **Acceptance:** `dbsim route` does shortest-path-by-scheduled-time (earliest arrival, incl. transfers) → plausible itineraries: Frankfurt→Hamburg picks the **direct ICE 22 06:46** (matches the M0.2-reconstructed schedule); Frankfurt→München ~3h32 (1 transfer); Frankfurt→Berlin ~4h14. ✅
- *Notes:* transfers currently allow any non-negative wait; a **minimum** connection time is deferred to M1.2. Some intermediate station names in the fv feed are generic ("Hauptbahnhof") — a data quirk, not a routing bug.

### M0.4 — First Bildfahrplan (time–distance diagram) · `S` ✅
- [x] Corridor = Frankfurt–Hannover ICE line (6 stations, 313 km); plotted the scheduled train graph with matplotlib. `dbsim bildfahrplan --date … --out …`.
- **Deliverable:** a static Bildfahrplan PNG — distance (cumulative great-circle) on y, time on x, trains coloured by direction; 159 trains on a Tuesday; overnight services render past 24:00.
- **Acceptance:** ICE 22 (trip 124021) corridor times match the published schedule **exactly** (Frankfurt 04:41 → Hanau 04:55/57 → Fulda 05:39/41 → Kassel 06:10/12 → Göttingen 06:30/32 → Hannover 07:05/09), consistent with the M0.2 trip reconstruction. ✅
- *Notes:* corridor is configurable via `--stations "A;B;C"`; output PNG is git-ignored (regenerable).

---

# Phase 1 — Macroscopic Simulation + Validation

*Goal: the first genuinely useful study tool — inject a delay, watch it cascade, compare to reality.*

### M1.1 — Event-driven core engine · `L` ✅
- [x] `MacroSimulation` drives trains through their scheduled stop sequences on the M0.1 event loop; event types `depart`/`arrive`; analytical movement at scheduled-running-time granularity. Running-time/dwell rules isolated as M1.2 extension points (`min_dwell_s` hook present). `dbsim run --date … [--all]`.
- **Deliverable:** simulate one day of one corridor (Frankfurt–Hannover), zero perturbation, reproducing the timetable. Also runs the full national macro day.
- **Acceptance:** with no perturbation, simulated times == scheduled times **exactly** (max deviation 0 s); deterministic across runs (identical run hash). ✅
  - Corridor: 159 trains, 3,148 events, 0 s deviation. Full national: 1,087 trains, 17,354 events, 0 s deviation, ~0.6 s wall-clock.

### M1.2 — Delay model & propagation · `M` ✅
- [x] Primary-delay injection (hold a trip at a stop by N s), dwell-time constraints **with recovery** (scheduled dwell slack absorbs delay down to `min_dwell`), and minimum connection/transfer holding (declared `Connection`: connector held to `feeder_arrival + min_transfer`, dropped past `sched_dep + max_wait`, via event-driven hold/release + deadline). `dbsim run --delay TRIP:SEQ:SECONDS`.
- **Deliverable:** inject "ICE 22 +20 min at Frankfurt," observe downstream + connection effects. ✅
- **Acceptance:** a primary delay produces plausible secondary delays; **no acausal effects** (zero negative deviations; the loop rejects scheduling in the past). ✅
  - On the real fv feed: ICE 22 +20 min decays along its route (+20 → +18 → +16 → … → +5 by Hamburg) as dwell slack recovers ~2 min/stop; a protected connector at Hanau is held +10 min for the late feeder.
- **⚠ Headway moved to M2.2.** Macro station-to-station headway without a track-segment/occupancy model is unprincipled; Phase 2 (M2.2) implements it correctly as a contended resource. The M1.2 acceptance is fully met without it.

### M1.3 — Recording format & replay · `S` ✅
- [x] A run emits a self-describing **Parquet** recording (one row per movement event; run metadata — schema version, date, seed, `run_hash` — embedded in Parquet KV metadata). `load_recording` reconstructs the movement stream and, for any train+time, its position (dwelling, or moving between two stops with an interpolation fraction; plus lat/lon interpolation). `dbsim run --record PATH`; `dbsim replay PATH [--at HH:MM]`.
- **Deliverable:** a recording file + `notebooks/replay.ipynb` (network-activity curve, a train's trajectory, a map-snapshot of interpolated positions). ✅
- **Acceptance:** the recording fully reconstructs the run and reloads to identical analysis — round-trip movement stream is byte-identical, `run_hash` and derived metrics match. ✅
  - On the real fv feed: a national run (17,354 events) records + replays; at 08:00, 241 trains' positions are reconstructed (e.g. "Mannheim Hbf → Stuttgart Hbf 71%").

### M1.4 — ⭐ Validation against GTFS-RT · `L`
*The milestone that makes this a study tool rather than a toy.*
- [ ] Ingest historical GTFS-RT for a chosen day; extract observed primary delays; feed them as inputs; compare simulated downstream delays vs observed.
- **Deliverable:** a validation report — mean/percentile delay error, sim-vs-observed scatter.
- **Acceptance:** simulated and observed downstream delays correlate meaningfully on a **held-out** day; the residual gap is quantified and discussed.

### M1.5 — Scale to national macro + profile · `M`
- [ ] Run the full DELFI timetable for a day; profile.
- **Deliverable:** full-network run completes; performance profile.
- **Acceptance:** a full day runs within a defined target (e.g. minutes); profiling pinpoints hotspots.
- **➡ Decision gate:** fast enough → skip Phase 1.5. Too slow → do M-Perf.

---

# Phase 1.5 — Performance Core (CONDITIONAL)

*Only if M1.5 profiling justifies it. A measured decision, not a flex.*

### M-Perf — Rust core via PyO3 · `L`
- [ ] Port the hot event loop / graph traversal to Rust; expose via PyO3 + maturin; keep Python for orchestration, experiments, viz.
- **Deliverable:** same results, faster.
- **Acceptance:** results numerically equivalent to the Python core; target speedup achieved.

---

# Phase 2 — Mesoscopic Capacity & Dispatching

*Goal: study capacity bottlenecks and disruption rerouting.*

### M2.1 — Track-segment model from OSM · `M`
- [ ] Extract line geometry, single- vs double-track, for the macro network / key corridors.
- **Deliverable:** segments with capacity attributes attached to the graph.
- **Acceptance:** known single-track lines correctly flagged; geometry plausible vs OpenRailwayMap.

### M2.2 — Running-time & headway model · `M`
- [ ] Per-segment traversal times, minimum headways; segment occupancy as a contended resource. *(Includes the macro→segment **headway** deferred from M1.2 — done here where the occupancy model makes it principled.)*
- **Deliverable:** trains contend for segment capacity.
- **Acceptance:** two trains cannot occupy a single-track segment in conflicting directions simultaneously.

### M2.3 — Conflict detection · `M`
- [ ] Time-window / blocking-time-based conflict detection at segment level.
- **Deliverable:** a conflict report for a run.
- **Acceptance:** injected over-saturation produces detected conflicts at the correct place/time.

### M2.4 — Priority-based dispatcher (v1) · `L`
- [ ] Define the **swappable dispatcher interface**; resolve conflicts by simple priority rules.
- **Deliverable:** disruptions resolved automatically; runs stay feasible.
- **Acceptance:** under a line closure, trains are held/rerouted and the schedule stays conflict-free; dispatcher is genuinely pluggable.

### M2.5 — Disruption scenario format · `S`
- [ ] Declarative scenarios (closures, speed restrictions, blocked segments).
- **Deliverable:** scenario files you can run and compare.
- **Acceptance:** a scenario file reproducibly produces the intended disruption.

### M2.6 — UIC 406 capacity analysis · `M`
- [ ] Blocking-time compression on a corridor to compute capacity utilization.
- **Deliverable:** a capacity-utilization report for a line.
- **Acceptance:** results are sane and interpretable (utilization %, bottleneck identified).

---

# Phase 3 — Microscopic Zone (research-grade, scoped)

*Goal: signal-level fidelity for one node/corridor. Could be a thesis topic on its own.*

### M3.1 — Pick & curate one zone · `L`
- [ ] Choose a zone (e.g. Frankfurt Hbf throat, or a single-track regional line where meets matter); hand-curate micro infrastructure from OSM/OpenRailwayMap (tracks, switches, signals, platform tracks, blocks).
- **Deliverable:** a validated micro-infrastructure model of the zone.
- **Acceptance:** track layout, signal/block positions match reality on inspection.

### M3.2 — Microscopic movement & blocking-time model · `L`
- [ ] Speed/acceleration profiles; block occupancy via blocking-time theory.
- **Deliverable:** micro-level runs through the zone with blocking-time stairways.
- **Acceptance:** the blocking-time stairway for a train sequence is correct and visualizable.

### M3.3 — Deadlock avoidance · `M`
- [ ] Time-window reservation / lookahead on single-track and critical resources.
- **Deliverable:** meets/passes resolved without deadlock.
- **Acceptance:** a constructed near-deadlock is avoided (trains meet correctly at a passing loop).

### M3.4 — Macro–micro coupling · `L`
- [ ] Hand off trains at the zone boundary (macro → micro arrivals, micro → macro departures).
- **Deliverable:** a run with the micro zone embedded in the national macro model.
- **Acceptance:** boundary hand-offs are time-consistent; the coupled run is deterministic.

---

# Phase 4 — Advanced Rescheduling & Robustness Studies

*Goal: the actual research payoff.*

### M4.1 — Alternative-graph dispatcher (v2) · `L`
- [ ] Implement the alternative-graph model + a heuristic (e.g. AMCC) for conflict resolution.
- **Deliverable:** a smarter dispatcher, comparable against v1.
- **Acceptance:** on the same disruption, v2 produces measurably different (ideally better) delay outcomes than v1.

### M4.2 — MILP / CP optimal baseline · `M`
- [ ] Formulate dispatching as MILP (HiGHS) or CP-SAT (OR-Tools) for a small zone.
- **Deliverable:** optimal-vs-heuristic comparison.
- **Acceptance:** solver returns feasible optimal schedules on small instances; gap to the heuristic is quantified.

### M4.3 — Monte Carlo robustness · `M`
- [ ] Sample primary-delay distributions (calibrated from GTFS-RT); run N seeded replications; analyze outcome distributions.
- **Deliverable:** a robustness study (delay percentiles, fragility hotspots).
- **Acceptance:** distributional results are stable; fragile points in the timetable are identified.

### M4.4 — ⭐ Strategy comparison study · `M`
*The tool's proof of value.*
- [ ] Compare dispatching strategies / timetable variants under disruption.
- **Deliverable:** a written study answering one sharp question (e.g. "how does a 20-min delay to ICE 599 at Frankfurt cascade over 3 hours, and which dispatching rule contains it best?").
- **Acceptance:** a reproducible study with clear conclusions.

---

## Suggested repository structure

```
db-sim/
├── pyproject.toml          # uv / deps
├── data/
│   ├── raw/                # GTFS, GTFS-RT, OSM extracts (git-ignored)
│   └── processed/          # DuckDB / Parquet canonical models
├── src/dbsim/
│   ├── ingest/             # GTFS, GTFS-RT, OSM ETL
│   ├── model/              # infrastructure + timetable graph
│   ├── engine/             # event loop, events, movement  (← Rust later)
│   ├── dispatch/           # swappable dispatchers (priority, alt-graph, MILP)
│   ├── scenario/           # disruption definitions
│   ├── record/             # recording read/write
│   └── analysis/           # metrics, validation, UIC 406
├── viz/                    # Bildfahrplan, stairways, MapLibre/deck.gl replay
├── notebooks/              # experiments & studies
└── tests/                  # incl. determinism + validation harness
```

---

## Effort & sequencing

T-shirt sizes at **part-time pace** (alongside thesis): `S` ≈ days · `M` ≈ 1–3 weeks · `L` ≈ 1–2 months. Rough — they depend entirely on your hours.

- **Near-term, clearly achievable:** Phases 0 → 1. This alone is a real, validated, demonstrable tool.
- **Long arc:** Phases 2 → 4. Phase 3 is research-grade and plausibly thesis-worthy in its own right.
- **Honest framing:** the full plan is a multi-year arc. The discipline that keeps it alive: every milestone ends in something that runs and is validated. The failure mode is treating it as one big push.

---

## Risk register

| Risk | Mitigation |
|---|---|
| Data ETL eats more time than expected | It's the bedrock — budget for it; don't rush to the engine |
| OSM micro coverage too sparse for chosen zone | Pick the zone *after* checking OpenRailwayMap coverage |
| Scope creep ("simulate all of DB at signal level") | Multi-scale by design; micro is one zone, full stop |
| Python too slow at national scale | Gated Rust port (M-Perf), only if M1.5 profiling demands it |
| Skipping validation | M1.4 is mandatory; without it the project has no scientific value |
| Non-determinism creeping in | Determinism test in CI from M0.1 onward |

---

## Decision log

- **Event-driven, not tick-based** — a railway follows a timetable; jump between events, interpolate for rendering only.
- **Python-first, Rust as a measured escape hatch** — correctness + OR ecosystem now; speed later only if proven necessary.
- **Multi-scale (macro national + one micro zone)** — dictated by the data ceiling, not preference.
- **Headless core + recording** — removes live-streaming/IPC complexity entirely.
- **Validation against GTFS-RT is the dividing line** between a toy and a study tool.

---

## Open questions (decide before / during Phase 0)

1. Which research question leads? (delay propagation · node capacity · disruption-response strategies)
2. Which micro zone? (depends on OSM coverage + the question above)
3. Which historical day(s) for validation? (pick days with known, interesting disruptions)
