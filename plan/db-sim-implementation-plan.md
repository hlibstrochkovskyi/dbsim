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

### M1.4 — ⭐ Validation against GTFS-RT · `L` ✅
*The milestone that makes this a study tool rather than a toy.*
- [x] Ingest GTFS-RT (`realtime.gtfs.de/realtime-free.pb`, protobuf); extract observed per-stop delays; feed each trip's **origin delay** as the only input; compare simulated downstream delays vs observed at **realized** (already-passed) stops. RT trip_ids match the **full** feed (not `fv`) — so the full feed is the static base.
- **Deliverable:** [`docs/validation-report.md`](validation-report.md) — mean/percentile delay error + sim-vs-observed scatter. `dbsim rt-capture`, `dbsim validate`.
- **Acceptance:** simulated and observed downstream delays correlate meaningfully (held-out by construction; downstream never an input); the residual gap is quantified and discussed. ✅
  - 2026-06-14: 122,937 held-out pairs, MAE 1.28 min, **r=0.47**; on delayed trains (≥2 min) the dwell-recovery model **beats** the constant-delay baseline (+2.6% overall, +12.9% long-distance). Under-prediction (negative bias) is the quantified motivation for Phase 2 (headway/conflicts).
- *Note:* one rich snapshot (realized past-stop delays); a full held-out day via forward `rt-capture` is the documented strengthening step.

### M1.5 — Scale to national macro + profile · `M` ✅
- [x] Ran the full `free` feed (35.2 M stop_times) for a day; profiled with cProfile. See [`docs/performance-profile.md`](performance-profile.md).
- **Deliverable:** full-network run completes; performance profile. ✅
- **Acceptance:** a full day runs within target (minutes) — **national rail day: ingest 22.6 s, load 11.4 s, simulate 7.9 s (896k events, 113k/s), 1.2 GB, reproduces schedule exactly**; hotspots = the pure-Python event loop + handlers. ✅
- **➡ Decision gate: SKIP Phase 1.5 (no Rust port).** Python is comfortably fast enough at the real scope (rail); research workloads parallelise across seeds; the only extreme-scale constraint is *memory* (records held in RAM), which Rust wouldn't fix — streaming to the recording would. Revisit only if a later milestone proves CPU-bound.

---

# Phase 1.5 — Performance Core (CONDITIONAL) — ⏭ SKIPPED

*Only if M1.5 profiling justifies it. A measured decision, not a flex.*

> **Decision (M1.5):** SKIPPED. The national rail day runs in ~8 s of simulation
> (~19 s end-to-end, 1.2 GB) — far inside target. Python stays. See
> [`docs/performance-profile.md`](performance-profile.md). Revisit only if a later
> milestone proves CPU-bound.

### M-Perf — Rust core via PyO3 · `L` *(not undertaken)*
- [ ] Port the hot event loop / graph traversal to Rust; expose via PyO3 + maturin; keep Python for orchestration, experiments, viz.
- **Deliverable:** same results, faster.
- **Acceptance:** results numerically equivalent to the Python core; target speedup achieved.

---

# Phase 2 — Mesoscopic Capacity & Dispatching

*Goal: study capacity bottlenecks and disruption rerouting.*

### M2.1 — Track-segment model from OSM · `M` ✅
- [x] Fetch `railway=rail` ways (geometry + tags) via Overpass; build station-to-station `Segment`s with **track count** (cross-section method — count parallel tracks on perpendiculars, since the `tracks` tag is rarely present), electrification, line speed; attach to a `segment_graph`. `dbsim segments`.
- **Deliverable:** segments with capacity attributes attached to the graph. ✅
- **Acceptance:** known single-track lines correctly flagged; geometry plausible vs OpenRailwayMap. ✅
  - Validated: the **Ammertalbahn** (Tübingen–Herrenberg, line 4633) flagged **single-track** on all 5 branch segments; the double-track Gäubahn (4860) and FFM–Hanau main line (3600) flagged 2-track. Lengths (1.5–5.5 km branch) and speeds (70–110 km/h) plausible.
