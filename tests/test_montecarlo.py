"""Tests for the Monte Carlo robustness harness (M4.3)."""

from __future__ import annotations

from dbsim.analysis.montecarlo import (
    DelayModel,
    calibrate,
    run_montecarlo,
)
from dbsim.engine.trains import ScheduledStop, TrainSchedule
from dbsim.seed import make_rng


def _train(trip_id: str, stops: list[tuple[str, int]]) -> TrainSchedule:
    # stops: (stop_id, time_s) — arrival == departure for simplicity.
    return TrainSchedule(
        trip_id,
        None,
        tuple(ScheduledStop(i, sid, t, t) for i, (sid, t) in enumerate(stops)),
    )


def _fleet() -> list[TrainSchedule]:
    # Every train passes through HUB, so it should top the fragility ranking.
    return [
        _train("T1", [("A", 0), ("HUB", 600), ("B", 1200)]),
        _train("T2", [("C", 100), ("HUB", 700), ("D", 1300)]),
        _train("T3", [("E", 200), ("HUB", 800), ("F", 1400)]),
    ]


def test_calibrate_splits_late_from_on_time() -> None:
    # 4 of 10 trains exceed the 60 s threshold.
    model = calibrate([0, 0, 0, 0, 0, 0, 90, 120, 300, 600], threshold_s=60)
    assert model.p_delayed == 0.4
    assert model.magnitudes_s == (90, 120, 300, 600)


def test_calibrate_empty_is_a_null_model() -> None:
    model = calibrate([])
    assert model.p_delayed == 0.0
    assert model.sample_primary(_fleet(), make_rng(0)) == []


def test_sample_primary_is_seed_deterministic() -> None:
    model = DelayModel(p_delayed=0.5, magnitudes_s=(300, 600))
    a = model.sample_primary(_fleet(), make_rng(7))
    b = model.sample_primary(_fleet(), make_rng(7))
    assert a == b
    # Every sampled delay targets the train's origin stop (seq 0).
    assert all(pd.seq == 0 for pd in a)


def test_montecarlo_is_reproducible_from_base_seed() -> None:
    model = DelayModel(p_delayed=0.5, magnitudes_s=(300, 600, 900))
    r1 = run_montecarlo(_fleet(), model, n_reps=50, base_seed=0)
    r2 = run_montecarlo(_fleet(), model, n_reps=50, base_seed=0)
    assert r1.outcomes == r2.outcomes
    assert r1.total_delay_percentiles() == r2.total_delay_percentiles()


def test_percentiles_are_ordered_and_distributional() -> None:
    model = DelayModel(p_delayed=0.5, magnitudes_s=(300, 600, 900))
    result = run_montecarlo(_fleet(), model, n_reps=200, base_seed=0)
    p = result.total_delay_percentiles()
    assert p[0.5] <= p[0.9] <= p[0.95] <= p[1.0]
    # With randomness there is genuine spread, not a single repeated value.
    assert p[1.0] > p[0.5]


def test_fragility_identifies_the_shared_hub() -> None:
    model = DelayModel(p_delayed=1.0, magnitudes_s=(600,))
    result = run_montecarlo(_fleet(), model, n_reps=20, base_seed=0)
    top_station = result.fragility(top=1)[0][0]
    assert top_station == "HUB"
    assert result.station_hotspot_share["HUB"] == 1.0


def test_distribution_is_stable_across_independent_halves() -> None:
    # First and last 100 reps use disjoint derived seeds → independent samples.
    model = DelayModel(p_delayed=0.5, magnitudes_s=(300, 600, 900))
    result = run_montecarlo(_fleet(), model, n_reps=400, base_seed=0)
    totals = [o.total_delay_s for o in result.outcomes]
    mean_a = sum(totals[:200]) / 200
    mean_b = sum(totals[200:]) / 200
    # The Monte Carlo mean has converged: the two halves agree within 15%.
    assert abs(mean_a - mean_b) / max(mean_a, mean_b, 1) < 0.15
