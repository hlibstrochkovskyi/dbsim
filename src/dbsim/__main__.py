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

from dbsim.analysis import (
    DEFAULT_CORRIDOR,
    build_corridor,
    extract_train_paths,
    render_bildfahrplan,
    render_scatter,
    run_validation,
)
from dbsim.engine import Event, MacroSimulation, PrimaryDelay, Simulation, load_schedules
from dbsim.ingest import FEEDS, bbox_around, capture, download_feed, fetch_railways, load_feed
from dbsim.model import (
    Timetable,
    TimetableGraph,
    build_corridor_segments,
    format_hms,
)
from dbsim.record import hash_run, load_recording, write_recording
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
# `graph` / `route` — the macroscopic timetable graph (M0.3)
# ---------------------------------------------------------------------------


def _parse_clock(value: str) -> int:
    """Parse ``HH:MM`` or ``HH:MM:SS`` into seconds since midnight."""
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise argparse.ArgumentTypeError(f"invalid time {value!r}; expected HH:MM[:SS]")
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) == 3 else 0
    return h * 3600 + m * 60 + s


def _run_graph_stats(args: argparse.Namespace) -> None:
    with Timetable(args.db) as tt:
        stats = TimetableGraph(tt, args.date).stats()
    print(f"timetable graph for {stats.service_date}:")
    print(f"  event nodes        {stats.event_nodes:>10,}")
    print(f"  event edges        {stats.event_edges:>10,}")
    print(f"  stations           {stats.stations:>10,}")
    print(f"  station edges      {stats.station_edges:>10,}")
    print(f"  components (weak)  {stats.weakly_connected_components:>10,}")
    print(f"  largest component  {stats.largest_component_stations:>10,} stations")


def _run_route(args: argparse.Namespace) -> None:
    depart_after = _parse_clock(args.depart_after)
    with Timetable(args.db) as tt:
        journey = TimetableGraph(tt, args.date).plan_journey(args.origin, args.dest, depart_after)
    if journey is None:
        print(f"no journey from {args.origin!r} to {args.dest!r} after {args.depart_after}")
        return
    print(
        f"{args.origin} -> {args.dest}: depart {format_hms(journey.depart_time_s)}, "
        f"arrive {format_hms(journey.arrive_time_s)}, "
        f"{format_hms(journey.duration_s)} travel, {journey.n_transfers} transfer(s)"
    )
    for leg in journey.legs:
        line = leg.line or "?"
        print(
            f"  {line:<8} {format_hms(leg.board_time_s)} {leg.board_stop_name}"
            f"  ->  {format_hms(leg.alight_time_s)} {leg.alight_stop_name}"
        )


def _run_bildfahrplan(args: argparse.Namespace) -> None:
    names = (
        tuple(s.strip() for s in args.stations.split(";")) if args.stations else DEFAULT_CORRIDOR
    )
    with Timetable(args.db) as tt:
        corridor = build_corridor(tt, names)
        paths = extract_train_paths(tt, corridor, args.date)
    title = f"Bildfahrplan {names[0]} – {names[-1]} ({args.date})"
    render_bildfahrplan(corridor, paths, args.out, title=title)
    print(
        f"{len(paths)} trains over {corridor.length_km:.0f} km "
        f"({len(corridor.stations)} stations) -> {args.out}"
    )


# ---------------------------------------------------------------------------
# `run` — macroscopic train-movement simulation (M1.1)
# ---------------------------------------------------------------------------


def _parse_delay(value: str) -> PrimaryDelay:
    """Parse a ``TRIP:SEQ:SECONDS`` primary-delay spec."""
    parts = value.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(f"invalid --delay {value!r}; expected TRIP:SEQ:SECONDS")
    return PrimaryDelay(parts[0], int(parts[1]), int(parts[2]))


def _run_simulate(args: argparse.Namespace) -> None:
    names = (
        tuple(s.strip() for s in args.stations.split(";")) if args.stations else DEFAULT_CORRIDOR
    )
    delays = [_parse_delay(d) for d in args.delay or []]
    with Timetable(args.db) as tt:
        if args.all:
            scope = "national macro"
            trip_ids: set[str] | None = None
        else:
            corridor = build_corridor(tt, names)
            trip_ids = {p.trip_id for p in extract_train_paths(tt, corridor, args.date)}
            scope = f"corridor {names[0]} – {names[-1]}"
        schedules = load_schedules(tt, args.date, trip_ids)
        macro = MacroSimulation(
            schedules, seed=args.seed, min_dwell_s=args.min_dwell, primary_delays=delays
        )
        result = macro.run()

    print(f"simulated {scope} on {args.date}: {len(schedules)} trains")
    print(f"  movement events    {len(macro.records):>10,}")
    print(f"  primary delays     {len(delays):>10,}")
    print(f"  delayed events     {macro.delayed_event_count():>10,}")
    print(f"  max delay          {macro.max_abs_deviation_s():>10,} s")
    print(f"  total delay        {macro.total_delay_s():>10,} s")
    print(f"  reproduces sched.  {macro.reproduces_schedule()!s:>10}")
    if macro.worst_trains():
        print("  most-delayed trains:")
        for trip, delay in macro.worst_trains():
            print(f"    {trip:<12} +{delay // 60} min")
    run_hash = hash_run(result)
    print(f"  run_hash={run_hash}")
    if args.record is not None:
        write_recording(
            macro.records,
            args.record,
            service_date=int(args.date),
            seed=args.seed,
            run_hash=run_hash,
        )
        print(f"  recording -> {args.record}")


