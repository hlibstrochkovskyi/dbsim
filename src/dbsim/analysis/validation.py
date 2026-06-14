"""Validation against GTFS-RT (M1.4) — the make-or-break milestone.

Methodology (held-out by construction):

1. Parse an RT snapshot into observed per-stop delay profiles.
2. Build the scheduled stop sequences of those trips from the **full** static
   feed (RT trip_ids match `free`, not `fv`).
3. Feed **only each trip's origin delay** as a primary input and simulate.
4. Compare simulated vs observed delays at downstream stops the train has
   **already passed** at the snapshot time (realized ground truth — computed from
   the snapshot timestamp and the service day's local midnight). Downstream
   observations are never inputs.
5. Score against a naive baseline ("the delay stays constant at its origin
   value"); if the dwell-recovery model beats it, the propagation modelling adds
   value.

The output is a :class:`ValidationReport` (error stats + correlation) and a
sim-vs-observed scatter plot.
"""

from __future__ import annotations

import math
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from dbsim.engine import MacroSimulation, PrimaryDelay, ScheduledStop, TrainSchedule
from dbsim.ingest.gtfsrt import read_snapshot_file

_BERLIN = ZoneInfo("Europe/Berlin")

#: route_short_name prefixes considered long-distance.
LONG_DISTANCE_PREFIXES = ("ICE", "IC", "EC", "ECE", "RJ", "RJX", "NJ", "EN", "TGV", "FLX")


def _seconds_sql(col: str) -> str:
    """SQL converting a GTFS ``HH:MM:SS`` column to seconds (HH may exceed 24)."""
    return (
        f"CASE WHEN {col} IS NULL OR {col} = '' THEN NULL ELSE "
        f"TRY_CAST(split_part({col}, ':', 1) AS INTEGER) * 3600 + "
        f"TRY_CAST(split_part({col}, ':', 2) AS INTEGER) * 60 + "
        f"TRY_CAST(split_part({col}, ':', 3) AS INTEGER) END"
    )


def build_schedules_for_trips(full_feed_zip: Path, trip_ids: set[str]) -> list[TrainSchedule]:
    """Build scheduled stop sequences for ``trip_ids`` from the full feed zip."""
    if not trip_ids:
        return []
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(full_feed_zip) as zf:
        tmp_dir = Path(tmp)
        for name in ("trips.txt", "stop_times.txt", "routes.txt"):
            zf.extract(name, tmp_dir)
        st = (tmp_dir / "stop_times.txt").as_posix()
        trips = (tmp_dir / "trips.txt").as_posix()
        routes = (tmp_dir / "routes.txt").as_posix()

        con = duckdb.connect()
        try:
            con.execute("CREATE TABLE targets (trip_id VARCHAR)")
            con.executemany("INSERT INTO targets VALUES (?)", [(t,) for t in trip_ids])
            rows = con.execute(
                f"""
                SELECT st.trip_id, r.route_short_name,
                       TRY_CAST(st.stop_sequence AS INTEGER) AS seq, st.stop_id,
                       {_seconds_sql("st.arrival_time")} AS arr_s,
                       {_seconds_sql("st.departure_time")} AS dep_s
                FROM read_csv('{st}', header = true, all_varchar = true) st
                JOIN targets g ON st.trip_id = g.trip_id
                LEFT JOIN read_csv('{trips}', header = true, all_varchar = true) t
                       ON st.trip_id = t.trip_id
                LEFT JOIN read_csv('{routes}', header = true, all_varchar = true) r
                       ON t.route_id = r.route_id
                ORDER BY st.trip_id, seq
                """
            ).fetchall()
        finally:
            con.close()

    by_trip: dict[str, list[ScheduledStop]] = {}
    line_of: dict[str, str | None] = {}
    for trip_id, line, seq, stop_id, arr_s, dep_s in rows:
        line_of.setdefault(trip_id, line)
        by_trip.setdefault(trip_id, []).append(ScheduledStop(int(seq), str(stop_id), arr_s, dep_s))
    return [TrainSchedule(t, line_of[t], tuple(stops)) for t, stops in by_trip.items()]


