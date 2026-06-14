"""Microscopic infrastructure model — one curated zone (M3.1).

This is the project's finest grain: a single **passing loop** modelled at
signal/block resolution, where the meso layer only had a track segment with a
headway number. The zone is the Pfäffingen loop on the single-track Ammertalbahn,
chosen by the M3.1.0 coverage survey.

A passing loop is the minimal interlocking where the line's drama lives: a
single-track line widens to **two parallel running tracks** between two switches,
so opposing trains can **meet** (one waits on a track while the other passes). The
model captures the occupation units a microscopic, blocking-time simulation
(M3.2) and deadlock-avoidance (M3.3) need:

- :class:`Block` — an occupation unit (a resource held by one train at a time):
  the two single-track **approaches** and the two **loop tracks**.
- :class:`MicroRoute` — an ordered path of blocks through the zone (per direction,
  per loop track).
- the real OSM **signals** and **switches** (positions + signal type/direction),
  for the blocking-time stairway, inspection, and validation.

Per the plan this layer is **hand-curated**: the logical interlocking is specified
explicitly (auto-deriving it from fragmented OSM ways is fragile), but every
quantity — loop length, line speeds, signal/switch positions — is grounded in the
surveyed OSM data.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from pyproj import Transformer

from dbsim.ingest.osm import RailWay, RailwayFeatures

_TF = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)

#: Pfäffingen loop bounding box (south, west, north, east) — the curated zone.
PFAFFINGEN_BBOX = (48.527, 8.958, 48.538, 8.972)

# Block kinds.
APPROACH = "approach"
LOOP = "loop"


def _project(lat: float, lon: float) -> tuple[float, float]:
    x, y = _TF.transform(lon, lat)
    return (x, y)


@dataclass(frozen=True, slots=True)
class Block:
    """A microscopic occupation unit — held by at most one train at a time."""

    id: str
    length_m: float
    max_speed_kmh: int
    has_platform: bool
    kind: str  # APPROACH or LOOP


@dataclass(frozen=True, slots=True)
class MicroRoute:
    """An ordered path of block ids through the zone."""

    name: str
    direction: str  # "west_to_east" | "east_to_west"
    loop_track: str  # which loop track the route uses ("1" | "2")
    blocks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MicroSignal:
    """A signal in the zone (block boundary), from OSM."""

    osm_id: int
    lat: float
    lon: float
    signal_type: str | None
    direction: str | None


@dataclass(frozen=True, slots=True)
class MicroSwitch:
    """A switch (turnout) in the zone, from OSM."""

    name: str
    lat: float
    lon: float


@dataclass(frozen=True, slots=True)
class MicroZone:
    """A curated microscopic infrastructure model of one zone."""

    name: str
    west_boundary: str  # macro station the west approach connects to
    east_boundary: str
    blocks: tuple[Block, ...]
    routes: tuple[MicroRoute, ...]
    signals: tuple[MicroSignal, ...]
    switches: tuple[MicroSwitch, ...]

    def block(self, block_id: str) -> Block:
        for b in self.blocks:
            if b.id == block_id:
                return b
        raise KeyError(block_id)

    @property
    def loop_blocks(self) -> tuple[Block, ...]:
        return tuple(b for b in self.blocks if b.kind == LOOP)

    @property
    def approach_blocks(self) -> tuple[Block, ...]:
        return tuple(b for b in self.blocks if b.kind == APPROACH)

    def validate(self) -> list[str]:
        """Return a list of structural problems (empty ⇒ a valid passing loop)."""
        issues: list[str] = []
        if len(self.loop_blocks) != 2:
            issues.append(f"expected 2 loop tracks, found {len(self.loop_blocks)}")
        if len(self.approach_blocks) != 2:
            issues.append(f"expected 2 approach blocks, found {len(self.approach_blocks)}")
        if not any(b.has_platform for b in self.blocks):
            issues.append("no platform track")
        if any(b.length_m <= 0 for b in self.blocks):
            issues.append("a block has non-positive length")
        block_ids = {b.id for b in self.blocks}
        for route in self.routes:
            missing = [bid for bid in route.blocks if bid not in block_ids]
            if missing:
                issues.append(f"route {route.name} references unknown blocks {missing}")
            kinds = [self.block(b).kind for b in route.blocks if b in block_ids]
            if kinds and kinds != [APPROACH, LOOP, APPROACH]:
                issues.append(f"route {route.name} is not approach→loop→approach: {kinds}")
        if not self.west_boundary or not self.east_boundary:
            issues.append("missing a zone boundary")
        return issues


def _max_switch_span_m(features: RailwayFeatures) -> float:
    """Greatest distance between any two switches — the loop's extent."""
    pts = [_project(s.lat, s.lon) for s in features.switches]
    return max((math.dist(a, b) for a in pts for b in pts), default=0.0)


def curate_pfaffingen_loop(
    rail_ways: list[RailWay],
    features: RailwayFeatures,
    *,
    west_boundary: str = "Unterjesingen Mitte",
    east_boundary: str = "Entringen",
    approach_length_m: float = 500.0,
) -> MicroZone:
    """Build the curated Pfäffingen passing-loop model, grounded in OSM data.

    Loop length is the switch-to-switch span; the through/loop track speeds come
    from the surveyed rail ways. The block/route topology is hand-curated.
    """
    loop_length = round(_max_switch_span_m(features)) or 289
    speeds = [w.max_speed_kmh for w in rail_ways if w.max_speed_kmh]
    # Through track ≈ the line's prevailing speed; passing track is the slowest
    # (diverging move through the turnouts).
    through_speed = Counter(speeds).most_common(1)[0][0] if speeds else 70
    loop_speed = min(speeds) if speeds else 50

    blocks = (
        Block("west_approach", approach_length_m, through_speed, False, APPROACH),
        Block("loop_t1", float(loop_length), through_speed, True, LOOP),  # through track + platform
        Block("loop_t2", float(loop_length), loop_speed, False, LOOP),  # passing track
        Block("east_approach", approach_length_m, through_speed, False, APPROACH),
    )
    routes = (
        MicroRoute("WE_t1", "west_to_east", "1", ("west_approach", "loop_t1", "east_approach")),
        MicroRoute("WE_t2", "west_to_east", "2", ("west_approach", "loop_t2", "east_approach")),
        MicroRoute("EW_t1", "east_to_west", "1", ("east_approach", "loop_t1", "west_approach")),
        MicroRoute("EW_t2", "east_to_west", "2", ("east_approach", "loop_t2", "west_approach")),
    )
    signals = tuple(
        MicroSignal(s.osm_id, s.lat, s.lon, s.signal_type, s.direction) for s in features.signals
    )
    switches = tuple(
        MicroSwitch(f"W{i + 1}", s.lat, s.lon) for i, s in enumerate(features.switches)
    )
    return MicroZone(
        name="Pfäffingen",
        west_boundary=west_boundary,
        east_boundary=east_boundary,
        blocks=blocks,
        routes=routes,
        signals=signals,
        switches=switches,
    )
