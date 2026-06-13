"""Bildfahrplan — the time–distance (Marey) diagram of a corridor (M0.4).

A Bildfahrplan plots **time on the x-axis** and **distance along a corridor on
the y-axis**; each train is a line connecting its (time, position) points. The
slope of a line is the train's speed; lines crossing means trains meet or
overtake. It is the classic way to read a railway timetable visually.

This module has two layers:

- a **data layer** (corridor geometry + train-path extraction) with no plotting
  dependency, so it is cheap to test;
- a **render layer** (:func:`render_bildfahrplan`) that draws the diagram with
  matplotlib to a PNG.

Distance along the corridor is the cumulative great-circle distance between the
corridor's stations (from their GTFS coordinates), so slopes reflect real speed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dbsim.model.timetable import Timetable

#: Default corridor — the dense Frankfurt–Hannover ICE line.
DEFAULT_CORRIDOR: tuple[str, ...] = (
    "Frankfurt(Main)Hbf",
    "Hanau Hbf",
    "Fulda",
    "Kassel-Wilhelmshöhe",
    "Göttingen",
    "Hannover Hbf",
)

_EARTH_RADIUS_KM = 6371.0088

# A train travelling in the direction of increasing corridor distance ("down",
# towards the last station) vs decreasing ("up", towards the first).
DOWN = "down"
UP = "up"


@dataclass(frozen=True, slots=True)
class CorridorStation:
    """A station on the corridor, with its cumulative distance from the start."""

    name: str
    stop_ids: frozenset[str]
    lat: float
    lon: float
    distance_km: float


@dataclass(frozen=True, slots=True)
class Corridor:
    """An ordered sequence of stations defining a time–distance axis."""

    stations: tuple[CorridorStation, ...]

    @property
    def length_km(self) -> float:
        return self.stations[-1].distance_km

    def index_of_stop(self, stop_id: str) -> int | None:
        """Return the corridor index a ``stop_id`` belongs to, or ``None``."""
        for i, station in enumerate(self.stations):
            if stop_id in station.stop_ids:
                return i
        return None


@dataclass(frozen=True, slots=True)
class TrainPath:
    """One train's trajectory across the corridor."""

    trip_id: str
    line: str | None
    #: ``(time_s, distance_km)`` points, ordered by time.
    points: tuple[tuple[int, float], ...]
    direction: str  # DOWN or UP


def _hhmm(x: float, _pos: object = None) -> str:
    """Format an x-axis value (hours) as ``HH:MM`` for tick labels."""
    hours = int(x)
    minutes = round((x - hours) * 60)
    if minutes == 60:
        hours, minutes = hours + 1, 0
    return f"{hours:02d}:{minutes:02d}"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def build_corridor(tt: Timetable, names: tuple[str, ...] = DEFAULT_CORRIDOR) -> Corridor:
    """Build a :class:`Corridor` from an ordered list of station names.

    Each name may correspond to several GTFS stops (platform children share the
    name); all of them are recorded so trains calling at any platform map to the
    corridor. The representative coordinate is the first stop with coordinates.
    """
    if len(names) < 2:
        raise ValueError("a corridor needs at least two stations")

    con = tt.connection
    stations: list[CorridorStation] = []
    cumulative = 0.0
    prev: tuple[float, float] | None = None

    for name in names:
        rows = con.execute(
            "SELECT stop_id, stop_lat, stop_lon FROM stops WHERE stop_name = ?", [name]
        ).fetchall()
        if not rows:
            raise ValueError(f"station {name!r} not found in the feed")
        stop_ids = frozenset(str(r[0]) for r in rows)
        coord = next(((r[1], r[2]) for r in rows if r[1] is not None and r[2] is not None), None)
        if coord is None:
            raise ValueError(f"station {name!r} has no coordinates")
        lat, lon = coord
        if prev is not None:
            cumulative += _haversine_km(prev[0], prev[1], lat, lon)
        prev = (lat, lon)
        stations.append(CorridorStation(name, stop_ids, lat, lon, cumulative))

    return Corridor(tuple(stations))


