"""Tests for the macroscopic timetable graph (:mod:`dbsim.model.graph`)."""

from __future__ import annotations

from dbsim.model import TimetableGraph, format_hms

H = 3600


def test_format_hms_handles_past_midnight() -> None:
    assert format_hms(8 * H) == "08:00:00"
    assert format_hms(25 * H + 10 * 60) == "25:10:00"
    assert format_hms(None) == "--:--:--"


def test_stats_counts_and_connectivity(mini_graph: TimetableGraph) -> None:
    stats = mini_graph.stats()
    # WD + DAILY active on Wednesday: trips T_ICE_A and T_NIGHT, 4 event nodes each.
    assert stats.event_nodes == 8
    assert stats.stations == 3
    assert stats.weakly_connected_components == 1
    assert stats.largest_component_stations == 3
    assert stats.station_edges == 4


def test_direct_journey_matches_schedule(mini_graph: TimetableGraph) -> None:
    journey = mini_graph.plan_journey("Nordstadt", "Suedstadt", 7 * H)
    assert journey is not None
    assert journey.n_transfers == 0
    leg = journey.legs[0]
    assert leg.line == "ICE 100"
    assert leg.board_stop_name == "Nordstadt"
    assert leg.board_time_s == 8 * H
    assert leg.alight_stop_name == "Suedstadt"
    assert leg.alight_time_s == 10 * H


def test_no_journey_after_last_train(mini_graph: TimetableGraph) -> None:
    # The only Nordstadt->Suedstadt train (WD) has departed; WE does not run today.
    assert mini_graph.plan_journey("Nordstadt", "Suedstadt", 9 * H) is None


def test_unknown_station_returns_none(mini_graph: TimetableGraph) -> None:
    assert mini_graph.plan_journey("Nowhere", "Suedstadt", 0) is None
    assert mini_graph.plan_journey("Nordstadt", "Nowhere", 0) is None


def test_transfer_journey_has_two_legs(transfer_graph: TimetableGraph) -> None:
    # No direct A->C: must ride L1 to Bravo, then L2 to Charlie.
    journey = transfer_graph.plan_journey("Alpha", "Charlie", 7 * H)
    assert journey is not None
    assert journey.n_transfers == 1
    first, second = journey.legs
    assert (first.line, first.board_stop_name, first.alight_stop_name) == ("L1", "Alpha", "Bravo")
    assert (second.line, second.board_stop_name, second.alight_stop_name) == (
        "L2",
        "Bravo",
        "Charlie",
    )
    assert journey.arrive_time_s == 10 * H


def test_transfer_journey_includes_connection_wait(transfer_graph: TimetableGraph) -> None:
    journey = transfer_graph.plan_journey("Alpha", "Charlie", 7 * H)
    assert journey is not None
    arrive_b = journey.legs[0].alight_time_s  # 09:00
    depart_b = journey.legs[1].board_time_s  # 09:20
    assert depart_b - arrive_b == 20 * 60


def test_departure_after_constraint_waits_at_origin(transfer_graph: TimetableGraph) -> None:
    # Asking to leave at 06:00 still boards the 08:00 train (waits at origin).
    journey = transfer_graph.plan_journey("Alpha", "Charlie", 6 * H)
    assert journey is not None
    assert journey.depart_time_s == 8 * H


def test_journey_is_deterministic(transfer_graph: TimetableGraph) -> None:
    a = transfer_graph.plan_journey("Alpha", "Charlie", 7 * H)
    b = transfer_graph.plan_journey("Alpha", "Charlie", 7 * H)
    assert a == b
