"""Recording and the determinism harness.

For now this holds the canonical run-hashing used to assert that the engine is
deterministic (M0.1). The Parquet trajectory recording/replay format arrives in
M1.3.
"""

from __future__ import annotations

from dbsim.record.hashing import hash_events, hash_run
from dbsim.record.recording import (
    Position,
    Recording,
    RunMeta,
    load_recording,
    write_recording,
)

__all__ = [
    "Position",
    "Recording",
    "RunMeta",
    "hash_events",
    "hash_run",
    "load_recording",
    "write_recording",
]
