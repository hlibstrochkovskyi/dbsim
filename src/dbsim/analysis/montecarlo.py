"""Monte Carlo robustness analysis (M4.3).

The macro engine is deterministic per seed (M1.1). Robustness asks a different
question: *given the day-to-day randomness of primary delays, how does the
timetable behave on average — and where is it fragile?* We answer it by

1. **Calibrating** a primary-delay model from real GTFS-RT observations — the
   probability a train starts late and the empirical distribution of how late;
2. running **N seeded replications**, each sampling an independent set of
   primary delays and propagating them through :class:`MacroSimulation`;
3. **aggregating** the outcomes into delay percentiles (the distribution, not a
   single number) and a **fragility ranking** of the stations that accumulate
   the most delay across replications.

Determinism is preserved at the *experiment* level: a Monte Carlo run is fully
reproducible from its ``base_seed`` because every replication's RNG is derived
via :func:`derive_seed`. Two runs with the same inputs give identical
percentiles — randomness is sampled, never leaked.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from random import Random

from dbsim.engine.trains import (
    Connection,
    MacroSimulation,
    PrimaryDelay,
    TrainSchedule,
)
from dbsim.ingest.gtfsrt import read_snapshot_file
from dbsim.seed import derive_seed, make_rng

#: Default threshold (s) above which an origin delay counts as a real primary
#: delay rather than measurement noise / rounding.
DEFAULT_THRESHOLD_S = 60


@dataclass(frozen=True, slots=True)
class DelayModel:
    """A calibrated primary-delay model: how often, and how much, trains start late.

    Each train independently starts late with probability ``p_delayed``; when it
    does, the lateness is drawn (bootstrap) from the empirical ``magnitudes_s``
    pool. This non-parametric model inherits the real feed's shape — heavy tail
    included — instead of assuming a convenient distribution.
    """

    p_delayed: float
    magnitudes_s: tuple[int, ...]
    threshold_s: int = DEFAULT_THRESHOLD_S

    @property
    def mean_primary_s(self) -> float:
        """Expected primary delay per train (s), including on-time trains as 0."""
        if not self.magnitudes_s:
            return 0.0
        return self.p_delayed * (sum(self.magnitudes_s) / len(self.magnitudes_s))

    def sample_one(self, rng: Random) -> int:
        """Draw a single primary delay (s): 0 if on time, else a bootstrap magnitude."""
        if not self.magnitudes_s or rng.random() >= self.p_delayed:
            return 0
        return rng.choice(self.magnitudes_s)

    def sample_primary(self, schedules: Sequence[TrainSchedule], rng: Random) -> list[PrimaryDelay]:
        """Draw an independent set of origin primary delays for one replication."""
        out: list[PrimaryDelay] = []
        if not self.magnitudes_s:
            return out
        for sched in schedules:
            if not sched.stops:
                continue
            if rng.random() < self.p_delayed:
                origin = sched.stops[0]
                out.append(PrimaryDelay(sched.trip_id, origin.seq, rng.choice(self.magnitudes_s)))
        return out


def calibrate(
    origin_delays: Iterable[int], *, threshold_s: int = DEFAULT_THRESHOLD_S
) -> DelayModel:
    """Calibrate a :class:`DelayModel` from observed origin delays (seconds).

    ``p_delayed`` is the fraction of trains whose origin delay exceeds the
    threshold; ``magnitudes_s`` is the pool of those above-threshold delays.
    """
    delays = list(origin_delays)
    if not delays:
        return DelayModel(p_delayed=0.0, magnitudes_s=(), threshold_s=threshold_s)
    late = tuple(d for d in delays if d >= threshold_s)
    return DelayModel(
        p_delayed=len(late) / len(delays),
        magnitudes_s=late,
        threshold_s=threshold_s,
    )


def origin_delays_from_snapshot(snapshot_path: Path) -> list[int]:
    """Approximate each trip's origin delay from a GTFS-RT snapshot.

    Without the full static feed we cannot know each trip's true origin stop, so
    we take the delay at the **earliest stop_sequence reported** for the trip as
    a proxy. This is an upper-bounded approximation (a train's earliest *reported*
    stop may be a few stops in), but it captures the real distribution's shape
    and tail, which is what the model needs. For true origins, calibrate from the
    full feed via :func:`build_schedules_for_trips` instead.
    """
    _ts, delays = read_snapshot_file(snapshot_path)
    earliest: dict[str, tuple[int, int]] = {}
    for d in delays:
        delay_s = d.departure_delay_s if d.departure_delay_s is not None else d.arrival_delay_s
        if delay_s is None:
            continue
        prev = earliest.get(d.trip_id)
        if prev is None or d.stop_sequence < prev[0]:
            earliest[d.trip_id] = (d.stop_sequence, delay_s)
    return [seq_delay[1] for seq_delay in earliest.values()]


@dataclass(frozen=True, slots=True)
class RepOutcome:
    """The metrics of a single Monte Carlo replication."""

    seed: int
    n_primary: int
    total_delay_s: int
    max_delay_s: int
    delayed_events: int


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile of an already-sorted sequence (q in [0,1])."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    """Aggregated outcomes across all replications."""

    outcomes: tuple[RepOutcome, ...]
    #: Mean positive delay (s) accumulated at each station, averaged over reps.
    station_mean_delay_s: dict[str, float]
    #: Fraction of reps in which each station was a top-k hotspot.
    station_hotspot_share: dict[str, float]

    @property
    def n_reps(self) -> int:
        return len(self.outcomes)

    def total_delay_percentiles(
        self, qs: Sequence[float] = (0.5, 0.9, 0.95, 1.0)
    ) -> dict[float, float]:
        """Percentiles of network-wide total delay (s) across replications."""
        vals = sorted(float(o.total_delay_s) for o in self.outcomes)
        return {q: _percentile(vals, q) for q in qs}

    def mean_total_delay_s(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(o.total_delay_s for o in self.outcomes) / len(self.outcomes)

    def fragility(self, top: int = 10) -> list[tuple[str, float, float]]:
        """The ``top`` most fragile stations: (stop_id, mean delay s, hotspot share)."""
        ranked = sorted(
            self.station_mean_delay_s.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )
        return [
            (stop, mean_s, self.station_hotspot_share.get(stop, 0.0))
            for stop, mean_s in ranked[:top]
        ]


def run_montecarlo(
    schedules: Sequence[TrainSchedule],
    model: DelayModel,
    *,
    n_reps: int = 200,
    base_seed: int = 0,
    connections: Iterable[Connection] | None = None,
    min_dwell_s: int = 0,
    hotspot_top: int = 5,
) -> MonteCarloResult:
    """Run ``n_reps`` seeded replications and aggregate the outcome distribution.

    Each replication derives an independent RNG from ``base_seed`` (so the whole
    experiment is reproducible), samples primary delays from ``model``, runs the
    macro simulation, and records both network metrics and per-station delay.
    """
    conns = tuple(connections or ())
    sched_list = list(schedules)
    outcomes: list[RepOutcome] = []
    station_delay_sum: dict[str, float] = defaultdict(float)
    station_hotspot_count: dict[str, int] = defaultdict(int)

    for i in range(n_reps):
        seed = derive_seed(base_seed, f"rep:{i}")
        rng = make_rng(seed)
        primary = model.sample_primary(sched_list, rng)
        macro = MacroSimulation(
            sched_list,
            seed=seed,
            min_dwell_s=min_dwell_s,
            primary_delays=primary,
            connections=conns,
        )
        macro.run()

        per_station: dict[str, int] = defaultdict(int)
        for rec in macro.records:
            dev = rec.deviation_s or 0
            if dev > 0:
                per_station[rec.stop_id] += dev
        for stop_id, dev_sum in per_station.items():
            station_delay_sum[stop_id] += dev_sum
        for stop_id, _dev in sorted(per_station.items(), key=lambda kv: (-kv[1], kv[0]))[
            :hotspot_top
        ]:
            station_hotspot_count[stop_id] += 1

        outcomes.append(
            RepOutcome(
                seed=seed,
                n_primary=len(primary),
                total_delay_s=macro.total_delay_s(),
                max_delay_s=macro.max_abs_deviation_s(),
                delayed_events=macro.delayed_event_count(),
            )
        )

    reps = max(n_reps, 1)
    station_mean = {s: total / reps for s, total in station_delay_sum.items()}
    station_share = {s: count / reps for s, count in station_hotspot_count.items()}
    return MonteCarloResult(
        outcomes=tuple(outcomes),
        station_mean_delay_s=station_mean,
        station_hotspot_share=station_share,
    )