# ---------------------------------------------------------------------------
# `replay` — read a recording back (M1.3)
# ---------------------------------------------------------------------------


def _run_replay(args: argparse.Namespace) -> None:
    rec = load_recording(args.recording)
    print(f"recording {args.recording}")
    print(f"  service_date {rec.meta.service_date}  seed {rec.meta.seed}")
    print(f"  trains {len(rec.trips()):,}  events {rec.meta.n_events:,}")
    print(f"  run_hash {rec.meta.run_hash}")
    if args.at is None:
        return

    t = _parse_clock(args.at)
    names: dict[str, str] = {}
    if args.db is not None:
        with Timetable(args.db) as tt:
            rows = tt.connection.execute("SELECT stop_id, stop_name FROM stops").fetchall()
        names = {str(r[0]): str(r[1]) for r in rows}

    moving = [
        (trip, p)
        for trip in rec.trips()
        if (p := rec.position_at(trip, t)) is not None and not p.at_stop
    ]
    print(f"\nat {args.at}: {len(moving)} trains underway (showing up to 15):")
    for trip, p in moving[:15]:
        frm = names.get(p.from_stop_id, p.from_stop_id)
        to = names.get(p.to_stop_id, p.to_stop_id)
        print(f"  {trip:<10} {frm} -> {to}  ({p.fraction * 100:.0f}%)")


# ---------------------------------------------------------------------------
# `rt-capture` / `validate` — GTFS-RT (M1.4)
# ---------------------------------------------------------------------------


def _run_rt_capture(args: argparse.Namespace) -> None:
    paths = capture(args.out_dir, count=args.count, interval_s=args.interval)
    print(f"captured {len(paths)} snapshot(s) -> {args.out_dir}")
    for p in paths:
        print(f"  {p.name}  ({p.stat().st_size:,} bytes)")


def _run_validate(args: argparse.Namespace) -> None:
    report, pairs = run_validation(
        args.snapshot,
        args.feed,
        args.date,
        long_distance_only=args.long_distance,
        primary_threshold_s=args.primary_threshold,
    )
    scope = "long-distance" if args.long_distance else "all RT trips"
    print(f"GTFS-RT validation — {args.date} ({scope}):")
    print(f"  trips compared       {report.n_trips:>10,}")
    print(f"  held-out pairs       {report.n_pairs:>10,}  (realized downstream stops)")
    print(f"  MAE                  {report.mae_s / 60:>10.2f} min")
    print(f"  RMSE                 {report.rmse_s / 60:>10.2f} min")
    print(f"  bias (sim-obs)       {report.bias_s / 60:>10.2f} min")
    print(f"  correlation r        {report.correlation:>10.3f}")
    print(f"  delayed trains (|origin| >= {report.primary_threshold_s}s):")
    print(f"    pairs              {report.n_delayed_pairs:>10,}")
    print(f"    MAE                {report.mae_delayed_s / 60:>10.2f} min")
    print(
        f"    baseline MAE       {report.baseline_mae_delayed_s / 60:>10.2f} min  (constant delay)"
    )
    print(f"    correlation r      {report.correlation_delayed:>10.3f}")
    print(f"    beats baseline     {report.beats_baseline!s:>10}")
    if args.scatter is not None:
        render_scatter(pairs, report, args.scatter)
        print(f"  scatter -> {args.scatter}")


# ---------------------------------------------------------------------------
# `segments` — track-segment model from OSM (M2.1)
# ---------------------------------------------------------------------------


