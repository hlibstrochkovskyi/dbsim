"""Query layer over the canonical GTFS tables in DuckDB.

This is the read model for the scheduled timetable (M0.2). It answers the two
M0.2 deliverable questions — "which trains call at station X on a given day?" and
"what is train T's full ordered stop sequence?" — plus the service-calendar logic
they depend on.

GTFS service-calendar semantics (implemented in :meth:`Timetable.services_on`):
a service runs on date *D* (weekday *W*) if a ``calendar`` row covers *D* with
weekday *W* enabled **and** no ``calendar_dates`` removal (``exception_type = 2``)
applies, **or** a ``calendar_dates`` addition (``exception_type = 1``) names it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import TracebackType

import duckdb

#: GTFS weekday columns indexed by Python ``date.weekday()`` (Mon=0 .. Sun=6).
_WEEKDAY_COLUMNS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


@dataclass(frozen=True, slots=True)
class StopCall:
    """One scheduled call of a trip at a stop (a row of the stop sequence)."""

    stop_sequence: int
    stop_id: str
    stop_name: str
    arrival_time: str | None
    departure_time: str | None
    arrival_s: int | None
    departure_s: int | None


@dataclass(frozen=True, slots=True)
class StationCall:
    """A train calling at a queried station: which trip, which line, when."""

    trip_id: str
    route_short_name: str | None
    trip_headsign: str | None
    arrival_time: str | None
    departure_time: str | None
    departure_s: int | None


def _as_yyyymmdd(day: date | int | str) -> int:
    """Normalise a date to the GTFS integer form ``YYYYMMDD``."""
    if isinstance(day, date):
        return int(day.strftime("%Y%m%d"))
    return int(day)


def _weekday_column(day: date | int | str) -> str:
    """Return the GTFS weekday column name for a date."""
    if isinstance(day, date):
        d = day
    else:
        s = str(int(day))
        d = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return _WEEKDAY_COLUMNS[d.weekday()]


class Timetable:
    """A read-only handle over a DuckDB database of canonical GTFS tables.

    Usable as a context manager::

        with Timetable(db_path) as tt:
            calls = tt.trains_through_station("Frankfurt(Main)Hbf", 20260616)
    """

    def __init__(self, db_path: Path | str) -> None:
        self._con = duckdb.connect(str(db_path), read_only=True)

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> Timetable:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- service calendar ---------------------------------------------------

    def services_on(self, day: date | int | str) -> set[str]:
        """Return the set of ``service_id``s active on ``day``."""
        ymd = _as_yyyymmdd(day)
        weekday = _weekday_column(day)
        rows = self._con.execute(
            f"""
            SELECT service_id FROM calendar
            WHERE start_date <= ? AND end_date >= ? AND {weekday} = 1
              AND service_id NOT IN (
                  SELECT service_id FROM calendar_dates
                  WHERE date = ? AND exception_type = 2
              )
            UNION
            SELECT service_id FROM calendar_dates
            WHERE date = ? AND exception_type = 1
            """,
            [ymd, ymd, ymd, ymd],
        ).fetchall()
        return {r[0] for r in rows}

    # -- station resolution -------------------------------------------------

    def resolve_station_stops(self, station_name: str) -> set[str]:
        """Return all ``stop_id``s for a station: the named stop and descendants.

        GTFS models a station as a parent stop with child platform stops; trips
        may call at either, so we walk the ``parent_station`` tree downward.
        """
        rows = self._con.execute(
            """
            WITH RECURSIVE station_stops AS (
                SELECT stop_id FROM stops WHERE stop_name = ?
                UNION
                SELECT s.stop_id FROM stops s
                JOIN station_stops ss ON s.parent_station = ss.stop_id
            )
            SELECT stop_id FROM station_stops
            """,
            [station_name],
        ).fetchall()
        return {r[0] for r in rows}

    # -- M0.2 deliverable queries -------------------------------------------

    def trains_through_station(self, station_name: str, day: date | int | str) -> list[StationCall]:
        """All trains calling at ``station_name`` on ``day``, ordered by departure.

        Returns one :class:`StationCall` per (trip, call-at-this-station).
        """
        stop_ids = self.resolve_station_stops(station_name)
        active = self.services_on(day)
        if not stop_ids or not active:
            return []
        rows = self._con.execute(
            """
            SELECT DISTINCT
                t.trip_id, r.route_short_name, t.trip_headsign,
                st.arrival_time, st.departure_time, st.departure_s
            FROM stop_times st
            JOIN trips t ON st.trip_id = t.trip_id
            LEFT JOIN routes r ON t.route_id = r.route_id
            WHERE st.stop_id IN (SELECT UNNEST(?))
              AND t.service_id IN (SELECT UNNEST(?))
            ORDER BY st.departure_s NULLS LAST, t.trip_id
            """,
            [list(stop_ids), list(active)],
        ).fetchall()
        return [
            StationCall(
                trip_id=r[0],
                route_short_name=r[1],
                trip_headsign=r[2],
                arrival_time=r[3],
                departure_time=r[4],
                departure_s=r[5],
            )
            for r in rows
        ]

    def trip_stop_sequence(self, trip_id: str) -> list[StopCall]:
        """Reconstruct a trip's full ordered stop sequence with scheduled times."""
        rows = self._con.execute(
            """
            SELECT
                st.stop_sequence, st.stop_id, s.stop_name,
                st.arrival_time, st.departure_time, st.arrival_s, st.departure_s
            FROM stop_times st
            LEFT JOIN stops s ON st.stop_id = s.stop_id
            WHERE st.trip_id = ?
            ORDER BY st.stop_sequence
            """,
            [trip_id],
        ).fetchall()
        return [
            StopCall(
                stop_sequence=r[0],
                stop_id=r[1],
                stop_name=r[2],
                arrival_time=r[3],
                departure_time=r[4],
                arrival_s=r[5],
                departure_s=r[6],
            )
            for r in rows
        ]

    def table_counts(self) -> dict[str, int]:
        """Row counts per canonical table (for sanity-checking a load)."""
        tables = (
            "agency",
            "stops",
            "routes",
            "trips",
            "stop_times",
            "calendar",
            "calendar_dates",
        )
        counts: dict[str, int] = {}
        for table in tables:
            result = self._con.execute(f"SELECT count(*) FROM {table}").fetchone()
            counts[table] = int(result[0]) if result is not None else 0
        return counts