- *Note:* classification is per line `ref` (so a double main line beside a separate S-Bahn reads as double for that line, not quad). Foundation for M2.2 headway/occupancy.

### M2.2 — Running-time & headway model · `M` ✅
- [x] `MesoSimulation`: each segment is a contended resource (capacity = tracks, minimum **headway**); trains acquire/queue/release segments and wait at stations when blocked. Running time = length / line speed (from M2.1). The **headway deferred from M1.2 is done here**, where the occupancy model makes it principled. `dbsim meso`.
- **Deliverable:** trains contend for segment capacity. ✅
- **Acceptance:** two trains cannot occupy a single-track segment in conflicting directions simultaneously. ✅
  - On the real **Ammertalbahn** (single-track): two opposing trains **meet at Entringen** — the second waits at the station for the segment to clear; no segment ever exceeds capacity. Double-track allows both directions at once.
- *Note:* headway is applied per segment entry (conservative for opposing moves on separate tracks); priority-then-FIFO contention. Conflict *detection report* is M2.3; *dispatcher* is M2.4; network-wide deadlock avoidance is M3.3.

### M2.3 — Conflict detection · `M` ✅
- [x] Blocking-time conflict detection at segment level: from trains' **planned** (uncontended) segment occupations, a *blocking interval* `[enter, exit + headway]` per train; a conflict is a maximal window where overlapping blocking intervals exceed segment capacity. Classified single-track meet vs over-capacity. `dbsim meso` reports planned conflicts before resolution.
- **Deliverable:** a conflict report for a run. ✅
- **Acceptance:** injected over-saturation produces detected conflicts at the correct place/time. ✅
  - Synthetic: 3 trains on a single-track segment → one over-capacity window [100,820], peak 3/1. Real Ammertalbahn: opposing trains → 2 single-track-meet conflicts at the Entringen-area segments (which M2.2 then resolves). No false positives on well-spaced trains.

### M2.4 — Priority-based dispatcher (v1) · `L` ✅
- [x] **Swappable `Dispatcher` interface** (`dispatch/`): `select(segment, waiting, now) -> train_id | None` decides which waiting train is admitted; the meso engine owns the mechanism (capacity/headway/closures), the dispatcher owns the policy. v1 = `PriorityDispatcher` (+ `FifoDispatcher` to prove pluggability). Line-closure disruptions (`Closure(segment, start, end)`); trains hold and reopen. `dbsim meso --dispatcher … --close SEG:START:END`.
- **Deliverable:** disruptions resolved automatically; runs stay feasible. ✅
- **Acceptance:** under a line closure, trains are held and the schedule stays conflict-free; dispatcher is genuinely pluggable. ✅
  - Real Ammertalbahn, segment closed [0,1800]: both trains held then complete (2/2), no over-capacity. Swapping priority↔fifo changes which train goes first after reopen (FWD vs BWD). Determinism preserved.
- *Note:* "held" only (corridor has no alternative path); rerouting needs network alternatives (the macro graph) — a later extension. Smarter strategies (alternative-graph M4.1, MILP/CP M4.2) implement the same interface.

### M2.5 — Disruption scenario format · `S` ✅
- [x] Declarative JSON `Scenario` (`scenario/`): corridor (station names + headway), trains (origin/dest/entry/priority), disruptions (segment **closures** + **speed restrictions** as running-time factors), dispatcher + seed. JSON round-trip; the runner resolves the corridor from OSM then applies the disruptions. `dbsim scenario FILE`. Example: [`scenarios/ammertal-closure.json`](../scenarios/ammertal-closure.json).
- **Deliverable:** scenario files you can run and compare. ✅
- **Acceptance:** a scenario file reproducibly produces the intended disruption. ✅
  - The example reproducibly: closes Pfäffingen–Entringen for 30 min (trains held), slows Tübingen–Unterjesingen ×1.5 (199 s → 298 s), runs two opposing RBs to completion conflict-free. Deterministic; round-trips through JSON.

