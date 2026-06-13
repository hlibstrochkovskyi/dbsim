"""Recording format & replay (M1.3).

The engine runs headless and emits a **recording**; visualization and analysis
read it afterward. This removes any live-streaming/IPC coupling — a core
architectural principle.

A recording is a single self-describing **Parquet** file: one row per movement
event (a train arriving at / departing a stop), with the run's metadata embedded
in the Parquet key/value metadata (schema version, service date, seed, and the
deterministic ``run_hash``). DuckDB is the reader/writer, consistent with the
rest of the storage layer.

The loader reconstructs, for any train and time, its position — dwelling at a
stop or moving between two stops with an interpolation fraction — which is all a
replay/visualizer needs (interpolation is a rendering concern, kept out of the
engine).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import duckdb

from dbsim.engine.trains import MovementRecord

#: Bumped if the on-disk schema changes incompatibly.
RECORDING_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class RunMeta:
    """Metadata describing a recorded run (embedded in the Parquet file)."""

    schema_version: str
    service_date: int
    seed: int
    run_hash: str
    n_events: int
    created_at: str


@dataclass(frozen=True, slots=True)
class Position:
    """A train's reconstructed position at a moment in time.

    When ``at_stop`` is true the train is dwelling at ``from_stop_id`` (and
    ``to_stop_id`` equals it). Otherwise it is moving from ``from_stop_id`` to
    ``to_stop_id`` and ``fraction`` in ``[0, 1]`` is how far along it is.
    """

    trip_id: str
    at_stop: bool
    from_stop_id: str
    to_stop_id: str
    fraction: float


def write_recording(
    movements: list[MovementRecord],
    path: Path,
    *,
    service_date: int,
    seed: int,
    run_hash: str,
) -> RunMeta:
    """Write a movement stream to a Parquet recording, returning its metadata."""
    meta = RunMeta(
        schema_version=RECORDING_SCHEMA_VERSION,
        service_date=service_date,
        seed=seed,
        run_hash=run_hash,
        n_events=len(movements),
        created_at=datetime.now(UTC).isoformat(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    try:
        con.execute(
            "CREATE TABLE movements ("
            "time_s BIGINT, trip_id VARCHAR, seq INTEGER, "
            "stop_id VARCHAR, event VARCHAR, scheduled_s BIGINT)"
        )
        con.executemany(
            "INSERT INTO movements VALUES (?, ?, ?, ?, ?, ?)",
            [(m.time_s, m.trip_id, m.seq, m.stop_id, m.event, m.scheduled_s) for m in movements],
        )
        kv = (
            f"schema_version: '{meta.schema_version}', "
            f"service_date: '{meta.service_date}', "
            f"seed: '{meta.seed}', "
            f"run_hash: '{meta.run_hash}', "
            f"n_events: '{meta.n_events}', "
            f"created_at: '{meta.created_at}'"
        )
        con.execute(f"COPY movements TO '{path.as_posix()}' (FORMAT PARQUET, KV_METADATA {{{kv}}})")
    finally:
        con.close()
    return meta


def load_recording(path: Path) -> Recording:
    """Load a Parquet recording into a :class:`Recording`."""
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT time_s, trip_id, seq, stop_id, event, scheduled_s "
            f"FROM read_parquet('{path.as_posix()}') ORDER BY time_s, trip_id, seq"
        ).fetchall()
        kv_rows = con.execute(
            f"SELECT key, value FROM parquet_kv_metadata('{path.as_posix()}')"
        ).fetchall()
    finally:
        con.close()

    kv = {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in kv_rows
    }
    meta = RunMeta(
        schema_version=kv["schema_version"],
        service_date=int(kv["service_date"]),
        seed=int(kv["seed"]),
        run_hash=kv["run_hash"],
        n_events=int(kv["n_events"]),
        created_at=kv["created_at"],
    )
    movements = tuple(
        MovementRecord(
            time_s=int(r[0]),
            trip_id=str(r[1]),
            seq=int(r[2]),
            stop_id=str(r[3]),
            event=str(r[4]),
            scheduled_s=None if r[5] is None else int(r[5]),
        )
        for r in rows
    )
    return Recording(meta, movements)


class Recording:
    """A loaded recording: the movement stream plus run metadata, with replay."""

    def __init__(self, meta: RunMeta, movements: tuple[MovementRecord, ...]) -> None:
        self.meta = meta
        self.movements = movements
        self._by_trip: dict[str, list[MovementRecord]] = {}
        for record in movements:
            self._by_trip.setdefault(record.trip_id, []).append(record)
        # Each train's events in chronological order (arrival before departure).
        for events in self._by_trip.values():
            events.sort(key=lambda r: (r.time_s, 0 if r.event == "arrive" else 1))

    def trips(self) -> list[str]:
        """All trip ids in the recording, sorted."""
        return sorted(self._by_trip)

    def train_events(self, trip_id: str) -> list[MovementRecord]:
        """A train's movement events in chronological order."""
        return list(self._by_trip.get(trip_id, []))

    def position_at(self, trip_id: str, t: int) -> Position | None:
        """Reconstruct ``trip_id``'s position at time ``t``.

        Returns ``None`` if the train has not yet departed its origin. After the
        final arrival it is reported dwelling at the terminus.
        """
        events = self._by_trip.get(trip_id)
        if not events or t < events[0].time_s:
            return None

        last = events[-1]
        if t >= last.time_s:
            return Position(trip_id, True, last.stop_id, last.stop_id, 0.0)

        for cur, nxt in pairwise(events):
            if cur.time_s <= t < nxt.time_s:
                if cur.event == "depart" and nxt.event == "arrive":
                    span = nxt.time_s - cur.time_s
                    fraction = (t - cur.time_s) / span if span > 0 else 0.0
                    return Position(trip_id, False, cur.stop_id, nxt.stop_id, fraction)
                # arrive -> depart at the same stop: dwelling.
                return Position(trip_id, True, cur.stop_id, cur.stop_id, 0.0)
        return None  # pragma: no cover - covered by the bounds checks above

    def position_xy_at(
        self, trip_id: str, t: int, coords: dict[str, tuple[float, float]]
    ) -> tuple[float, float] | None:
        """Interpolate ``trip_id``'s lat/lon at ``t`` using a stop coordinate map."""
        pos = self.position_at(trip_id, t)
        if pos is None:
            return None
        a = coords.get(pos.from_stop_id)
        b = coords.get(pos.to_stop_id)
        if a is None or b is None:
            return None
        return (
            a[0] + (b[0] - a[0]) * pos.fraction,
            a[1] + (b[1] - a[1]) * pos.fraction,
        )

    def active_trip_count(self, t: int) -> int:
        """Number of trains underway (departed, not yet arrived) at time ``t``."""
        return sum(
            1
            for trip in self._by_trip
            if (p := self.position_at(trip, t)) is not None and not p.at_stop
        )
