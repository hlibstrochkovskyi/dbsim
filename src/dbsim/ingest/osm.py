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


def fetch_railways(
    bbox: BBox,
    *,
    url: str = OVERPASS_URL,
    timeout: float = 180.0,
    cache_path: Path | None = None,
) -> list[RailWay]:
    """Fetch ``railway=rail`` ways in ``bbox`` (cached to ``cache_path`` if given)."""
    if cache_path is not None and cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return parse_overpass(data)

    body = urllib.parse.urlencode({"data": overpass_query(bbox)}).encode()
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
    data = json.loads(raw)
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(raw)
    return parse_overpass(data)


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
