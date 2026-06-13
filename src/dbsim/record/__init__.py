"""Recording and the determinism harness.

For now this holds the canonical run-hashing used to assert that the engine is
deterministic (M0.1). The Parquet trajectory recording/replay format arrives in
M1.3.
"""

from __future__ import annotations

from dbsim.record.hashing import hash_events, hash_run

__all__ = ["hash_events", "hash_run"]
