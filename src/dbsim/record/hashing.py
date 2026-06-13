"""Canonical hashing of a run — the heart of the determinism harness.

Two runs are considered identical iff their processed event sequences serialise
to the same canonical bytes. We hash that serialisation (SHA-256) so tests and
CI can cheaply assert "same inputs + same seed → byte-identical run" without
storing or diffing whole event logs.

The serialisation is deliberately explicit and stable:

- events are serialised in order, one per line;
- each event becomes compact JSON with **sorted keys**, so payload key order
  never affects the hash;
- floats use ``repr`` semantics via ``json`` (round-trippable), so rounding is
  not silently introduced here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import TYPE_CHECKING

from dbsim.engine.events import Event

if TYPE_CHECKING:
    from dbsim.engine.loop import RunResult


def _canonical_event(event: Event) -> str:
    """Serialise one event to a stable, compact, key-sorted JSON string."""
    return json.dumps(
        {"t": event.time, "k": event.kind, "p": event.payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def hash_events(events: Iterable[Event]) -> str:
    """Return the SHA-256 hex digest of a sequence of processed events."""
    digest = hashlib.sha256()
    for event in events:
        digest.update(_canonical_event(event).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def hash_run(result: RunResult) -> str:
    """Return the SHA-256 hex digest of a finished run's event sequence."""
    return hash_events(result.events)
