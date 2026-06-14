# dbsim — Deutsche Bahn Simulation

A headless, event-driven simulation of the German rail network, built as a **study
tool** for investigating network-wide **delay propagation**, capacity bottlenecks,
and dispatching strategies.

> This is a research/analysis tool, not a renderer or game. The engine runs headless
> and emits a recording; visualization reads the recording afterward.

See [`plan/db-sim-implementation-plan.md`](plan/db-sim-implementation-plan.md) for the
full multi-phase plan. The leading research question is **delay propagation**.

## Core principles

- **Deterministic per seed** — same inputs + same seed → byte-identical run. Tested in CI.
- **Headless core + recording** — no live streaming/IPC.
- **Multi-scale** — macroscopic nationally, microscopic for one curated zone.
- **Swappable dispatcher** — conflict resolution is a pluggable interface.
- **Event-driven, not tick-based** — a `heapq` event loop.

## Getting started

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
uv sync                 # create the venv and install dev deps
uv run dbsim sim        # run the hello-world sim loop (prints a run hash)
uv run pytest           # run the test suite (incl. determinism harness)
uv run ruff check .     # lint
uv run ruff format --check .
uv run mypy             # type-check
```

### Ingest the timetable (M0.2)

```bash
# Download the gtfs.de long-distance (ICE/IC) feed and load it into DuckDB.
uv run dbsim ingest --feed fv

# "All trains through Frankfurt Hbf on a given Tuesday":
uv run dbsim query trains "Frankfurt(Main)Hbf" --date 20260616 \
    --db data/processed/gtfs-fv.duckdb

# Reconstruct a specific train's full scheduled stop sequence:
uv run dbsim query trip 124021 --db data/processed/gtfs-fv.duckdb
```

### Build the timetable graph & plan journeys (M0.3)

```bash
# Graph statistics (event nodes/edges, stations, connectivity) for a date:
uv run dbsim graph --date 20260616 --db data/processed/gtfs-fv.duckdb

# Earliest-arrival journey by scheduled time (includes transfers):
uv run dbsim route "Frankfurt(Main)Hbf" "München Hbf" \
    --date 20260616 --depart-after 08:00 --db data/processed/gtfs-fv.duckdb
```

### Bildfahrplan — time–distance diagram (M0.4)

```bash
# Render the scheduled train graph for a corridor (default: Frankfurt–Hannover):
uv run dbsim bildfahrplan --date 20260616 \
    --db data/processed/gtfs-fv.duckdb --out viz/bildfahrplan.png
```

### Simulate a day (M1.1)

```bash
# Drive trains through the timetable (corridor by default; --all for the nation).
# Unperturbed, simulated times reproduce the schedule exactly and deterministically.
uv run dbsim run --date 20260616 --db data/processed/gtfs-fv.duckdb
uv run dbsim run --date 20260616 --db data/processed/gtfs-fv.duckdb --all

# Inject a primary delay (TRIP:SEQ:SECONDS) and watch it cascade (M1.2):
uv run dbsim run --date 20260616 --db data/processed/gtfs-fv.duckdb \
    --delay 124021:0:1200   # ICE 22 +20 min at Frankfurt
```

### Record & replay (M1.3)

```bash
# Emit a Parquet recording of a run, then read positions back:
uv run dbsim run --date 20260616 --db data/processed/gtfs-fv.duckdb --all \
    --record data/processed/run.parquet
uv run dbsim replay data/processed/run.parquet --at 08:00 --db data/processed/gtfs-fv.duckdb
```

See `notebooks/replay.ipynb` for a worked replay (activity curve, trajectories, a map snapshot).

### Validate against GTFS-RT (M1.4)

```bash
# Capture a live real-time snapshot, then validate the model against it.
# (Validation needs the FULL static feed — RT trip_ids match `free`, not `fv`.)
uv run dbsim rt-capture data/raw/gtfsrt/today --count 1
uv run dbsim ingest --feed free            # one-time, downloads ~268 MB
uv run dbsim validate data/raw/gtfsrt/today/snapshot-*.pb \
    --feed data/raw/gtfs/gtfsde-free/<date>/feed.zip --date <YYYYMMDD> \
    --scatter viz/validation.png
```

The methodology and results are written up in [`docs/validation-report.md`](docs/validation-report.md).

### Track-segment model from OSM (M2.1)

```bash
# Classify single- vs double-track per station-to-station segment from OpenStreetMap.
uv run dbsim segments --db data/processed/gtfs-free.duckdb \
    --stations "Tübingen Hbf;Unterjesingen Mitte;Entringen;Herrenberg"

# Mesoscopic meet: two opposing trains contend for single-track segments (M2.2/M2.3).
uv run dbsim meso --db data/processed/gtfs-free.duckdb \
    --stations "Tübingen Hbf;Unterjesingen Mitte;Entringen;Herrenberg"

