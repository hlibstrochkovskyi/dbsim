"""Central seed control — the single source of randomness for the simulation.

Determinism is a non-negotiable guiding principle: the same inputs + the same
seed must produce a byte-identical run. To make that achievable, *all*
randomness in the engine must flow through here, never through the global
``random`` module or ``numpy.random`` defaults.

Usage::

    from dbsim.seed import make_rng

    rng = make_rng(seed=42)
    x = rng.random()

When a component needs its own independent-but-reproducible stream (e.g. one RNG
per train, per scenario), derive a child seed with :func:`derive_seed` rather
than reusing or guessing offsets — derivation is stable across Python versions
and platforms because it is based on a fixed hash, not ``hash()``.
"""

from __future__ import annotations

import hashlib
import random

#: The default seed used when none is supplied. Chosen to be boring and explicit.
DEFAULT_SEED: int = 0


def make_rng(seed: int = DEFAULT_SEED) -> random.Random:
    """Return a freshly seeded, independent pseudo-random generator.

    Uses :class:`random.Random` (the Mersenne Twister), which is deterministic
    across platforms for a given seed.
    """
    return random.Random(seed)


def derive_seed(base_seed: int, label: str) -> int:
    """Deterministically derive a child seed from a base seed and a label.

    This lets each component own a reproducible sub-stream without coordinating
    integer offsets. The derivation is stable across runs, processes, and Python
    versions (it does not use the salted built-in ``hash()``).

    Example::

        train_seed = derive_seed(run_seed, f"train:{train_id}")
        train_rng = make_rng(train_seed)
    """
    digest = hashlib.sha256(f"{base_seed}:{label}".encode()).digest()
    # 64 bits of the digest is ample entropy for seeding and keeps values small.
    return int.from_bytes(digest[:8], "big")
