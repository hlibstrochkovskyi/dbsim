# Data versioning convention

Determinism is a guiding principle, and it extends to data: a run is only
reproducible if you know *exactly which* timetable, real-time feed, and OSM
extract produced it. Datasets are large, regenerable, and often licensed, so we
**never commit them to git** (see `.gitignore`). Instead we commit *metadata*
that pins each dataset, and keep the bytes out of the repo.

## Layout

```
data/
├── raw/         # Immutable downloads, exactly as fetched (git-ignored)
└── processed/   # Canonical DuckDB / Parquet models built from raw (git-ignored)
```

- **`raw/` is append-only and never edited.** If a source changes, fetch a new
  snapshot under a new versioned name — don't overwrite.
- **`processed/` is fully derivable** from `raw/` + code. It can be deleted and
  rebuilt at any time; never hand-edit it.

## Naming: pin the source and the snapshot

Every dataset lives under a directory that encodes **source** and **snapshot
date** (the day the data describes or was retrieved), so runs are traceable:

```
data/raw/gtfs/delfi/2026-06-01/            # DELFI national GTFS, snapshot date
data/raw/gtfs-rt/gtfsde/2026-06-01/        # GTFS-RT realtime capture for that day
data/raw/osm/openrailwaymap/frankfurt/2026-06-01/
```

## Pinning: a `source.json` next to every snapshot

Each snapshot directory carries a small, **committed** manifest so the dataset
is reproducible even though its bytes are not in git. Place it at
`data/raw/<source>/<snapshot>/source.json` and **commit only the manifest**
(add an explicit allow-rule in `.gitignore` when the first real dataset lands):

```json
{
  "source": "DELFI national GTFS",
  "url": "https://gtfs.de/de/feeds/de_full/",
  "retrieved_at": "2026-06-01T08:00:00Z",
  "snapshot_date": "2026-06-01",
  "sha256": "<sha256 of the downloaded archive>",
  "license": "CC-BY 4.0 (DELFI)",
  "notes": "Full DE feed; used for M0.2 ingestion."
}
```

Compute the checksum with `sha256sum <file>` and record it. A run that consumes
a dataset should reference its snapshot path + `sha256`, so the validation work
in M1.4 can state precisely which inputs it used.

## Rules of thumb

1. Raw bytes never enter git. Manifests (`source.json`) and code do.
2. One snapshot = one immutable directory, named by source + date.
3. `processed/` is disposable; the pipeline that builds it is the source of truth.
4. If you can't name the snapshot and its checksum, the run isn't reproducible.
