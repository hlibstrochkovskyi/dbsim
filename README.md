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
uv run dbsim            # run the hello-world sim loop (prints a run hash)
uv run pytest           # run the test suite (incl. determinism harness)
uv run ruff check .     # lint
uv run ruff format --check .
uv run mypy             # type-check
```

## Project status

Currently at **M0.1 — Project skeleton & determinism harness** (Phase 0).
The engine is an empty, deterministic event loop with a run-hashing harness; data
ingestion and the timetable graph come in M0.2+.

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