def extract_train_paths(
    tt: Timetable, corridor: Corridor, day: date | int | str
) -> list[TrainPath]:
    """Extract every train's trajectory across ``corridor`` on ``day``.

    A train is included if it calls at two or more corridor stations. Its points
    are the scheduled arrival and departure times at those stations (so a dwell
    shows as a short horizontal segment). Direction is inferred from whether
    corridor distance increases or decreases over the journey.
    """
    active = tt.services_on(day)
    if not active:
        return []
    all_stop_ids = {sid for st in corridor.stations for sid in st.stop_ids}

    rows = tt.connection.execute(
        """
        SELECT t.trip_id, r.route_short_name, st.stop_sequence,
               st.stop_id, st.arrival_s, st.departure_s
        FROM stop_times st
        JOIN trips t ON st.trip_id = t.trip_id
        LEFT JOIN routes r ON t.route_id = r.route_id
        WHERE t.service_id IN (SELECT UNNEST(?))
          AND st.stop_id IN (SELECT UNNEST(?))
        ORDER BY t.trip_id, st.stop_sequence
        """,
        [list(active), list(all_stop_ids)],
    ).fetchall()

    # Group consecutive rows by trip.
    paths: list[TrainPath] = []
    current: str | None = None
    line: str | None = None
    points: list[tuple[int, float]] = []
    distances: list[float] = []

    def flush() -> None:
        if current is not None and len(set(distances)) >= 2:
            direction = DOWN if distances[-1] > distances[0] else UP
            ordered = tuple(sorted(points))
            paths.append(TrainPath(current, line, ordered, direction))

    for trip_id, route_name, _seq, stop_id, arr_s, dep_s in rows:
        if trip_id != current:
            flush()
            current = trip_id
            line = route_name
            points = []
            distances = []
        idx = corridor.index_of_stop(str(stop_id))
        if idx is None:
            continue
        dist = corridor.stations[idx].distance_km
        distances.append(dist)
        if arr_s is not None:
            points.append((arr_s, dist))
        if dep_s is not None:
            points.append((dep_s, dist))
    flush()

    return paths


def render_bildfahrplan(
    corridor: Corridor,
    paths: list[TrainPath],
    out_path: Path,
    *,
    title: str | None = None,
) -> Path:
    """Render the Bildfahrplan to a PNG and return its path."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    fig, ax = plt.subplots(figsize=(16, 9))

    colors = {DOWN: "#1f77b4", UP: "#d62728"}
    for path in paths:
        xs = [p[0] / 3600 for p in path.points]
        ys = [p[1] for p in path.points]
        ax.plot(xs, ys, color=colors[path.direction], linewidth=0.8, alpha=0.8)

    # Horizontal station lines + labels on the distance axis.
    for station in corridor.stations:
        ax.axhline(station.distance_km, color="0.85", linewidth=0.6, zorder=0)
    ax.set_yticks([s.distance_km for s in corridor.stations])
    ax.set_yticklabels([f"{s.name}  ({s.distance_km:.0f} km)" for s in corridor.stations])

    ax.xaxis.set_major_formatter(FuncFormatter(_hhmm))
    ax.set_xlabel("Time of day")
    ax.set_ylabel("Distance along corridor")
    ax.set_title(title or "Bildfahrplan")
    ax.grid(axis="x", color="0.92", linewidth=0.5)
    ax.margins(x=0.01)

    # Legend by direction.
    down_label = f"{corridor.stations[0].name} → {corridor.stations[-1].name}"
    up_label = f"{corridor.stations[-1].name} → {corridor.stations[0].name}"
    ax.plot([], [], color=colors[DOWN], label=down_label)
    ax.plot([], [], color=colors[UP], label=up_label)
    ax.legend(loc="upper left", fontsize=8)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
