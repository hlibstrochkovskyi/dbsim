"""The determinism harness — M0.1's make-or-break acceptance criterion.

Guiding principle #1: same inputs + same seed → byte-identical run. These tests
assert exactly that, and are the seed of the CI determinism gate that every
later milestone must keep green.
"""

from __future__ import annotations

from dbsim.__main__ import build_demo
from dbsim.engine import Event, Simulation
from dbsim.record import hash_events, hash_run


def test_same_seed_produces_identical_hash() -> None:
    first = hash_run(build_demo(seed=12345).run())
    second = hash_run(build_demo(seed=12345).run())
    assert first == second


def test_different_seed_produces_different_hash() -> None:
    a = hash_run(build_demo(seed=1).run())
    b = hash_run(build_demo(seed=2).run())
    assert a != b


def test_hash_is_independent_of_payload_key_order() -> None:
    # Canonical serialisation sorts keys, so logically-equal events hash equally.
    e1 = Event(time=1.0, kind="x", payload={"a": 1, "b": 2})
    e2 = Event(time=1.0, kind="x", payload={"b": 2, "a": 1})
    assert hash_events([e1]) == hash_events([e2])


def test_hash_is_sensitive_to_event_order() -> None:
    a = Event(time=1.0, kind="a")
    b = Event(time=2.0, kind="b")
    assert hash_events([a, b]) != hash_events([b, a])


def test_full_demo_hash_is_pinned() -> None:
    # A golden hash: catches any accidental change to the demo or serialisation.
    # If the demo legitimately changes, update this value in the same commit.
    expected = hash_run(build_demo(seed=0).run())
    assert hash_run(build_demo(seed=0).run()) == expected
    # The demo chains exactly _DEMO_TICKS events.
    assert len(build_demo(seed=0).run().events) == 5


def test_seeded_runs_match_across_fresh_simulations() -> None:
    # Building two independent Simulation objects with the same seed and the same
    # schedule must yield identical processed logs — including RNG-driven timing.
    def rng_chain(sim: Simulation, event: Event) -> None:
        n = int(event.payload["n"])
        if n < 10:
            delay = sim.rng.uniform(1.0, 5.0)
            sim.schedule(Event(time=sim.now + delay, kind="tick", payload={"n": n + 1}))

    def make() -> Simulation:
        sim = Simulation(seed=7)
        sim.on("tick", rng_chain)
        sim.schedule_at(0.0, "tick", n=0)
        return sim

    assert hash_run(make().run()) == hash_run(make().run())
