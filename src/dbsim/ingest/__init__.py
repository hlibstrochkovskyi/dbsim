"""ETL for external data sources (GTFS now; GTFS-RT and OSM in later phases)."""

from __future__ import annotations

from dbsim.ingest.gtfs import FEEDS, download_feed, feed_url, load_feed
from dbsim.ingest.gtfsrt import (
    DEFAULT_RT_URL,
    TripDelay,
    capture,
    fetch_snapshot,
    parse_snapshot,
    read_snapshot_file,
)
from dbsim.ingest.osm import (
    OVERPASS_URL,
    RailWay,
    RailwayFeatures,
    RailwayNode,
    bbox_around,
    fetch_railway_features,
    fetch_railways,
    parse_overpass,
    parse_railway_features,
)

__all__ = [
    "DEFAULT_RT_URL",
    "FEEDS",
    "OVERPASS_URL",
    "RailWay",
    "RailwayFeatures",
    "RailwayNode",
    "TripDelay",
    "bbox_around",
    "capture",
    "download_feed",
    "feed_url",
    "fetch_railway_features",
    "fetch_railways",
    "fetch_snapshot",
    "load_feed",
    "parse_overpass",
    "parse_railway_features",
    "parse_snapshot",
    "read_snapshot_file",
]