### M2.6 — UIC 406 capacity analysis · `M` ✅
- [x] Blocking-time compression per segment: occupancy = n_trains × (running + headway) / capacity over the auto-detected **peak window**; train counts interpolated from the Bildfahrplan paths (M0.4), capacity/headway from OSM segments (M2.1). Reports occupancy % per segment, the bottleneck, and a UIC threshold flag. `dbsim capacity`.
- **Deliverable:** a capacity-utilization report for a line. ✅
- **Acceptance:** results are sane and interpretable (utilization %, bottleneck identified). ✅
  - Ammertalbahn (104 corridor trains, peak 06:19–07:19): bottleneck = Tübingen–Unterjesingen at **79.8%** (single-track, **over** the 75% UIC threshold); the double-track approach is only 10.9% (capacity halves occupancy).

---

# Phase 3 — Microscopic Zone (research-grade, scoped)

*Goal: signal-level fidelity for one node/corridor. Could be a thesis topic on its own.*

> **Discipline (carried from prior phases):** the micro layer gets the **same
> rigor from its first commit** — determinism test in M3.2, boundary-consistency
> test in M3.4, a micro-validation harness (M3.5). "Mostly working" is not done.

### M3.1.0 — Zone-coverage survey · `S` ✅
*De-risk the zone before curating it — front-load the project's biggest external-data unknown (OSM micro coverage).*
- [x] OSM micro-feature ingestion (`fetch_railway_features`: signals w/ type+direction, switches, buffer stops, crossings). Surveyed candidate zones; measured coverage + passing loops. See [`docs/zone-survey.md`](zone-survey.md).
- **Acceptance:** a zone chosen from measured evidence. ✅
  - **Chosen: Ammertalbahn, scoped to the Pfäffingen passing loop** (97 switches / 247 signals over the line; signal direction known for 99%; loops cleanly mapped at Pfäffingen & Altingen). Frankfurt throat **rejected** (267 switches in one node — well-mapped but brutal).

### M3.1 — Pick & curate one zone · `L` ✅
- [x] Curated `MicroZone` of the Pfäffingen loop (`model/micro.py`): blocks (2 single-track approaches + 2 loop tracks), routes (2 directions × 2 tracks), real OSM signals + switches. Topology hand-curated; loop length (289 m, switch-to-switch) and speeds (through 70, passing 50 km/h) grounded in OSM. `validate()` checks the passing-loop structure. `dbsim micro`.
- **Deliverable:** a validated micro-infrastructure model of the zone. ✅
- **Acceptance:** track layout, signal/block positions match reality on inspection. ✅
  - Built from real OSM: through track (70 km/h, **platform**) + passing track (50 km/h), 289 m loop, 15 signals, 4 switches — a Kreuzungsbahnhof matching the real Pfäffingen on inspection. `validate()` → valid passing loop.

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

### M3.5 — Micro-validation harness · `M` *(added)*
*Mirror M1.4 at micro grain — micro errors compound fast, so validate, don't trust.*
- [ ] Compare the simulated zone's throughput/occupancy/timings against observed train counts on a real day (from GTFS + GTFS-RT through the loop).
- **Deliverable:** a micro-validation report for the zone.
- **Acceptance:** simulated zone behaviour matches observed within a quantified, discussed gap.

> **Roadmap beyond v1** (shape to be decided by what M3.4 teaches, not over-specified now):
> stochastic microscopic operation (sampled dwell/acceleration, micro-grain primary delays)
> → microscopic Monte Carlo → **multiple coupled micro zones** handing trains off through
> the macro network (study how a disruption at one micro node re-materialises as congestion
> at a distant one — a question neither pure-macro nor single-zone-micro tools can answer).

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