@dataclass(frozen=True, slots=True)
class ValidationPair:
    """One observed-vs-simulated downstream delay comparison."""

    trip_id: str
    seq: int
    origin_delay_s: int
    observed_s: int
    simulated_s: int


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Aggregate validation result on a held-out set of downstream delays.

    Metrics are reported both over *all* comparisons and over the subset of
    trains that started meaningfully late (``|origin delay| >=
    primary_threshold_s``) — the cases where modelling propagation actually
    matters, and where the dwell-recovery model is tested against the naive
    "the delay stays constant" baseline.
    """

    service_date: int
    snapshot_ts: int
    n_trips: int
    long_distance_only: bool
    primary_threshold_s: int
    # All comparisons.
    n_pairs: int
    mae_s: float
    rmse_s: float
    bias_s: float
    correlation: float
    baseline_mae_s: float
    # Subset with a significant primary delay.
    n_delayed_pairs: int
    mae_delayed_s: float
    baseline_mae_delayed_s: float
    correlation_delayed: float

    @property
    def beats_baseline(self) -> bool:
        """Whether the model beats the constant-delay baseline on delayed trains."""
        return self.mae_delayed_s < self.baseline_mae_delayed_s


def _berlin_midnight_epoch(service_date: int) -> float:
    s = str(service_date)
    d = datetime(int(s[:4]), int(s[4:6]), int(s[6:8]), tzinfo=_BERLIN)
    return d.timestamp()


def _is_long_distance(line: str | None) -> bool:
    if not line:
        return False
    s = line.upper()
    return any(s.startswith(p) for p in LONG_DISTANCE_PREFIXES)


def run_validation(
    snapshot_path: Path,
    full_feed_zip: Path,
    service_date: int,
    *,
    long_distance_only: bool = False,
    primary_threshold_s: int = 120,
) -> tuple[ValidationReport, list[ValidationPair]]:
    """Run the GTFS-RT validation; return the report and the comparison pairs."""
    snapshot_ts, delays = read_snapshot_file(snapshot_path)

    # Observed arrival/departure delay per (trip, seq).
    obs_arr: dict[tuple[str, int], int] = {}
    obs_dep: dict[tuple[str, int], int] = {}
    trips_seen: set[str] = set()
    for d in delays:
        trips_seen.add(d.trip_id)
        if d.arrival_delay_s is not None:
            obs_arr[d.trip_id, d.stop_sequence] = d.arrival_delay_s
        if d.departure_delay_s is not None:
            obs_dep[d.trip_id, d.stop_sequence] = d.departure_delay_s

    schedules = build_schedules_for_trips(full_feed_zip, trips_seen)
    if long_distance_only:
        schedules = [s for s in schedules if _is_long_distance(s.line)]
    schedules = [s for s in schedules if s.stops]

    # Primary input = the observed delay at each trip's origin (first stop).
    primary: list[PrimaryDelay] = []
    origin_delay: dict[str, int] = {}
    for sched in schedules:
        origin = sched.stops[0]
        key = (sched.trip_id, origin.seq)
        delay = obs_dep.get(key, obs_arr.get(key, 0))
        origin_delay[sched.trip_id] = delay
        if delay != 0:
            primary.append(PrimaryDelay(sched.trip_id, origin.seq, delay))

    macro = MacroSimulation(schedules, seed=0, primary_delays=primary)
    macro.run()
    sim_arr: dict[tuple[str, int], int] = {
        (r.trip_id, r.seq): (r.deviation_s or 0) for r in macro.records if r.event == "arrive"
    }

    midnight = _berlin_midnight_epoch(service_date)
    pairs: list[ValidationPair] = []
    for sched in schedules:
        origin_seq = sched.stops[0].seq
        for stop in sched.stops:
            if stop.seq == origin_seq or stop.arrival_s is None:
                continue
            key = (sched.trip_id, stop.seq)
            if key not in obs_arr or key not in sim_arr:
                continue
            observed = obs_arr[key]
            # Realized only: the train has actually arrived by the snapshot time.
            if midnight + stop.arrival_s + observed > snapshot_ts:
                continue
            pairs.append(
                ValidationPair(
                    trip_id=sched.trip_id,
                    seq=stop.seq,
                    origin_delay_s=origin_delay[sched.trip_id],
                    observed_s=observed,
                    simulated_s=sim_arr[key],
                )
            )

    report = _score(
        pairs, len(schedules), service_date, snapshot_ts, long_distance_only, primary_threshold_s
    )
    return report, pairs


def _mae(pairs: list[ValidationPair]) -> tuple[float, float]:
    """Return ``(model_mae, baseline_mae)`` in seconds for a set of pairs."""
    n = len(pairs)
    if n == 0:
        return 0.0, 0.0
    model = sum(abs(p.simulated_s - p.observed_s) for p in pairs) / n
    baseline = sum(abs(p.origin_delay_s - p.observed_s) for p in pairs) / n
    return model, baseline


def _score(
    pairs: list[ValidationPair],
    n_schedules: int,
    service_date: int,
    snapshot_ts: int,
    long_distance_only: bool,
    primary_threshold_s: int,
) -> ValidationReport:
    n = len(pairs)
    delayed = [p for p in pairs if abs(p.origin_delay_s) >= primary_threshold_s]
    mae, baseline_mae = _mae(pairs)
    mae_d, baseline_mae_d = _mae(delayed)
    errors = [p.simulated_s - p.observed_s for p in pairs]
    rmse = math.sqrt(sum(e * e for e in errors) / n) if n else 0.0
    bias = sum(errors) / n if n else 0.0
    return ValidationReport(
        service_date=service_date,
        snapshot_ts=snapshot_ts,
        n_trips=len({p.trip_id for p in pairs}),
        long_distance_only=long_distance_only,
        primary_threshold_s=primary_threshold_s,
        n_pairs=n,
        mae_s=mae,
        rmse_s=rmse,
        bias_s=bias,
        correlation=_pearson([p.observed_s for p in pairs], [p.simulated_s for p in pairs]),
        baseline_mae_s=baseline_mae,
        n_delayed_pairs=len(delayed),
        mae_delayed_s=mae_d,
        baseline_mae_delayed_s=baseline_mae_d,
        correlation_delayed=_pearson(
            [p.observed_s for p in delayed], [p.simulated_s for p in delayed]
        ),
    )


def _pearson(xs: list[int], ys: list[int]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


def render_scatter(pairs: list[ValidationPair], report: ValidationReport, out_path: Path) -> Path:
    """Render an observed-vs-simulated downstream-delay scatter to a PNG."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    obs = [p.observed_s / 60 for p in pairs]
    sim = [p.simulated_s / 60 for p in pairs]
    lim = max([1.0, *map(abs, obs), *map(abs, sim)])

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.scatter(obs, sim, s=6, alpha=0.3, color="#1f77b4")
    ax.plot([-lim, lim], [-lim, lim], color="0.4", linewidth=1, linestyle="--", label="perfect")
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_xlabel("Observed downstream delay (min)")
    ax.set_ylabel("Simulated downstream delay (min)")
    ax.set_title(
        f"GTFS-RT validation {report.service_date} — "
        f"n={report.n_pairs}, MAE={report.mae_s / 60:.1f} min, r={report.correlation:.2f}"
    )
    ax.grid(color="0.92")
    ax.legend(loc="upper left")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
