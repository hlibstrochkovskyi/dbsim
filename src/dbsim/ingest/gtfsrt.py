"""GTFS-RT ingestion: parse real-time delay snapshots, and capture them.

The free real-time feed (``https://realtime.gtfs.de/realtime-free.pb``) is a live
GTFS-RT v2.0 protobuf snapshot of the whole German network. Each ``TripUpdate``
carries a per-stop delay profile (``arrival``/``departure`` delay in seconds; may
be negative = early). Its ``trip_id``s match the **full** static feed (`free`),
not the `fv` subset — see the validation module.

Because the feed is a live snapshot, building a historical day means capturing
snapshots forward over time (:func:`capture`). A single snapshot, however,
already contains each in-progress train's realized delays at the stops it has
passed, which is enough for the M1.4 validation.
"""

from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from google.transit import gtfs_realtime_pb2

#: The free, nationwide GTFS-RT feed.
DEFAULT_RT_URL = "https://realtime.gtfs.de/realtime-free.pb"


@dataclass(frozen=True, slots=True)
class TripDelay:
    """One observed per-stop delay from a GTFS-RT ``TripUpdate``."""

    snapshot_ts: int  # feed header timestamp (epoch seconds)
    trip_id: str
    stop_sequence: int
    stop_id: str
    arrival_delay_s: int | None
    departure_delay_s: int | None


def fetch_snapshot(url: str = DEFAULT_RT_URL, *, timeout: float = 60.0) -> bytes:
    """Fetch the raw protobuf bytes of the live feed."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data: bytes = response.read()
    return data


def _event_delay(stu: object, field: str) -> int | None:
    """Delay (seconds) of a StopTimeUpdate's arrival/departure, or None."""
    if not stu.HasField(field):  # type: ignore[attr-defined]
        return None
    event = getattr(stu, field)
    return int(event.delay) if event.HasField("delay") else None


def parse_snapshot(data: bytes) -> tuple[int, list[TripDelay]]:
    """Parse protobuf bytes into ``(snapshot_ts, delays)``.

    ``snapshot_ts`` is the feed header timestamp (epoch seconds). One
    :class:`TripDelay` is emitted per stop-time-update that has a delay.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(data)
    snapshot_ts = int(feed.header.timestamp)

    delays: list[TripDelay] = []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip_id = tu.trip.trip_id
        for stu in tu.stop_time_update:
            arr = _event_delay(stu, "arrival")
            dep = _event_delay(stu, "departure")
            if arr is None and dep is None:
                continue
            delays.append(
                TripDelay(
                    snapshot_ts=snapshot_ts,
                    trip_id=trip_id,
                    stop_sequence=int(stu.stop_sequence),
                    stop_id=str(stu.stop_id),
                    arrival_delay_s=None if arr is None else int(arr),
                    departure_delay_s=None if dep is None else int(dep),
                )
            )
    return snapshot_ts, delays


def read_snapshot_file(path: Path) -> tuple[int, list[TripDelay]]:
    """Parse a ``.pb`` snapshot file from disk."""
    return parse_snapshot(path.read_bytes())


def capture(
    out_dir: Path,
    *,
    url: str = DEFAULT_RT_URL,
    count: int = 1,
    interval_s: float = 120.0,
) -> list[Path]:
    """Poll the live feed ``count`` times, saving each snapshot as a ``.pb`` file.

    Snapshots are named by their UTC capture time. Returns the written paths.
    Use this to build a historical day by capturing forward.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for i in range(count):
        data = fetch_snapshot(url)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = out_dir / f"snapshot-{stamp}.pb"
        path.write_bytes(data)
        written.append(path)
        if i + 1 < count:
            time.sleep(interval_s)
    return written