def _run_segments(args: argparse.Namespace) -> None:
    names = tuple(s.strip() for s in args.stations.split(";"))
    coords: list[tuple[str, float, float]] = []
    with Timetable(args.db) as tt:
        for name in names:
            row = tt.connection.execute(
                "SELECT stop_lat, stop_lon FROM stops "
                "WHERE stop_name = ? AND stop_lat IS NOT NULL LIMIT 1",
                [name],
            ).fetchone()
            if row is None:
                raise SystemExit(f"station not found in feed: {name!r}")
            coords.append((name, float(row[0]), float(row[1])))

    bbox = bbox_around([(la, lo) for _, la, lo in coords], margin_deg=0.02)
    ways = fetch_railways(bbox, cache_path=args.cache)
    segments = build_corridor_segments(coords, ways)
    print(f"corridor: {len(coords)} stations, {len(ways):,} OSM rail ways")
    for s in segments:
        kind = "single-track" if s.single_track else f"{s.tracks}-track"
        power = "elec" if s.electrified else "diesel"
        speed = f"{s.max_speed_kmh}km/h" if s.max_speed_kmh else "?"
        print(
            f"  {s.from_station} -> {s.to_station}: {kind}  "
            f"ref={s.line_ref}  {s.length_km:.1f}km  {speed}  {power}"
        )


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

    p_graph = sub.add_parser("graph", help="Timetable-graph statistics for a date.")
    p_graph.add_argument("--date", required=True, help="Service date as YYYYMMDD.")
    p_graph.add_argument("--db", type=Path, required=True, help="DuckDB path.")
    p_graph.set_defaults(func=_run_graph_stats)

    p_route = sub.add_parser("route", help="Earliest-arrival journey between two stations.")
    p_route.add_argument("origin", help='Origin station name, e.g. "Frankfurt(Main)Hbf".')
    p_route.add_argument("dest", help="Destination station name.")
    p_route.add_argument("--date", required=True, help="Service date as YYYYMMDD.")
    p_route.add_argument("--depart-after", default="00:00", help="Earliest departure HH:MM[:SS].")
    p_route.add_argument("--db", type=Path, required=True, help="DuckDB path.")
    p_route.set_defaults(func=_run_route)

    p_bild = sub.add_parser("bildfahrplan", help="Render a corridor time–distance diagram.")
    p_bild.add_argument("--date", required=True, help="Service date as YYYYMMDD.")
    p_bild.add_argument("--db", type=Path, required=True, help="DuckDB path.")
    p_bild.add_argument("--out", type=Path, default=Path("viz/bildfahrplan.png"), help="PNG path.")
    p_bild.add_argument(
        "--stations",
        default=None,
        help="Semicolon-separated ordered station names (default: Frankfurt–Hannover).",
    )
    p_bild.set_defaults(func=_run_bildfahrplan)

    p_run = sub.add_parser("run", help="Simulate train movement for a date (M1.1).")
    p_run.add_argument("--date", required=True, help="Service date as YYYYMMDD.")
    p_run.add_argument("--db", type=Path, required=True, help="DuckDB path.")
    p_run.add_argument("--all", action="store_true", help="Simulate all trains, not a corridor.")
    p_run.add_argument("--stations", default=None, help="Corridor names (semicolon-separated).")
    p_run.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Run seed.")
    p_run.add_argument("--min-dwell", type=int, default=0, help="Minimum dwell seconds.")
    p_run.add_argument(
        "--delay",
        action="append",
        metavar="TRIP:SEQ:SECONDS",
        help="Inject a primary delay (repeatable).",
    )
    p_run.add_argument("--record", type=Path, default=None, help="Write a Parquet recording.")
    p_run.set_defaults(func=_run_simulate)

    p_replay = sub.add_parser("replay", help="Read back a recording (M1.3).")
    p_replay.add_argument("recording", type=Path, help="Recording Parquet path.")
    p_replay.add_argument("--at", default=None, help="Show positions at HH:MM[:SS].")
    p_replay.add_argument("--db", type=Path, default=None, help="DuckDB for stop names.")
    p_replay.set_defaults(func=_run_replay)

    p_cap = sub.add_parser("rt-capture", help="Capture GTFS-RT snapshots (M1.4).")
    p_cap.add_argument("out_dir", type=Path, help="Directory to save .pb snapshots.")
    p_cap.add_argument("--count", type=int, default=1, help="Number of snapshots.")
    p_cap.add_argument("--interval", type=float, default=120.0, help="Seconds between polls.")
    p_cap.set_defaults(func=_run_rt_capture)

    p_val = sub.add_parser("validate", help="Validate the sim against GTFS-RT (M1.4).")
    p_val.add_argument("snapshot", type=Path, help="GTFS-RT .pb snapshot.")
    p_val.add_argument("--feed", type=Path, required=True, help="Full static feed zip.")
    p_val.add_argument("--date", type=int, required=True, help="Service date YYYYMMDD.")
    p_val.add_argument("--long-distance", action="store_true", help="Restrict to ICE/IC/EC.")
    p_val.add_argument(
        "--primary-threshold", type=int, default=120, help="Delayed-train cutoff (s)."
    )
    p_val.add_argument("--scatter", type=Path, default=None, help="Write a scatter PNG.")
    p_val.set_defaults(func=_run_validate)

    p_seg = sub.add_parser("segments", help="Track-segment model from OSM (M2.1).")
    p_seg.add_argument("--stations", required=True, help="Ordered station names (semicolon-sep).")
    p_seg.add_argument("--db", type=Path, required=True, help="DuckDB for station coordinates.")
    p_seg.add_argument("--cache", type=Path, default=None, help="Cache the Overpass JSON here.")
    p_seg.set_defaults(func=_run_segments)

    return parser


def main() -> None:
    """CLI entry point."""
    args = _build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