# Dispatch a line closure (M2.4): swap policy, close a segment over a time window.
uv run dbsim meso --db data/processed/gtfs-free.duckdb \
    --stations "Tübingen Hbf;Unterjesingen Mitte;Entringen;Herrenberg" \
    --dispatcher priority --close 1:0:1800

# Run a declarative disruption scenario from a file (M2.5).
uv run dbsim scenario scenarios/ammertal-closure.json --db data/processed/gtfs-free.duckdb

# UIC 406 capacity utilisation per segment + bottleneck (M2.6).
uv run dbsim capacity --db data/processed/gtfs-free.duckdb --date 20260616 \
    --stations "Tübingen Hbf;Unterjesingen Mitte;Entringen;Herrenberg"
```

Data is **not** committed (see [`docs/data-versioning.md`](docs/data-versioning.md));
only a small `source.json` manifest pins each download.

## Project status

Phase 0 in progress:

- **M0.1 — skeleton & determinism harness** ✅ — deterministic event loop + run hashing.
- **M0.2 — GTFS ingestion** ✅ — gtfs.de feed → canonical DuckDB tables; station/trip queries.
- **M0.3 — macroscopic timetable graph** ✅ — `rustworkx` time-expanded graph; earliest-arrival routing.
- **M0.4 — first Bildfahrplan** ✅ — corridor time–distance diagram (matplotlib).

**Phase 0 complete.**

Phase 1 in progress:

- **M1.1 — event-driven core engine** ✅ — `MacroSimulation` reproduces the timetable exactly, deterministically.
- **M1.2 — delay model & propagation** ✅ — primary delays, dwell recovery, connection holding; no acausal effects.
- **M1.3 — recording format & replay** ✅ — self-describing Parquet recording; position reconstruction.
- **M1.4 — ⭐ validation against GTFS-RT** ✅ — sim vs observed delays correlate (r≈0.47); see [`docs/validation-report.md`](docs/validation-report.md).
- **M1.5 — scale to national macro + profile** ✅ — national rail day in ~8 s; **Rust port not needed** ([`docs/performance-profile.md`](docs/performance-profile.md)).

**Phase 1 complete.**

Phase 2 in progress:

- **M2.1 — track-segment model from OSM** ✅ — single/double-track per segment via cross-section counting.
- **M2.2 — running-time & headway model** ✅ — segment occupancy as a contended resource; single-track meets resolve at stations.
- **M2.3 — conflict detection** ✅ — blocking-time over-saturation detection; `dbsim meso` reports conflicts before dispatching.
- **M2.4 — priority-based dispatcher (v1)** ✅ — swappable `Dispatcher` interface; line closures held conflict-free.
- **M2.5 — disruption scenario format** ✅ — declarative JSON scenarios (closures, speed restrictions).
- **M2.6 — UIC 406 capacity analysis** ✅ — blocking-time compression; per-segment occupancy + bottleneck.

**Phase 2 complete.**

Phase 3 in progress:

- **M3.1.0 — zone-coverage survey** ✅ — chose the micro zone from OSM coverage evidence ([`docs/zone-survey.md`](docs/zone-survey.md)): the Ammertalbahn Pfäffingen passing loop.
- **M3.1 — curate the zone** ✅ — validated micro-infrastructure `MicroZone` (blocks, routes, signals, switches) from real OSM.
- **M3.2 — microscopic movement & blocking-time** ✅ — speed profiles + blocking-time stairways; min headway.
- **M3.3 — deadlock avoidance** ✅ — opposing trains meet at the loop without deadlock (`dbsim meet`).
- **M3.4 — macro–micro coupling** ✅ — micro zone embedded in the macro schedule; micro contention propagates to macro (`dbsim couple`).
- **M3.5 — micro-validation harness** ✅ — zone consistent with the operated timetable ([`docs/micro-validation.md`](docs/micro-validation.md)).

**Phase 3 complete.** Remaining: Phase 4 (advanced rescheduling & robustness studies).

## Repository layout

```
src/dbsim/
├── seed.py        # central seed control (single source of randomness)
├── engine/        # event loop, events, movement   (← Rust later, if M1.5 demands)
├── record/        # recording + determinism hashing
├── ingest/        # GTFS, GTFS-RT, OSM ETL          (M0.2+)
├── model/         # infrastructure + timetable graph (M0.3+)
├── dispatch/      # swappable dispatchers            (Phase 2+)
├── scenario/      # disruption definitions           (Phase 2+)
└── analysis/      # metrics, validation, UIC 406     (Phase 1+)
data/              # raw + processed data (git-ignored; see docs/data-versioning.md)
viz/  notebooks/  tests/
```

## License

MIT
