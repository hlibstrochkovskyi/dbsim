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

__all__ = [
    "DEFAULT_RT_URL",
    "FEEDS",
    "TripDelay",
    "capture",
    "download_feed",
    "feed_url",
    "fetch_snapshot",
    "load_feed",
    "parse_snapshot",
    "read_snapshot_file",
]
