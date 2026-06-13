"""Tests for the timetable query layer (:mod:`dbsim.model.timetable`)."""

from __future__ import annotations

from dbsim.model import Timetable

# Calendar dates in the fixture:
#   2026-06-16 is a Tuesday; WD is removed and WE is added on that date.
TUESDAY = 20260616
WEDNESDAY = 20260617
SATURDAY = 20260620


def test_services_on_applies_remove_and_add_exceptions(mini_tt: Timetable) -> None:
    # WD removed, WE added → only WE + DAILY run on this Tuesday.
    assert mini_tt.services_on(TUESDAY) == {"WE", "DAILY"}


def test_services_on_regular_weekday(mini_tt: Timetable) -> None:
    assert mini_tt.services_on(WEDNESDAY) == {"WD", "DAILY"}


def test_services_on_regular_weekend(mini_tt: Timetable) -> None:
    assert mini_tt.services_on(SATURDAY) == {"WE", "DAILY"}


def test_resolve_station_includes_platform_children(mini_tt: Timetable) -> None:
    # Querying the parent station name must pull in its child platform stop.
    assert mini_tt.resolve_station_stops("Musterstadt Hbf") == {"S_HBF", "S_HBF_1"}


def test_unknown_station_resolves_empty(mini_tt: Timetable) -> None:
    assert mini_tt.resolve_station_stops("Nowhere") == set()


def test_trains_through_station_respects_calendar(mini_tt: Timetable) -> None:
    # On the Tuesday with exceptions: T_ICE_A (WD) is gone; the night train
    # (DAILY) and the regional (WE, added) call at the station.
    trips = {c.trip_id for c in mini_tt.trains_through_station("Musterstadt Hbf", TUESDAY)}
    assert trips == {"T_NIGHT", "T_RB_WE"}


def test_trains_through_station_regular_weekday(mini_tt: Timetable) -> None:
    trips = {c.trip_id for c in mini_tt.trains_through_station("Musterstadt Hbf", WEDNESDAY)}
    assert trips == {"T_ICE_A", "T_NIGHT"}


def test_trains_through_station_ordered_by_departure(mini_tt: Timetable) -> None:
    calls = mini_tt.trains_through_station("Musterstadt Hbf", WEDNESDAY)
    deps = [c.departure_s for c in calls]
    assert None not in deps
    assert deps == sorted(d for d in deps if d is not None)


def test_trains_through_unknown_station_is_empty(mini_tt: Timetable) -> None:
    assert mini_tt.trains_through_station("Nowhere", WEDNESDAY) == []


def test_trip_stop_sequence_reconstructs_order_and_times(mini_tt: Timetable) -> None:
    calls = mini_tt.trip_stop_sequence("T_ICE_A")
    assert [c.stop_sequence for c in calls] == [0, 1, 2]
    assert [c.stop_name for c in calls] == ["Nordstadt", "Musterstadt Hbf", "Suedstadt"]
    # Origin departs 08:00:00; middle stop arrives 09:00:00.
    assert calls[0].departure_s == 8 * 3600
    assert calls[1].arrival_s == 9 * 3600
    # Terminus has no departure.
    assert calls[2].departure_time is None


def test_trip_stop_sequence_handles_past_midnight(mini_tt: Timetable) -> None:
    calls = mini_tt.trip_stop_sequence("T_NIGHT")
    assert calls[-1].arrival_s == 25 * 3600 + 10 * 60


def test_unknown_trip_is_empty(mini_tt: Timetable) -> None:
    assert mini_tt.trip_stop_sequence("NOPE") == []
