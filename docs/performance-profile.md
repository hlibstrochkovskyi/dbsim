# M1.5 — National-scale performance profile & the Rust decision

*Run the full national timetable for a day, profile it, and decide the gate:
Python fast enough → skip the Rust port; too slow → do M-Perf.*

Measured on a 14 GB-RAM machine (≈ 5 GB free), Python 3.12, single-threaded.

## Workload

The full `free` feed (all German public transport, 2026-06-14):

| | rows |
|---|---|
| trips | 1,708,973 |
| stop_times | 35,179,633 |
| stops | 690,628 |
| routes | 24,879 |

The project's scope is **rail** (GTFS `route_type = 2`). On this Sunday: 1,630
active services, **35,569 active rail trains** (375,465 trips across *all* modes —
mostly buses, out of scope).

## Stage timings (national rail day)

| Stage | Time | Notes |
|---|---|---|
| Ingest full feed → DuckDB | **22.6 s** | 35.2 M stop_times; memory-limited (3 GB) + disk spill; 0.61 GB DB |
| `load_schedules` (rail) | **11.4 s** | 35,569 trains, 483,586 scheduled stops |
| **Simulate the day** | **7.9 s** | 896,034 movement events — **112,800 events/s** |
| Peak RSS | **1.22 GB** | |

End-to-end (ingest is one-off; a run is load + simulate ≈ **19 s**). The
simulation **reproduces the schedule exactly** at national scale — correctness and
determinism hold.

## Hotspots (cProfile, simulation)

Time is spent entirely in the pure-Python event loop and movement handlers:

| Function | share |
|---|---|
| `Simulation.run` (heap loop) | ~20% self |
| `_on_arrive` / `_on_depart` / `_decide_departure` | ~bulk |
| `heapq.heappop` (896 k calls) | ~10% |
| `Event.__init__` (frozen-slots dataclass, 896 k) | ~8% |

No single pathological hotspot — it is uniformly the cost of processing ~0.9 M
Python-level events.

## Scaling note

The **all-modes** workload (375 k trips, ~10× rail, mostly buses) did **not**
finish within a 5-minute budget on this machine: it becomes **memory-bound**
(every `MovementRecord` is held in RAM; ~10× rail approaches the machine's free
memory and starts swapping), not CPU-bound. This is out of scope, and the fix —
if ever needed — is to **stream records to the Parquet recording** instead of
retaining them, which is an engineering change orthogonal to language choice.

## ➡ Decision gate: **skip Phase 1.5 (no Rust port)**

The target workload — a national **rail** day — loads and simulates in **~19 s**
within **1.2 GB**, far inside the "minutes" target. Justification:

- **Speed is a non-issue at the real scope.** ~8 s/run leaves ample headroom for
  the research workloads (A/B dispatcher comparison, Monte Carlo replications),
  which are *embarrassingly parallel across seeds* — throughput scales with cores,
  not with a rewrite.
- **The binding constraint at extreme scale is memory, not CPU.** Rust would not
  fix that; streaming the recording would. So a port would target the wrong cost.
- **Cost/benefit.** A PyO3/maturin core adds build, packaging, and maintenance
  complexity for no current benefit, against the project's "Python-first, Rust as
  a *measured* escape hatch" principle.

**Revisit only if** a later milestone proves CPU-bound — e.g. the microscopic
zone (Phase 3, signal-level blocking-time at high resolution) or very large Monte
Carlo ensembles that parallelism can't absorb. Until then, Python stays.
