# M3.5 — Micro-validation of the Pfäffingen zone

*Mirror of M1.4 at micro grain: does the simulated zone behave like reality?*
Micro errors compound fast, so the micro layer is **validated, not trusted**.

## Why the timetable, not GTFS-RT

The macro model was validated against observed **GTFS-RT** delays (M1.4). For this
regional zone that path is too thin: only **10** of the day's Ammertalbahn trips
appear in the RT snapshot. But a single-track line gives a *stronger* ground truth
than delays — **trains can only meet at loops**, so the operated GTFS timetable is
itself a hard test of the loop model: every scheduled meet must be physically
possible on the two loop tracks, and the line speeds/headways must make the
schedule feasible.

## Method

Run the day's trains through the coupled micro zone (M3.4) — each train's real
macro arrival at the boundary, handed off to the block-level micro simulation —
and check three things against the timetable:

1. **meet structure** — every scheduled opposing-train meet at the loop resolves
   without deadlock (the modelled infrastructure supports the operation);
2. **occupancy** — the loop is never occupied beyond its track count;
3. **capacity headroom** — the micro minimum headway is well below the operated
   train spacing.

Reproduce:

```bash
uv run dbsim micro-validate --db data/processed/gtfs-free.duckdb --date 20260616
```

## Results (Ammertalbahn, 2026-06-16)

| Metric | Value |
|---|---|
| Trains through the zone | 104 |
| Scheduled meets at the loop | **12 — all resolved, no deadlock** |
| Max simultaneous occupancy | **2** / capacity **2** |
| Micro minimum headway | 56 s |
| Observed minimum train spacing | 593 s |
| Capacity utilisation | **9 %** |

**Verdict: the zone model is consistent with the operated timetable.**

## Discussion (the quantified gap)

- **Meet structure matches reality.** The timetable schedules 12 opposing-train
  meets at Pfäffingen, and the micro model resolves every one on the two loop
  tracks without deadlock (M3.3). On a single-track line that is the decisive
  test: the modelled infrastructure (a 2-track loop) is exactly what the real
  operation requires.
- **Occupancy never exceeds capacity.** The loop holds at most 2 trains at once —
  precisely the two loop tracks used during a meet — and never more. The model
  neither under- nor over-provisions the zone.
- **The residual gap is slack, not error.** The micro minimum headway (56 s) is
  ~10× tighter than the operated spacing (593 s), so the loop runs at ~9 %
  utilisation. The "gap" between simulated capacity and observed use is the
  timetable's deliberate headroom on an hourly regional line — expected, and the
  reason no delays accumulate in the zone.

## Limitations & next steps

- **Schedule-vs-model, not realised-vs-model.** Sparse RT means this validates the
  micro model against the *operated plan*, not measured delays. A richer RT
  capture (or a denser feed) would allow an observed-delay study like M1.4; the
  `rt-capture` tooling supports building one.
- The approach blocks are a tight stub around the loop; extending the zone to the
  full inter-station single-track sections would let the model be checked against
  scheduled section run times directly.
- The natural strengthening is the **stochastic micro / coupled-zones** roadmap:
  validate how the zone degrades under sampled load, then how a disruption here
  re-materialises at a distant micro node.
