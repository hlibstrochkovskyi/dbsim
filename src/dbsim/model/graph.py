"""The macroscopic timetable graph (M0.3).

Two graph views are built from the canonical timetable for one service date:

1. **Time-expanded graph** (the routing model). Every scheduled arrival and
   departure is a node; edges are:

   - *ride* — a train moving from one stop's departure to the next stop's
     arrival (weight = running time);
   - *dwell* — waiting on the train at a stop (arrival → departure of the same
     trip);
   - *timeline* — the chronological chain of all events at a station, so a
     traveller can wait and transfer between trains (weight = elapsed time).

   Shortest path by edge weight is therefore **earliest arrival in scheduled
   time**, including realistic waiting and transfers — exactly the M0.3
   acceptance ("a plausible itinerary matching a real connection").

2. **Station graph** (the connectivity view, the literal "stations = nodes").
   One node per station, a directed edge wherever a train runs directly between
   two stations. Used for the node/edge/connectivity statistics.

Scope note: transfers currently allow any non-negative wait. A *minimum*
connection time is M1.2's concern and will refine the timeline edges later.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

import rustworkx as rx

from dbsim.model.timetable import Timetable

# Event kinds and their sort rank, so an arrival sorts before a departure at the
# same instant (you can alight then board in the same moment).
_ARR = "arr"
_DEP = "dep"
_KIND_RANK = {_ARR: 0, _DEP: 1}


@dataclass(frozen=True, slots=True)
class EventNode:
    """A scheduled timetable event — one node of the time-expanded graph."""

    station_id: str
    stop_id: str
    time_s: int
    kind: str  # _ARR or _DEP
    trip_id: str | None  # None only for virtual source/sink nodes


@dataclass(frozen=True, slots=True)
class JourneyLeg:
    """One train leg of a planned journey."""

    trip_id: str
    line: str | None
    board_stop_id: str
    board_stop_name: str
    board_time_s: int
    alight_stop_id: str
    alight_stop_name: str
    alight_time_s: int


@dataclass(frozen=True, slots=True)
class Journey:
    """A planned earliest-arrival journey: an ordered list of train legs."""

    legs: tuple[JourneyLeg, ...]

    @property
    def depart_time_s(self) -> int:
        return self.legs[0].board_time_s

    @property
    def arrive_time_s(self) -> int:
        return self.legs[-1].alight_time_s

    @property
    def duration_s(self) -> int:
        return self.arrive_time_s - self.depart_time_s

    @property
    def n_transfers(self) -> int:
        return len(self.legs) - 1


@dataclass(frozen=True, slots=True)
class GraphStats:
    """Basic size and connectivity statistics of the timetable graph."""

    service_date: int
    event_nodes: int
    event_edges: int
    stations: int
    station_edges: int
    weakly_connected_components: int
    largest_component_stations: int


def format_hms(seconds: int | None) -> str:
    """Format seconds-since-midnight as ``HH:MM:SS`` (hours may exceed 24)."""
    if seconds is None:
        return "--:--:--"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _station_of(stop_id: str, parent_station: str | None) -> str:
    """Group a stop under its station: the parent if any, else the stop itself."""
    return parent_station if parent_station else stop_id


class TimetableGraph:
    """A timetable graph for a single service date, built from a :class:`Timetable`."""

    def __init__(self, tt: Timetable, day: date | int | str) -> None:
        self._service_date = int(day) if not isinstance(day, date) else int(day.strftime("%Y%m%d"))
        self._g: rx.PyDiGraph[EventNode, int] = rx.PyDiGraph()

        # Lookups populated during the build.
        self._stop_to_station: dict[str, str] = {}
        self._stop_name: dict[str, str] = {}
        self._name_to_stations: dict[str, set[str]] = defaultdict(set)
        self._trip_line: dict[str, str | None] = {}
        self._timeline: dict[str, list[int]] = defaultdict(list)  # station -> node indices
        self._arrivals_by_station: dict[str, list[int]] = defaultdict(list)
        self._station_edges: set[tuple[str, str]] = set()

        self._load_stops(tt)
        self._build_events(tt)
        self._build_timelines()

    # -- construction -------------------------------------------------------

    def _load_stops(self, tt: Timetable) -> None:
        rows = tt.connection.execute(
            "SELECT stop_id, parent_station, stop_name FROM stops"
        ).fetchall()
        for stop_id, parent, name in rows:
            station = _station_of(stop_id, parent)
            self._stop_to_station[stop_id] = station
            self._stop_name[stop_id] = name
            if name is not None:
                self._name_to_stations[name].add(station)

    def _build_events(self, tt: Timetable) -> None:
        active = tt.services_on(self._service_date)
        if not active:
            return
        rows = tt.connection.execute(
            """
            SELECT t.trip_id, r.route_short_name, st.stop_sequence,
                   st.stop_id, st.arrival_s, st.departure_s
            FROM stop_times st
            JOIN trips t ON st.trip_id = t.trip_id
            LEFT JOIN routes r ON t.route_id = r.route_id
            WHERE t.service_id IN (SELECT UNNEST(?))
            ORDER BY t.trip_id, st.stop_sequence
            """,
            [list(active)],
        ).fetchall()

        g = self._g
        current_trip: str | None = None
        prev_dep_node: int | None = None
        prev_dep_time: int | None = None

        for trip_id, line, _seq, stop_id, arr_s, dep_s in rows:
            if trip_id != current_trip:
                current_trip = trip_id
                prev_dep_node = None
                prev_dep_time = None
                self._trip_line[trip_id] = line

            station = self._stop_to_station.get(stop_id, stop_id)
            arr_node: int | None = None
            dep_node: int | None = None

            if arr_s is not None:
                arr_node = g.add_node(EventNode(station, stop_id, arr_s, _ARR, trip_id))
                self._timeline[station].append(arr_node)
                self._arrivals_by_station[station].append(arr_node)
            if dep_s is not None:
                dep_node = g.add_node(EventNode(station, stop_id, dep_s, _DEP, trip_id))
                self._timeline[station].append(dep_node)

            # Dwell on the train at this stop.
            if arr_node is not None and dep_node is not None and dep_s >= arr_s:
                g.add_edge(arr_node, dep_node, dep_s - arr_s)

            # Ride edge from the previous stop's departure to this arrival.
            if prev_dep_node is not None and arr_node is not None and arr_s >= prev_dep_time:
                g.add_edge(prev_dep_node, arr_node, arr_s - prev_dep_time)
                self._station_edges.add((self._node_station(prev_dep_node), station))

            if dep_node is not None:
                prev_dep_node = dep_node
                prev_dep_time = dep_s

    def _node_station(self, idx: int) -> str:
        return self._g[idx].station_id

    def _build_timelines(self) -> None:
        """Chain each station's events in time order so travellers can wait."""
        g = self._g
        for nodes in self._timeline.values():
            nodes.sort(key=lambda i: (g[i].time_s, _KIND_RANK[g[i].kind]))
            for u, v in pairwise(nodes):
                g.add_edge(u, v, g[v].time_s - g[u].time_s)

    # -- statistics ---------------------------------------------------------

    def stats(self) -> GraphStats:
        """Compute size and connectivity statistics."""
        station_graph: rx.PyDiGraph[str, None] = rx.PyDiGraph()
        idx_of: dict[str, int] = {}
        for a, b in self._station_edges:
            for s in (a, b):
                if s not in idx_of:
                    idx_of[s] = station_graph.add_node(s)
            station_graph.add_edge(idx_of[a], idx_of[b], None)
        # Include isolated stations that appear in timelines but have no edges.
        for station in self._timeline:
            if station not in idx_of:
                idx_of[station] = station_graph.add_node(station)

        components = rx.weakly_connected_components(station_graph)
        largest = max((len(c) for c in components), default=0)
        return GraphStats(
            service_date=self._service_date,
            event_nodes=self._g.num_nodes(),
            event_edges=self._g.num_edges(),
            stations=station_graph.num_nodes(),
            station_edges=station_graph.num_edges(),
            weakly_connected_components=len(components),
            largest_component_stations=largest,
        )

    # -- journey planning ---------------------------------------------------

    def _stations_for_name(self, name: str) -> set[str]:
        return set(self._name_to_stations.get(name, set()))

    def plan_journey(self, from_name: str, to_name: str, depart_after_s: int) -> Journey | None:
        """Earliest-arrival journey from ``from_name`` to ``to_name``.

        Args:
            from_name: Origin station name (exact GTFS ``stop_name``).
            to_name: Destination station name.
            depart_after_s: Earliest departure, seconds since midnight.

        Returns:
            The earliest-arrival :class:`Journey`, or ``None`` if unreachable.
        """
        from_stations = self._stations_for_name(from_name)
        to_stations = self._stations_for_name(to_name)
        if not from_stations or not to_stations:
            return None

        g = self._g
        source = g.add_node(EventNode("", "", depart_after_s, _DEP, None))
        sink = g.add_node(EventNode("", "", 0, _ARR, None))
        try:
            self._connect_source(source, from_stations, depart_after_s)
            for station in to_stations:
                for arr_idx in self._arrivals_by_station.get(station, []):
                    g.add_edge(arr_idx, sink, 0)
            paths = rx.dijkstra_shortest_paths(g, source, target=sink, weight_fn=float)
            if sink not in paths:
                return None
            node_path = list(paths[sink])
            events = [g[i] for i in node_path[1:-1]]  # drop virtual source/sink
        finally:
            g.remove_node(source)
            g.remove_node(sink)

        return self._reconstruct(events)

    def _connect_source(self, source: int, stations: set[str], depart_after_s: int) -> None:
        """Link the virtual source to the first reachable event at each origin."""
        g = self._g
        for station in stations:
            timeline = self._timeline.get(station, [])
            for node_idx in timeline:
                if g[node_idx].time_s >= depart_after_s:
                    g.add_edge(source, node_idx, g[node_idx].time_s - depart_after_s)
                    break

    def _reconstruct(self, events: list[EventNode]) -> Journey | None:
        """Collapse a node path into train legs (one per boarded trip)."""
        legs: list[JourneyLeg] = []
        trip: str | None = None
        board: EventNode | None = None
        alight: EventNode | None = None

        def flush() -> None:
            if trip is not None and board is not None and alight is not None:
                legs.append(
                    JourneyLeg(
                        trip_id=trip,
                        line=self._trip_line.get(trip),
                        board_stop_id=board.stop_id,
                        board_stop_name=self._stop_name.get(board.stop_id, board.stop_id),
                        board_time_s=board.time_s,
                        alight_stop_id=alight.stop_id,
                        alight_stop_name=self._stop_name.get(alight.stop_id, alight.stop_id),
                        alight_time_s=alight.time_s,
                    )
                )

        for node in events:
            if node.trip_id is None:
                continue
            if node.kind == _DEP and node.trip_id != trip:
                flush()
                trip = node.trip_id
                board = node
                alight = None
            elif node.kind == _ARR and node.trip_id == trip:
                alight = node
        flush()

        return Journey(legs=tuple(legs)) if legs else None
