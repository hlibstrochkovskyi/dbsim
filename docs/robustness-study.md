# Monte Carlo robustness study (M4.3)

*How does the timetable behave under the day-to-day randomness of primary delays,
and where is it fragile?* The macro engine is deterministic per seed, so a single
run answers "what happens if **this** train is late." Robustness needs the
**distribution** of outcomes over many plausible days — and the places that are
consistently fragile across them.

## Method

1. **Calibrate** a primary-delay model from a real GTFS-RT snapshot
   (`analysis/montecarlo.calibrate`). For each trip we take the delay at its
   earliest reported stop as a proxy for its origin delay, then split on a 60 s
   threshold: `p_delayed` is the fraction starting late, and the above-threshold
   delays form an empirical bootstrap pool. The model is **non-parametric** — it
   inherits the feed's real heavy tail rather than assuming a tidy distribution.
2. **Replicate.** Each of N replications derives an independent RNG from the
   experiment `base_seed` (via `derive_seed`), samples one primary delay per
   train (late with probability `p_delayed`, magnitude drawn from the pool),
   injects them at each train's origin, and propagates them through
   `MacroSimulation`.
3. **Aggregate** into the distribution of network-wide total delay (percentiles)
   and a **fragility ranking** — the stations that accumulate the most positive
   delay, averaged across replications, plus how often each is a top-5 hotspot.

Determinism is preserved at the *experiment* level: a Monte Carlo run is fully
reproducible from its `base_seed` (verified — two runs produce byte-identical
output), because randomness is **sampled**, never leaked.

## Calibration (snapshot 2026-06-14 10:10)

From the nationwide GTFS-RT feed (`snapshot-101016.pb`, 33,597 trips reporting):

| quantity | value |
|---|---|
| P(train starts late, ≥ 60 s) | **12.7 %** |
| late-delay pool size | 4,262 trains |
| late magnitude — median | 1.7 min |
| late magnitude — p90 | 5.0 min |
| late magnitude — max | **123 min** (the tail) |
| mean primary delay per train | 0.4 min |

Most trains start on time; a minority start late, and a thin tail starts *very*
late. That shape is the whole point — it is what drives the spread below.

## Results

Corridor Frankfurt(Main)Hbf – Hannover Hbf, service date 2026-06-16, 159 trains,
**500 replications**, RT-calibrated model:

```
uv run dbsim montecarlo --db data/processed/gtfs-fv.duckdb --date 20260616 \
    --reps 500 --snapshot data/raw/gtfsrt/20260614/snapshot-101016.pb
```

**Distribution of network-wide total delay**

| percentile | total delay |
|---|---|
| mean | 324 min |
| p50 | 173 min |
| p90 | 721 min |
| p95 | 1,148 min |
| max | **3,513 min** |

The distribution is strongly right-skewed: a *typical* day costs ~3 h of summed
delay, but the worst 5 % of days cost 6–20×  that. A single mean would have
hidden the risk entirely — robustness lives in the tail.

**Fragility hotspots** (mean accumulated delay across replications)

| station | mean delay | top-5 hotspot share |
|---|---|---|
| Frankfurt(Main)Hbf | 20 min | 39 % |
| Fulda | 14 min | 19 % |
| Hamburg Hbf | 14 min | 38 % |
| Kassel-Wilhelmshöhe | 13 min | 12 % |
| Hannover Hbf | 13 min | 23 % |
| Hamburg-Harburg(S) | 12 min | 26 % |
| Nürnberg Hbf | 11 min | 25 % |
| Würzburg Hbf | 10 min | 17 % |

The ranking is dominated by the corridor's major hubs — **Frankfurt Hbf** is the
single most fragile node, accumulating the most delay and appearing in the
worst-5 on 39 % of simulated days. These are the points where targeted recovery
margin or dispatching attention would buy the most network-wide robustness.

## Stability / acceptance

- **Reproducible** — identical `base_seed` ⇒ identical percentiles and ranking
  (tested; confirmed by hashing CLI output across two runs).
- **Converged** — splitting the replications into two independent halves, the
  mean total delay agrees within 15 % (`test_distribution_is_stable_across_independent_halves`).
- **Fragile points identified** — the ranking is non-degenerate and led by the
  corridor's principal junction.

Acceptance (M4.3): *distributional results are stable; fragile points in the
timetable are identified.* ✅
