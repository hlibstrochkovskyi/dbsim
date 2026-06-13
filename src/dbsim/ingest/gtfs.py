"""GTFS ingestion: download a feed and load it into canonical DuckDB tables.

We ingest GTFS the DuckDB-native way — a GTFS feed is just a zip of CSVs, and
DuckDB reads CSVs directly — rather than pulling in the heavy pandas/``gtfs-kit``
stack. This keeps ingestion deterministic, fast, and dependency-light, and lands
the data straight in the storage/query layer the project standardises on.

Two responsibilities:

- :func:`download_feed` — fetch a feed from gtfs.de into ``data/raw`` and pin it
  with a committed ``source.json`` manifest (per ``docs/data-versioning.md``).
- :func:`load_feed` — parse the feed's CSVs into typed canonical tables in a
  DuckDB database, parsing GTFS clock strings (which may exceed ``24:00:00``)
  into integer seconds-since-midnight for arithmetic.

The loader is **feed-agnostic**: optional GTFS columns that a given feed omits
become ``NULL`` rather than errors, so pointing it at the full national feed
later (M1.5) needs no code change.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import urllib.request
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import duckdb

#: Base URL for the open gtfs.de feeds.
_GTFS_DE_BASE = "https://download.gtfs.de/germany"

#: Known gtfs.de feeds, smallest/most-focused first. ``fv`` (Fernverkehr =
#: long-distance, ICE/IC) is the M0.2 default: tiny and the canonical setting for
#: delay-propagation study. ``free`` is the full national feed for later scaling.
FEEDS: dict[str, str] = {
    "fv": "fv_free",
    "rv": "rv_free",
    "nv": "nv_free",
    "free": "free",
}

#: Licence string recorded in the manifest (gtfs.de data is provided by DELFI).
_FEED_LICENSE = "CC-BY 4.0 (data: DELFI e.V., via gtfs.de)"


def feed_url(feed: str) -> str:
    """Return the download URL for a named feed (see :data:`FEEDS`)."""
    if feed not in FEEDS:
        raise ValueError(f"unknown feed {feed!r}; known: {sorted(FEEDS)}")
    return f"{_GTFS_DE_BASE}/{FEEDS[feed]}/latest.zip"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_feed(
    feed: str = "fv",
    *,
    data_root: Path = Path("data"),
    snapshot_date: str | None = None,
) -> Path:
    """Download a gtfs.de feed into ``data/raw`` and write its manifest.

    Args:
        feed: A key of :data:`FEEDS` (default ``"fv"``).
        data_root: The project data directory (``data/`` by default).
        snapshot_date: ``YYYY-MM-DD`` snapshot label; defaults to today (UTC).

    Returns:
        The snapshot directory containing ``feed.zip`` and ``source.json``.
    """
    snapshot_date = snapshot_date or datetime.now(UTC).strftime("%Y-%m-%d")
    url = feed_url(feed)
    dest_dir = data_root / "raw" / "gtfs" / f"gtfsde-{feed}" / snapshot_date
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "feed.zip"

    urllib.request.urlretrieve(url, zip_path)

    manifest = {
        "source": f"gtfs.de {FEEDS[feed]} (DELFI national GTFS, {feed})",
        "url": url,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "snapshot_date": snapshot_date,
        "sha256": _sha256(zip_path),
        "size_bytes": zip_path.stat().st_size,
        "license": _FEED_LICENSE,
    }
    (dest_dir / "source.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return dest_dir


# ---------------------------------------------------------------------------
# Loading GTFS CSVs into canonical DuckDB tables
# ---------------------------------------------------------------------------


def _read_header(path: Path) -> set[str]:
    """Return the set of column names present in a GTFS CSV file."""
    with path.open(encoding="utf-8-sig") as fh:
        header = fh.readline().strip()
    return {c.strip() for c in header.split(",")}


def _raw(name: str, present: set[str]) -> str:
    """SQL expression for a passthrough varchar column, or NULL if absent."""
    return f'"{name}"' if name in present else "NULL"


def _num(name: str, present: set[str], sql_type: str) -> str:
    """SQL expression TRY_CASTing a column to a numeric type (NULL-safe)."""
    return f'TRY_CAST("{name}" AS {sql_type})' if name in present else "NULL"


def _time_seconds(name: str, present: set[str]) -> str:
    """SQL expression converting a GTFS ``HH:MM:SS`` (HH may be >= 24) to seconds.

    Blank/missing times become NULL. ``TRY_CAST`` keeps malformed values from
    aborting the load.
    """
    if name not in present:
        return "NULL"
    col = f'"{name}"'
    return (
        f"CASE WHEN {col} IS NULL OR {col} = '' THEN NULL ELSE "
        f"TRY_CAST(split_part({col}, ':', 1) AS INTEGER) * 3600 + "
        f"TRY_CAST(split_part({col}, ':', 2) AS INTEGER) * 60 + "
        f"TRY_CAST(split_part({col}, ':', 3) AS INTEGER) END"
    )


def _from_csv(path: Path) -> str:
    """FROM-clause reading a GTFS CSV as all-varchar (deterministic, no inference)."""
    return f"read_csv('{path.as_posix()}', header = true, all_varchar = true)"


def _build_select(table: str, path: Path) -> str:
    """Build the typed SELECT that projects a raw GTFS file to a canonical table."""
    present = _read_header(path)
    src = _from_csv(path)

    if table == "agency":
        cols = (
            f"{_raw('agency_id', present)} AS agency_id, "
            f"{_raw('agency_name', present)} AS agency_name, "
            f"{_raw('agency_timezone', present)} AS agency_timezone"
        )
    elif table == "stops":
        cols = (
            f"{_raw('stop_id', present)} AS stop_id, "
            f"{_raw('stop_name', present)} AS stop_name, "
            f"{_raw('parent_station', present)} AS parent_station, "
            f"{_num('stop_lat', present, 'DOUBLE')} AS stop_lat, "
            f"{_num('stop_lon', present, 'DOUBLE')} AS stop_lon, "
            f"{_num('location_type', present, 'INTEGER')} AS location_type, "
            f"{_raw('platform_code', present)} AS platform_code"
        )
    elif table == "routes":
        cols = (
            f"{_raw('route_id', present)} AS route_id, "
            f"{_raw('route_short_name', present)} AS route_short_name, "
            f"{_raw('route_long_name', present)} AS route_long_name, "
            f"{_num('route_type', present, 'INTEGER')} AS route_type, "
            f"{_raw('agency_id', present)} AS agency_id"
        )
    elif table == "trips":
        cols = (
            f"{_raw('trip_id', present)} AS trip_id, "
            f"{_raw('route_id', present)} AS route_id, "
            f"{_raw('service_id', present)} AS service_id, "
            f"{_raw('trip_headsign', present)} AS trip_headsign"
        )
    elif table == "stop_times":
        cols = (
            f"{_raw('trip_id', present)} AS trip_id, "
            f"{_num('stop_sequence', present, 'INTEGER')} AS stop_sequence, "
            f"{_raw('stop_id', present)} AS stop_id, "
            f"{_raw('arrival_time', present)} AS arrival_time, "
            f"{_raw('departure_time', present)} AS departure_time, "
            f"{_time_seconds('arrival_time', present)} AS arrival_s, "
            f"{_time_seconds('departure_time', present)} AS departure_s, "
            f"{_raw('stop_headsign', present)} AS stop_headsign"
        )
    elif table == "calendar":
        cols = (
            f"{_raw('service_id', present)} AS service_id, "
            f"{_num('monday', present, 'INTEGER')} AS monday, "
            f"{_num('tuesday', present, 'INTEGER')} AS tuesday, "
            f"{_num('wednesday', present, 'INTEGER')} AS wednesday, "
            f"{_num('thursday', present, 'INTEGER')} AS thursday, "
            f"{_num('friday', present, 'INTEGER')} AS friday, "
            f"{_num('saturday', present, 'INTEGER')} AS saturday, "
            f"{_num('sunday', present, 'INTEGER')} AS sunday, "
            f"{_num('start_date', present, 'INTEGER')} AS start_date, "
            f"{_num('end_date', present, 'INTEGER')} AS end_date"
        )
    elif table == "calendar_dates":
        cols = (
            f"{_raw('service_id', present)} AS service_id, "
            f"{_num('date', present, 'INTEGER')} AS date, "
            f"{_num('exception_type', present, 'INTEGER')} AS exception_type"
        )
    else:  # pragma: no cover - guarded by the caller's table list
        raise ValueError(f"unknown canonical table {table!r}")

    return f"SELECT {cols} FROM {src}"


#: GTFS file -> canonical table. ``calendar``/``calendar_dates`` are optional in
#: GTFS (a feed may use only one), so the loader tolerates either being absent.
_TABLES: dict[str, str] = {
    "agency.txt": "agency",
    "stops.txt": "stops",
    "routes.txt": "routes",
    "trips.txt": "trips",
    "stop_times.txt": "stop_times",
    "calendar.txt": "calendar",
    "calendar_dates.txt": "calendar_dates",
}

#: Files that must be present for a usable timetable.
_REQUIRED_FILES = frozenset({"stops.txt", "routes.txt", "trips.txt", "stop_times.txt"})

#: Empty-schema DDL for optional tables, so the query layer can rely on them
#: existing even when a feed omits the corresponding file.
_EMPTY_DDL: dict[str, str] = {
    "calendar": (
        "CREATE OR REPLACE TABLE calendar ("
        "service_id VARCHAR, monday INTEGER, tuesday INTEGER, wednesday INTEGER, "
        "thursday INTEGER, friday INTEGER, saturday INTEGER, sunday INTEGER, "
        "start_date INTEGER, end_date INTEGER)"
    ),
    "calendar_dates": (
        "CREATE OR REPLACE TABLE calendar_dates ("
        "service_id VARCHAR, date INTEGER, exception_type INTEGER)"
    ),
}


def load_feed(zip_path: Path, db_path: Path) -> Path:
    """Load a GTFS feed zip into canonical tables in a DuckDB database file.

    Args:
        zip_path: Path to the GTFS ``.zip`` (as produced by :func:`download_feed`).
        db_path: Path to the DuckDB file to (re)create the tables in.

    Returns:
        ``db_path``.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(zip_path) as zf:
        tmp_dir = Path(tmp)
        members = {Path(n).name for n in zf.namelist()}
        missing = _REQUIRED_FILES - members
        if missing:
            raise ValueError(f"feed {zip_path.name} is missing required files: {sorted(missing)}")
        zf.extractall(tmp_dir)
        _load_extracted(tmp_dir, db_path, members)
    return db_path


def _load_extracted(src_dir: Path, db_path: Path, members: Iterable[str]) -> None:
    member_set = set(members)
    con = duckdb.connect(str(db_path))
    try:
        for filename, table in _TABLES.items():
            if filename not in member_set:
                # Optional file absent: create an empty, correctly-shaped table so
                # the query layer can always assume it exists (calendar tables).
                if table in _EMPTY_DDL:
                    con.execute(_EMPTY_DDL[table])
                continue
            select = _build_select(table, src_dir / filename)
            con.execute(f"CREATE OR REPLACE TABLE {table} AS {select}")
    finally:
        con.close()
