"""The hello-world sim loop (M0.1 deliverable).

Run it with ``uv run dbsim`` or ``python -m dbsim``. It builds a tiny seeded
simulation, runs it, and prints the canonical run hash. Running it twice with
the same seed prints the same hash — the M0.1 acceptance criterion, made
observable from the command line.
"""

from __future__ import annotations

import argparse

from dbsim.engine import Event, Simulation
from dbsim.record import hash_run
from dbsim.seed import DEFAULT_SEED

#: Number of "tick" events the demo chains together.
_DEMO_TICKS = 5


def build_demo(*, seed: int = DEFAULT_SEED) -> Simulation:
    """Construct the hello-world simulation.

    A single ``tick`` handler chains the next tick at a random (but seeded)
    delay, so the run exercises the RNG, the priority queue, and handler-driven
    scheduling all at once. With a fixed seed the chain is fully reproducible.
    """
    sim = Simulation(seed=seed, max_time=1_000.0)

    def on_tick(sim: Simulation, event: Event) -> None:
        n = int(event.payload["n"])
        if n + 1 < _DEMO_TICKS:
            delay = sim.rng.uniform(1.0, 10.0)
            sim.schedule(Event(time=sim.now + delay, kind="tick", payload={"n": n + 1}))

    sim.on("tick", on_tick)
    sim.schedule(Event(time=0.0, kind="tick", payload={"n": 0}))
    return sim


def main() -> None:
    """CLI entry point: run the demo and print its run hash."""
    parser = argparse.ArgumentParser(prog="dbsim", description="Run the hello-world sim loop.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Run seed.")
    args = parser.parse_args()

    result = build_demo(seed=args.seed).run()
    print(f"seed={result.seed} events={len(result.events)} end_time={result.end_time:.3f}")
    print(f"run_hash={hash_run(result)}")


if __name__ == "__main__":
    main()
