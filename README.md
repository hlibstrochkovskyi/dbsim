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

Data is **not** committed (see [`docs/data-versioning.md`](docs/data-versioning.md));
only a small `source.json` manifest pins each download.

## Project status

Phase 0 in progress:

- **M0.1 — skeleton & determinism harness** ✅ — deterministic event loop + run hashing.
- **M0.2 — GTFS ingestion** ✅ — gtfs.de feed → canonical DuckDB tables; station/trip queries.
- **M0.3 — macroscopic timetable graph** ✅ — `rustworkx` time-expanded graph; earliest-arrival routing.
- **M0.4 — first Bildfahrplan** — next.

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
