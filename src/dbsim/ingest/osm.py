"""OpenStreetMap railway ingestion via the Overpass API (M2.1).

Track topology nationally is community-mapped in OSM / OpenRailwayMap. German
main lines are mapped as **one ``way`` per physical track** (tagged
``railway=rail``), so the number of tracks on a line section is the number of
parallel ways there — which the segment model counts geometrically.

This module fetches ``railway=rail`` ways (with geometry and the tags that
matter for capacity) inside a bounding box, with optional on-disk caching of the
raw Overpass JSON for reproducibility and offline reuse.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: A public Overpass endpoint.
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

#: bbox = (south, west, north, east) in WGS84 degrees.
BBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class RailWay:
    """One ``railway=rail`` OSM way: a single physical track with its tags."""

    osm_id: int
    ref: str | None  # line number (Streckennummer), e.g. "3611"
    usage: str | None  # main / branch / industrial / ...
    tracks: int | None  # explicit ``tracks`` tag, if any
    electrified: bool
    max_speed_kmh: int | None
    preferred_direction: bool  # has railway:preferred_direction (directional track)
    geometry: tuple[tuple[float, float], ...]  # (lat, lon) points


def overpass_query(bbox: BBox) -> str:
    """Build the Overpass QL to fetch rail ways with geometry in ``bbox``."""
    south, west, north, east = bbox
    return (
        "[out:json][timeout:120];"
        f'way["railway"="rail"]({south},{west},{north},{east});'
        "out geom tags;"
    )


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    digits = "".join(c for c in value if c.isdigit())
    return int(digits) if digits else None


def _to_railway(element: dict[str, Any]) -> RailWay | None:
    if element.get("type") != "way" or "geometry" not in element:
        return None
    tags = element.get("tags", {})
    geometry = tuple((pt["lat"], pt["lon"]) for pt in element["geometry"])
    if len(geometry) < 2:
        return None
    return RailWay(
        osm_id=int(element["id"]),
        ref=tags.get("ref"),
        usage=tags.get("usage"),
        tracks=_parse_int(tags.get("tracks")),
        electrified=tags.get("electrified", "no") not in ("no", None),
        max_speed_kmh=_parse_int(tags.get("maxspeed")),
        preferred_direction="railway:preferred_direction" in tags,
        geometry=geometry,
    )


def parse_overpass(data: dict[str, Any]) -> list[RailWay]:
    """Parse an Overpass JSON response into :class:`RailWay` objects."""
    return [rw for el in data.get("elements", []) if (rw := _to_railway(el)) is not None]


def _overpass(query: str, *, url: str, timeout: float, cache_path: Path | None) -> dict[str, Any]:
    """POST an Overpass query (or read a cached response), returning parsed JSON."""
    if cache_path is not None and cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    body = urllib.parse.urlencode({"data": query}).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": "dbsim/0.1 (railway simulation study tool)",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(raw)
    return json.loads(raw)  # type: ignore[no-any-return]


def fetch_railways(
    bbox: BBox,
    *,
    url: str = OVERPASS_URL,
    timeout: float = 180.0,
    cache_path: Path | None = None,
) -> list[RailWay]:
    """Fetch ``railway=rail`` ways in ``bbox`` (cached to ``cache_path`` if given)."""
    data = _overpass(overpass_query(bbox), url=url, timeout=timeout, cache_path=cache_path)
    return parse_overpass(data)


# ---------------------------------------------------------------------------
# Microscopic point features: signals, switches, buffer stops, crossings (M3)
# ---------------------------------------------------------------------------

#: OSM ``railway`` values for the point features a microscopic model needs.
_FEATURE_KINDS = ("signal", "switch", "buffer_stop", "railway_crossing", "crossing", "derail")


@dataclass(frozen=True, slots=True)
class RailwayNode:
    """A microscopic OSM point feature (signal, switch, buffer stop, …)."""

    osm_id: int
    kind: str  # one of _FEATURE_KINDS
    lat: float
    lon: float
    signal_type: str | None  # for signals: main / distant / combined / shunting / …
    direction: str | None  # for signals: forward / backward (which way it faces)


@dataclass(frozen=True, slots=True)
class RailwayFeatures:
    """Microscopic point features in a zone, grouped by kind."""

    signals: tuple[RailwayNode, ...]
    switches: tuple[RailwayNode, ...]
    buffer_stops: tuple[RailwayNode, ...]
    crossings: tuple[RailwayNode, ...]

    def counts(self) -> dict[str, int]:
        return {
            "signals": len(self.signals),
            "switches": len(self.switches),
            "buffer_stops": len(self.buffer_stops),
            "crossings": len(self.crossings),
        }

    def typed_signal_fraction(self) -> float:
        """Share of signals carrying a usable type (not a bare ``railway=signal``)."""
        if not self.signals:
            return 0.0
        return sum(1 for s in self.signals if s.signal_type is not None) / len(self.signals)

    def directional_signal_fraction(self) -> float:
        """Share of signals whose facing direction is known."""
        if not self.signals:
            return 0.0
        return sum(1 for s in self.signals if s.direction is not None) / len(self.signals)


def railway_features_query(bbox: BBox) -> str:
    """Overpass QL for the microscopic point features in ``bbox``."""
    south, west, north, east = bbox
    pattern = "|".join(_FEATURE_KINDS)
    return (
        "[out:json][timeout:120];"
        f'node["railway"~"^({pattern})$"]({south},{west},{north},{east});'
        "out;"
    )


def _signal_attrs(tags: dict[str, str]) -> tuple[str | None, str | None]:
    """Extract a signal's (type, direction) from its tags."""
    signal_type: str | None = None
    for sub in ("main", "distant", "combined", "shunting", "crossing", "minor"):
        if f"railway:signal:{sub}" in tags:
            signal_type = sub
            break
    return signal_type, tags.get("railway:signal:direction")


