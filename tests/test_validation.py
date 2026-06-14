"""Tests for GTFS-RT ingestion and validation (M1.4).

A synthetic GTFS-RT protobuf is built in-memory and the ``gtfs_mini`` fixture
stands in for the full static feed, so these run with no network.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from google.transit import gtfs_realtime_pb2

from dbsim.analysis.validation import (
    _berlin_midnight_epoch,
    build_schedules_for_trips,
    run_validation,
)
from dbsim.ingest import parse_snapshot

FIXTURES = Path(__file__).parent / "fixtures" / "gtfs_mini"
DATE = 20260617  # a Wednesday; T_ICE_A (service WD) runs


def _feed_zip(tmp_path: Path) -> Path:
    """Zip the gtfs_mini fixture as a stand-in full feed."""
    zip_path = tmp_path / "feed.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for txt in sorted(FIXTURES.glob("*.txt")):
            zf.write(txt, arcname=txt.name)
    return zip_path


def _make_snapshot(
    ts: int, trip_id: str, stops: list[tuple[int, str, int | None, int | None]]
) -> bytes:
    """Serialize a one-trip GTFS-RT snapshot."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = ts
    entity = feed.entity.add()
    entity.id = "e1"
    tu = entity.trip_update
    tu.trip.trip_id = trip_id
    for seq, stop_id, arr, dep in stops:
        stu = tu.stop_time_update.add()
        stu.stop_sequence = seq
        stu.stop_id = stop_id
        if arr is not None:
            stu.arrival.delay = arr
        if dep is not None:
            stu.departure.delay = dep
    return bytes(feed.SerializeToString())


def test_parse_snapshot_extracts_delays() -> None:
    data = _make_snapshot(1000, "T1", [(0, "A", None, 30), (1, "B", 45, 50)])
    ts, delays = parse_snapshot(data)
    assert ts == 1000
    assert len(delays) == 2
    origin = next(d for d in delays if d.stop_sequence == 0)
    assert origin.arrival_delay_s is None
    assert origin.departure_delay_s == 30
    mid = next(d for d in delays if d.stop_sequence == 1)
    assert (mid.arrival_delay_s, mid.departure_delay_s) == (45, 50)


def test_build_schedules_for_trips(tmp_path: Path) -> None:
    schedules = build_schedules_for_trips(_feed_zip(tmp_path), {"T_ICE_A"})
    assert len(schedules) == 1
    s = schedules[0]
    assert s.trip_id == "T_ICE_A"
    assert [st.seq for st in s.stops] == [0, 1, 2]
    assert s.stops[0].departure_s == 8 * 3600  # 08:00


def _snapshot_file(tmp_path: Path, ts: int) -> Path:
    # T_ICE_A: origin +600 s; observed arrivals +500 (seq1), +400 (seq2).
    data = _make_snapshot(
        ts,
        "T_ICE_A",
        [(0, "S_NORD", None, 600), (1, "S_HBF_1", 500, None), (2, "S_SUED", 400, None)],
    )
    path = tmp_path / "snap.pb"
    path.write_bytes(data)
    return path


def test_validation_pairs_and_observed(tmp_path: Path) -> None:
    midnight = _berlin_midnight_epoch(DATE)
    ts = int(midnight + 40000)  # after both downstream arrivals → both realized
    snap = _snapshot_file(tmp_path, ts)
    report, pairs = run_validation(snap, _feed_zip(tmp_path), DATE)

    assert report.n_pairs == 2  # seq1 and seq2 (origin excluded)
    by_seq = {p.seq: p for p in pairs}
    assert by_seq[1].observed_s == 500
    assert by_seq[2].observed_s == 400
    assert all(p.origin_delay_s == 600 for p in pairs)


def test_validation_realized_filter(tmp_path: Path) -> None:
    midnight = _berlin_midnight_epoch(DATE)
    # Snapshot time after seq1's realized arrival but before seq2's → only seq1.
    ts = int(midnight + 33000)
    snap = _snapshot_file(tmp_path, ts)
    report, pairs = run_validation(snap, _feed_zip(tmp_path), DATE)
    assert report.n_pairs == 1
    assert pairs[0].seq == 1


def test_validation_simulated_matches_engine(tmp_path: Path) -> None:
    # The simulated downstream delay must reflect dwell recovery: origin +600 s,
    # 300 s scheduled dwell at the Hbf → departs there only 300 s late, so the
    # arrival at the next stop is +300 s, not +600 s.
    midnight = _berlin_midnight_epoch(DATE)
    snap = _snapshot_file(tmp_path, int(midnight + 40000))
    _report, pairs = run_validation(snap, _feed_zip(tmp_path), DATE)
    by_seq = {p.seq: p for p in pairs}
    assert by_seq[1].simulated_s == 600  # arrives Hbf still 600 s late
    assert by_seq[2].simulated_s == 300  # recovered the 300 s dwell slack


def test_empty_snapshot_yields_no_pairs(tmp_path: Path) -> None:
    data = _make_snapshot(1000, "UNKNOWN_TRIP", [(0, "X", None, 60)])
    path = tmp_path / "snap.pb"
    path.write_bytes(data)
    report, pairs = run_validation(path, _feed_zip(tmp_path), DATE)
    assert report.n_pairs == 0
    assert pairs == []
