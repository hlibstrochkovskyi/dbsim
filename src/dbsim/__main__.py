"""The ``dbsim`` command-line interface.

Subcommands:

- ``sim``    — run the hello-world deterministic sim loop (M0.1).
- ``ingest`` — download a GTFS feed and load it into a DuckDB database (M0.2).
- ``query``  — ask the loaded timetable the M0.2 deliverable questions.

Run ``uv run dbsim <subcommand> --help`` for details.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dbsim.engine import Event, Simulation
from dbsim.ingest import FEEDS, download_feed, load_feed
from dbsim.model import Timetable
from dbsim.record import hash_run
from dbsim.seed import DEFAULT_SEED

#: Number of "tick" events the demo chains together.
_DEMO_TICKS = 5


# ---------------------------------------------------------------------------
# `sim` — the M0.1 hello-world loop
# ---------------------------------------------------------------------------


def build_demo(*, seed: int = DEFAULT_SEED) -> Simulation:
    """Construct the hello-world simulation.

    A single ``tick`` handler chains the next tick at a random (but seeded)
    delay, so the run exercises the RNG, the priority queue, and handler-driven
    scheduling all at once. With a fixed seed the chain is fully reproducible.
    """
    sim = Simulation(seed=seed, max_time=1_000.0)

    def on_tick(sim: Simulation, event: Event) -> None:
        n = int(event.payload["n"])
        if n + 1 < _DEMO_TICKS:
            delay = sim.rng.uniform(1.0, 10.0)
            sim.schedule(Event(time=sim.now + delay, kind="tick", payload={"n": n + 1}))

    sim.on("tick", on_tick)
    sim.schedule(Event(time=0.0, kind="tick", payload={"n": 0}))
    return sim


def _run_sim(args: argparse.Namespace) -> None:
    result = build_demo(seed=args.seed).run()
    print(f"seed={result.seed} events={len(result.events)} end_time={result.end_time:.3f}")
    print(f"run_hash={hash_run(result)}")


# ---------------------------------------------------------------------------
# `ingest` — download + load a GTFS feed
# ---------------------------------------------------------------------------


def _default_db_path(feed: str) -> Path:
    return Path("data") / "processed" / f"gtfs-{feed}.duckdb"


def _run_ingest(args: argparse.Namespace) -> None:
    db_path = args.db or _default_db_path(args.feed)
    snapshot_dir = download_feed(
        args.feed, data_root=args.data_root, snapshot_date=args.snapshot_date
    )
    zip_path = snapshot_dir / "feed.zip"
    print(f"downloaded {args.feed} -> {zip_path}")
    load_feed(zip_path, db_path)
    print(f"loaded into {db_path}")
    with Timetable(db_path) as tt:
        for table, count in tt.table_counts().items():
            print(f"  {table:<16} {count:>9,}")


# ---------------------------------------------------------------------------
# `query` — ask the loaded timetable
# ---------------------------------------------------------------------------


def _run_query_trains(args: argparse.Namespace) -> None:
    with Timetable(args.db) as tt:
        calls = tt.trains_through_station(args.station, args.date)
    print(f"{len(calls)} trains through {args.station!r} on {args.date}:")
    for c in calls:
        line = c.route_short_name or "?"
        dep = c.departure_time or c.arrival_time or "--:--:--"
        print(f"  {dep}  {line:<6} trip={c.trip_id}  -> {c.trip_headsign or ''}")


def _run_query_trip(args: argparse.Namespace) -> None:
    with Timetable(args.db) as tt:
        calls = tt.trip_stop_sequence(args.trip_id)
    print(f"trip {args.trip_id}: {len(calls)} stops")
    for c in calls:
        arr = c.arrival_time or "--:--:--"
        dep = c.departure_time or "--:--:--"
        print(f"  {c.stop_sequence:>3}  {arr}/{dep}  {c.stop_name or c.stop_id}")


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dbsim", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_sim = sub.add_parser("sim", help="Run the hello-world deterministic sim loop.")
    p_sim.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Run seed.")
    p_sim.set_defaults(func=_run_sim)

    p_ingest = sub.add_parser("ingest", help="Download a GTFS feed and load it into DuckDB.")
    p_ingest.add_argument("--feed", choices=sorted(FEEDS), default="fv", help="Feed to ingest.")
    p_ingest.add_argument("--data-root", type=Path, default=Path("data"), help="Data directory.")
    p_ingest.add_argument("--db", type=Path, default=None, help="Output DuckDB path.")
    p_ingest.add_argument("--snapshot-date", default=None, help="YYYY-MM-DD snapshot label.")
    p_ingest.set_defaults(func=_run_ingest)

    p_query = sub.add_parser("query", help="Query the loaded timetable.")
    query_sub = p_query.add_subparsers(dest="query_command", required=True)

    p_trains = query_sub.add_parser("trains", help="Trains through a station on a date.")
    p_trains.add_argument("station", help='Station name, e.g. "Frankfurt(Main)Hbf".')
    p_trains.add_argument("--date", required=True, help="Service date as YYYYMMDD.")
    p_trains.add_argument("--db", type=Path, required=True, help="DuckDB path.")
    p_trains.set_defaults(func=_run_query_trains)

    p_trip = query_sub.add_parser("trip", help="Full stop sequence of a trip.")
    p_trip.add_argument("trip_id", help="GTFS trip_id.")
    p_trip.add_argument("--db", type=Path, required=True, help="DuckDB path.")
    p_trip.set_defaults(func=_run_query_trip)

    return parser


def main() -> None:
    """CLI entry point."""
    args = _build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