def _to_feature(element: dict[str, Any]) -> RailwayNode | None:
    if element.get("type") != "node" or "lat" not in element:
        return None
    tags = element.get("tags", {})
    kind = tags.get("railway")
    if kind not in _FEATURE_KINDS:
        return None
    signal_type, direction = _signal_attrs(tags) if kind == "signal" else (None, None)
    return RailwayNode(
        osm_id=int(element["id"]),
        kind=kind,
        lat=float(element["lat"]),
        lon=float(element["lon"]),
        signal_type=signal_type,
        direction=direction,
    )


def parse_railway_features(data: dict[str, Any]) -> RailwayFeatures:
    """Parse an Overpass JSON response into grouped :class:`RailwayFeatures`."""
    nodes = [n for el in data.get("elements", []) if (n := _to_feature(el)) is not None]
    crossing_kinds = {"crossing", "railway_crossing"}
    return RailwayFeatures(
        signals=tuple(n for n in nodes if n.kind == "signal"),
        switches=tuple(n for n in nodes if n.kind == "switch"),
        buffer_stops=tuple(n for n in nodes if n.kind == "buffer_stop"),
        crossings=tuple(n for n in nodes if n.kind in crossing_kinds),
    )


def fetch_railway_features(
    bbox: BBox,
    *,
    url: str = OVERPASS_URL,
    timeout: float = 180.0,
    cache_path: Path | None = None,
) -> RailwayFeatures:
    """Fetch microscopic point features in ``bbox`` (cached if ``cache_path`` given)."""
    data = _overpass(railway_features_query(bbox), url=url, timeout=timeout, cache_path=cache_path)
    return parse_railway_features(data)


def bbox_around(coords: list[tuple[float, float]], *, margin_deg: float = 0.03) -> BBox:
    """Bounding box (with margin) around a set of ``(lat, lon)`` points."""
    lats = [lat for lat, _ in coords]
    lons = [lon for _, lon in coords]
    return (
        min(lats) - margin_deg,
        min(lons) - margin_deg,
        max(lats) + margin_deg,
        max(lons) + margin_deg,
    )
