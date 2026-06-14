"""Declarative disruption scenarios (M2.5).

A **scenario** is a small JSON file that fully describes a mesoscopic corridor run
and the disruption applied to it — a corridor, the trains, the disruptions
(segment **closures** and **speed restrictions**), and the dispatch policy/seed.
Loading and running a scenario reproduces the same run every time, so scenarios
are reproducible experiments you can save, share, and compare (e.g. the same
disruption under different dispatchers).

Disruptions:

- **Closure** — a segment is unusable over ``[start_s, end_s)`` (a line closure);
  trains hold and proceed when it reopens.
- **SpeedRestriction** — a segment's running time is multiplied by ``factor``
  (``> 1`` = slower), modelling a temporary speed limit.

The corridor is referenced by station names; the runner resolves it (to track
segments from OSM) at run time, then this module applies the disruptions to it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dbsim.dispatch import DISPATCHERS, Dispatcher
from dbsim.engine.meso import (
    Closure,
    MesoCorridor,
    MesoSegment,
    MesoSimulation,
    MesoTrain,
    meso_corridor_from_segments,
)
from dbsim.ingest import bbox_around, fetch_railways
from dbsim.model import Timetable, build_corridor_segments


@dataclass(frozen=True, slots=True)
class TrainSpec:
    """A train in a scenario, routed by origin/destination station names."""

    id: str
    from_station: str
    to_station: str
    entry_time_s: int = 0
    priority: int = 0
    dwell_s: int = 30


@dataclass(frozen=True, slots=True)
class ClosureSpec:
    """A segment closed over a time window."""

    segment: int
    start_s: int
    end_s: int


@dataclass(frozen=True, slots=True)
class SpeedRestriction:
    """A segment slowed: its running time is multiplied by ``factor`` (> 1)."""

    segment: int
    factor: float


@dataclass(frozen=True, slots=True)
class Scenario:
    """A complete, reproducible disruption scenario."""

    name: str
    stations: tuple[str, ...]
    trains: tuple[TrainSpec, ...]
    description: str = ""
    headway_s: int = 120
    closures: tuple[ClosureSpec, ...] = ()
    speed_restrictions: tuple[SpeedRestriction, ...] = ()
    dispatcher: str = "priority"
    seed: int = 0

    def __post_init__(self) -> None:
        if self.dispatcher not in DISPATCHERS:
            raise ValueError(
                f"unknown dispatcher {self.dispatcher!r}; known: {sorted(DISPATCHERS)}"
            )

    # -- (de)serialisation --------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Scenario:
        corridor = data.get("corridor", {})
        disruptions = data.get("disruptions", {})
        dispatch = data.get("dispatch", {})
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            stations=tuple(corridor["stations"]),
            headway_s=int(corridor.get("headway_s", 120)),
            trains=tuple(
                TrainSpec(
                    id=t["id"],
                    from_station=t["from"],
                    to_station=t["to"],
                    entry_time_s=int(t.get("entry_time_s", 0)),
                    priority=int(t.get("priority", 0)),
                    dwell_s=int(t.get("dwell_s", 30)),
                )
                for t in data.get("trains", [])
            ),
            closures=tuple(
                ClosureSpec(int(c["segment"]), int(c["start_s"]), int(c["end_s"]))
                for c in disruptions.get("closures", [])
            ),
            speed_restrictions=tuple(
                SpeedRestriction(int(s["segment"]), float(s["factor"]))
                for s in disruptions.get("speed_restrictions", [])
            ),
            dispatcher=dispatch.get("dispatcher", "priority"),
            seed=int(dispatch.get("seed", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "corridor": {"stations": list(self.stations), "headway_s": self.headway_s},
            "trains": [
                {
                    "id": t.id,
                    "from": t.from_station,
                    "to": t.to_station,
                    "entry_time_s": t.entry_time_s,
                    "priority": t.priority,
                    "dwell_s": t.dwell_s,
                }
                for t in self.trains
            ],
            "disruptions": {
                "closures": [
                    {"segment": c.segment, "start_s": c.start_s, "end_s": c.end_s}
                    for c in self.closures
                ],
                "speed_restrictions": [
                    {"segment": s.segment, "factor": s.factor} for s in self.speed_restrictions
                ],
            },
            "dispatch": {"dispatcher": self.dispatcher, "seed": self.seed},
        }

    @classmethod
    def load(cls, path: Path) -> Scenario:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")


def apply_speed_restrictions(corridor: MesoCorridor, scenario: Scenario) -> MesoCorridor:
    """Return a corridor with the scenario's speed restrictions applied."""
    factor_of = {r.segment: r.factor for r in scenario.speed_restrictions}
    segments = tuple(
        MesoSegment(
            index=seg.index,
            name=seg.name,
            running_time_s=round(seg.running_time_s * factor_of.get(seg.index, 1.0)),
            capacity=seg.capacity,
            headway_s=seg.headway_s,
        )
        for seg in corridor.segments
    )
    return MesoCorridor(corridor.stations, segments)


def scenario_trains(corridor: MesoCorridor, scenario: Scenario) -> list[MesoTrain]:
    """Resolve a scenario's trains into :class:`MesoTrain`s over the corridor."""
    trains: list[MesoTrain] = []
    for spec in scenario.trains:
        a = corridor.station_index(spec.from_station)
        b = corridor.station_index(spec.to_station)
        path = tuple(range(a, b + 1)) if b >= a else tuple(range(a, b - 1, -1))
        trains.append(
            MesoTrain(
                spec.id, path, spec.entry_time_s, priority=spec.priority, dwell_s=spec.dwell_s
            )
        )
    return trains


def run_scenario(scenario: Scenario, corridor: MesoCorridor) -> MesoSimulation:
    """Apply a scenario's disruptions to ``corridor`` and run it."""
    corr = apply_speed_restrictions(corridor, scenario)
    trains = scenario_trains(corr, scenario)
    closures = [Closure(c.segment, c.start_s, c.end_s) for c in scenario.closures]
    dispatcher: Dispatcher = DISPATCHERS[scenario.dispatcher]()
    meso = MesoSimulation(
        corr, trains, seed=scenario.seed, dispatcher=dispatcher, closures=closures
    )
    meso.run()
    return meso


def build_corridor_for_scenario(
    scenario: Scenario, db_path: Path, *, cache_path: Path | None = None
) -> MesoCorridor:
    """Build the scenario's :class:`MesoCorridor` from OSM (the real run path)."""
    coords: list[tuple[str, float, float]] = []
    with Timetable(db_path) as tt:
        for name in scenario.stations:
            row = tt.connection.execute(
                "SELECT stop_lat, stop_lon FROM stops "
                "WHERE stop_name = ? AND stop_lat IS NOT NULL LIMIT 1",
                [name],
            ).fetchone()
            if row is None:
                raise ValueError(f"station not found in feed: {name!r}")
            coords.append((name, float(row[0]), float(row[1])))

    bbox = bbox_around([(la, lo) for _, la, lo in coords], margin_deg=0.02)
    segments = build_corridor_segments(coords, fetch_railways(bbox, cache_path=cache_path))
    return meso_corridor_from_segments(segments, headway_s=scenario.headway_s)
