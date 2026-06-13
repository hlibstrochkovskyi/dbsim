"""Tests for GTFS loading (:mod:`dbsim.ingest.gtfs`)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from dbsim.ingest import feed_url, load_feed
from dbsim.ingest.gtfs import FEEDS


def test_feed_url_known() -> None:
    assert feed_url("fv") == "https://download.gtfs.de/germany/fv_free/latest.zip"
    assert set(FEEDS) >= {"fv", "free"}


def test_feed_url_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown feed"):
        feed_url("nope")


def test_load_rejects_feed_missing_required_files(tmp_path: Path) -> None:
    import zipfile

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("agency.txt", "agency_id\n1\n")
    with pytest.raises(ValueError, match="missing required files"):
        load_feed(bad, tmp_path / "x.duckdb")


def test_table_counts(mini_db: Path) -> None:
    tables = ("agency", "stops", "routes", "trips", "stop_times", "calendar", "calendar_dates")
    con = duckdb.connect(str(mini_db), read_only=True)
    try:
        counts = {
            t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # type: ignore[index]
            for t in tables
        }
    finally:
        con.close()
    assert counts == {
        "agency": 1,
        "stops": 4,
        "routes": 2,
        "trips": 3,
        "stop_times": 9,
        "calendar": 3,
        "calendar_dates": 2,
    }


def test_times_past_midnight_parse_to_seconds(mini_db: Path) -> None:
    con = duckdb.connect(str(mini_db), read_only=True)
    try:
        row = con.execute(
            "SELECT arrival_s, departure_s FROM stop_times "
            "WHERE trip_id = 'T_NIGHT' AND stop_sequence = 1"
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    # 24:30:00 and 24:35:00 — past 24h, must not wrap.
    assert row[0] == 24 * 3600 + 30 * 60
    assert row[1] == 24 * 3600 + 35 * 60


def test_blank_times_become_null(mini_db: Path) -> None:
    con = duckdb.connect(str(mini_db), read_only=True)
    try:
        row = con.execute(
            "SELECT arrival_time, arrival_s FROM stop_times "
            "WHERE trip_id = 'T_ICE_A' AND stop_sequence = 0"
        ).fetchone()
    finally:
        con.close()
    assert row == (None, None)
