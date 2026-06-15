# dbsim — Deutsche Bahn Simulation

[![CI](https://github.com/hlibstrochkovskyi/dbsim/actions/workflows/ci.yml/badge.svg)](https://github.com/hlibstrochkovskyi/dbsim/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A headless, event-driven simulation of the German railway network, built as a
research instrument for studying how delays propagate across a timetable, where
capacity bottlenecks form, and which dispatching strategies best contain
disruption. It is an analysis tool rather than a renderer or a game: the engine
runs without a user interface and emits a recording, and visualisation reads that
recording afterward.

The leading research question is **delay propagation** — given a primary delay,
where does it spread, how far, and how does the choice of dispatching rule change
the outcome.

## Design principles

- **Deterministic per seed.** A run is a pure function of its inputs and a single
  random seed; the same pair reproduces byte-for-byte. All randomness is routed
  through one seeded generator, and run hashes are compared in the test suite.
- **Headless core, separate visualisation.** The simulation has no live
  rendering or inter-process streaming. It produces a self-describing recording;
  diagrams and replays are derived from it.
- **Multi-scale.** The network is modelled at three levels of resolution —
  macroscopic (national timetable), mesoscopic (track segments and contention),
  and microscopic (one curated zone, signal by signal).
- **Swappable dispatching.** Conflict resolution is a pluggable interface, so
  competing strategies can be compared on identical instances.
- **Grounded in real data and theory.** Timetables, live delays, and track
  geometry come from public feeds; the models follow established railway
  operations theory (blocking-time stairways, UIC 406 capacity, the
  alternative-graph dispatching model).

## How it works

### Execution model

The core is a discrete-event simulation. A single priority queue (`heapq`) of
timestamped events advances the clock from one event to the next, rather than in
fixed time steps, so the cost of a run scales with the number of events rather
than with simulated duration. Every stochastic decision draws from one seeded
generator (`seed.py`), with independent sub-streams derived by hashing a label
into a child seed. This makes a run reproducible across processes and platforms.

### Data layer

Static timetables come from the gtfs.de GTFS feeds and are normalised into a
columnar DuckDB store. Live punctuality comes from the GTFS-RT `TripUpdate` feed.
Infrastructure geometry — track counts, signals, and switches — is extracted from
OpenStreetMap through the Overpass API. None of this data is committed to the
repository; a small `source.json` manifest pins each download (see
[`docs/data-versioning.md`](docs/data-versioning.md)).

### Macroscopic scale

The national timetable is compiled into a time-expanded graph in `rustworkx`:
nodes are (station, time) events and edges are train runs and feasible transfers.
Earliest-arrival journeys are shortest paths over this graph. Simulation drives
each train through its scheduled stops; the delay model injects primary delays,
recovers slack within dwell margins, and holds connecting services within
configured transfer and maximum-wait limits. Propagation is strictly causal — a
delay can never move an event earlier than its schedule. A full national service
day simulates in a few seconds, which kept the engine in Python (see
[`docs/performance-profile.md`](docs/performance-profile.md)).

Against live data, simulated downstream delays correlate with observed delays
(r ≈ 0.47 overall, higher on materially delayed trains), confirming the
propagation model is calibrated rather than merely plausible
([`docs/validation-report.md`](docs/validation-report.md)).

### Mesoscopic scale

Station-to-station segments are classified as single- or double-track by counting
parallel ways across OpenStreetMap cross-sections. Each segment is then a
capacity-constrained resource that trains acquire and release under a
minimum-headway constraint derived from blocking-time theory; opposing movements
on single track must therefore resolve their meet at a station. Oversubscription
is detected as a conflict before dispatching, and a pluggable `Dispatcher`
resolves the remaining contention. Capacity is quantified by UIC 406
blocking-time compression, yielding per-segment occupancy and the corridor
bottleneck.

### Microscopic scale

One zone — the Pfäffingen passing loop on the Ammertalbahn, chosen from a survey
of OpenStreetMap coverage ([`docs/zone-survey.md`](docs/zone-survey.md)) — is
modelled to the individual signal and switch. Blocking-time stairways are
computed from speed profiles via forward and backward passes over each route,
giving the minimum headway between successive movements. Opposing trains are
routed to a free loop track to avoid deadlock, and the zone's occupancy is
coupled back into the macroscopic schedule at its boundary, so microscopic
contention becomes macroscopic delay ([`docs/micro-validation.md`](docs/micro-validation.md)).

### Dispatching and optimisation

Track contention is formalised as an alternative graph (Mascis & Pacciarelli):
fixed arcs encode each train's route, and a disjunctive pair on every shared
resource encodes the ordering decision. Three solvers operate on the same graph:

- a **priority rule**, which always favours the higher-priority train;
- the **AMCC** heuristic (Avoid Most Critical Completion), which selects
  orderings greedily while staying cycle-free;
- a **CP-SAT** model (OR-Tools) that returns the minimum-makespan schedule.

The CP-SAT optimum is a lower bound against which the heuristic is measured, so
the cost of using a fast heuristic instead of an exact solver can be quantified
exactly.

### Robustness analysis

A Monte Carlo harness calibrates a primary-delay model from the empirical GTFS-RT
delay distribution — the probability a train starts late and a non-parametric
bootstrap pool of how late — then runs N reproducible replications, each with an
independent seed derived from a base seed. It reports the outcome distribution:
delay percentiles across simulated days and the stations that most consistently
accumulate delay (the network's fragile points). The same machinery drives the
strategy comparison, evaluating the three dispatchers across a common ensemble of
disruptions.

## Installation

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer.

```bash
uv sync                      # create the virtual environment and install deps
uv run dbsim sim             # smoke test: a deterministic event-loop run hash
uv run pytest                # test suite, including the determinism harness
uv run ruff check .          # lint
uv run ruff format --check . # formatting
uv run mypy                  # static type checking
```

## Usage

The tool is a single command, `dbsim`, with subcommands. Each accepts
`--help`. The examples below assume a timetable has been ingested.

### Timetable ingestion and queries

```bash
# Download a gtfs.de feed and load it into DuckDB.
# `fv` is the long-distance (ICE/IC) network; `free` is the full national feed.
uv run dbsim ingest --feed fv

# All trains calling at a station on a given date.
uv run dbsim query trains "Frankfurt(Main)Hbf" --date 20260616 \
    --db data/processed/gtfs-fv.duckdb

# The full scheduled stop sequence of a single trip.
uv run dbsim query trip 124021 --db data/processed/gtfs-fv.duckdb
```

### Journey planning and the time–distance diagram

```bash
# Time-expanded graph statistics (event nodes/edges, stations, connectivity).
uv run dbsim graph --date 20260616 --db data/processed/gtfs-fv.duckdb

# Earliest-arrival journey, including transfers.
uv run dbsim route "Frankfurt(Main)Hbf" "München Hbf" \
    --date 20260616 --depart-after 08:00 --db data/processed/gtfs-fv.duckdb

# Bildfahrplan: the scheduled time–distance diagram for a corridor.
uv run dbsim bildfahrplan --date 20260616 \
    --db data/processed/gtfs-fv.duckdb --out viz/bildfahrplan.png
```

### Macroscopic simulation and delay propagation

```bash
# Simulate a corridor (default) or the whole network (--all). Unperturbed, the
# simulation reproduces the published timetable exactly and deterministically.
uv run dbsim run --date 20260616 --db data/processed/gtfs-fv.duckdb
uv run dbsim run --date 20260616 --db data/processed/gtfs-fv.duckdb --all

# Inject a primary delay (TRIP:SEQ:SECONDS) and observe the cascade.
uv run dbsim run --date 20260616 --db data/processed/gtfs-fv.duckdb \
    --delay 124021:0:1200          # ICE 22, +20 min at Frankfurt
```

### Recording and replay

```bash
# Emit a self-describing Parquet recording, then reconstruct positions from it.
uv run dbsim run --date 20260616 --db data/processed/gtfs-fv.duckdb --all \
    --record data/processed/run.parquet
uv run dbsim replay data/processed/run.parquet --at 08:00 \
    --db data/processed/gtfs-fv.duckdb
```

`notebooks/replay.ipynb` works through a replay end to end (activity curve,
trajectories, and a map snapshot).

### Validation against real-time data

```bash
# Capture a live snapshot, then compare the model against observed delays.
# Validation uses the full feed, since RT trip ids match `free`, not `fv`.
uv run dbsim rt-capture data/raw/gtfsrt/today --count 1
uv run dbsim ingest --feed free
uv run dbsim validate data/raw/gtfsrt/today/snapshot-*.pb \
    --feed data/raw/gtfs/gtfsde-free/<date>/feed.zip --date <YYYYMMDD> \
    --scatter viz/validation.png
```

### Track-segment model and capacity

```bash
# Classify single- vs double-track per segment from OpenStreetMap.
uv run dbsim segments --db data/processed/gtfs-free.duckdb \
    --stations "Tübingen Hbf;Unterjesingen Mitte;Entringen;Herrenberg"

# Mesoscopic meet: opposing trains contend for single-track segments.
uv run dbsim meso --db data/processed/gtfs-free.duckdb \
    --stations "Tübingen Hbf;Unterjesingen Mitte;Entringen;Herrenberg"

# Dispatch a line closure: choose a policy, close a segment over a time window.
uv run dbsim meso --db data/processed/gtfs-free.duckdb \
    --stations "Tübingen Hbf;Unterjesingen Mitte;Entringen;Herrenberg" \
    --dispatcher priority --close 1:0:1800

# Run a declarative disruption scenario from a file.
uv run dbsim scenario scenarios/ammertal-closure.json \
    --db data/processed/gtfs-free.duckdb

# UIC 406 capacity utilisation per segment, with the bottleneck.
uv run dbsim capacity --db data/processed/gtfs-free.duckdb --date 20260616 \
    --stations "Tübingen Hbf;Unterjesingen Mitte;Entringen;Herrenberg"
```

### Microscopic zone

```bash
# Opposing trains meet at the loop without deadlock.
uv run dbsim meet

# Embed the micro zone in the macroscopic schedule and propagate its contention.
uv run dbsim couple --date 20260616 --db data/processed/gtfs-free.duckdb
```

### Dispatching: heuristic versus optimal

```bash
# Priority rule vs AMCC vs the CP-SAT optimum on a delayed meet.
uv run dbsim reschedule --delay 1000

# Optimal-vs-heuristic on a harder three-train instance, with the gap quantified.
uv run dbsim optimal
```

### Monte Carlo robustness

```bash
# Calibrate the delay model from a GTFS-RT snapshot, then run N replications.
# Reports total-delay percentiles and the network's fragility hotspots.
uv run dbsim montecarlo --db data/processed/gtfs-fv.duckdb --date 20260616 \
    --reps 500 --snapshot data/raw/gtfsrt/<date>/snapshot-*.pb

# Without a snapshot, supply a manual model.
uv run dbsim montecarlo --db data/processed/gtfs-fv.duckdb --date 20260616 \
    --reps 200 --p-delayed 0.3 --mean-delay 300
```

Methodology and results: [`docs/robustness-study.md`](docs/robustness-study.md).

### Strategy comparison

This compares all three dispatchers across a Monte Carlo ensemble of disruptions
on a contended single-track corridor. It needs no downloaded data to run with a
manual disruption model:

```bash
uv run dbsim study --reps 300 --p-delayed 0.4 --mean-delay 600
```

```
  strategy       mean      p50      p90      max   (clearance delay, min)
  priority       99.5    101.7    110.0    111.7
  amcc           38.1     38.3     45.0     51.7
  optimal        38.0     38.3     45.0     51.7
```

Each figure is the extra time, in minutes, needed to clear every train from the
corridor relative to free running. Alternative-graph dispatching roughly halves
that delay against the priority rule across the whole distribution, and the AMCC
heuristic essentially matches the CP-SAT optimum. To calibrate the disruptions
from real data instead, pass a snapshot:

```bash
uv run dbsim study --reps 1000 --snapshot data/raw/gtfsrt/<date>/snapshot-*.pb
```

The full write-up is [`docs/strategy-comparison-study.md`](docs/strategy-comparison-study.md).

## Repository layout

```
src/dbsim/
├── seed.py        # central seed control (the single source of randomness)
├── engine/        # discrete-event loop, events, train movement
├── record/        # recording format and determinism hashing
├── ingest/        # GTFS, GTFS-RT, and OSM extraction
├── model/         # infrastructure and the timetable graph
├── dispatch/      # swappable dispatchers and the optimisation solvers
├── scenario/      # declarative disruption definitions
└── analysis/      # metrics, validation, capacity, robustness studies
data/              # raw and processed data (git-ignored)
docs/  viz/  notebooks/  tests/
```

## License

MIT
