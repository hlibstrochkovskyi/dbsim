"""Tests for central seed control (:mod:`dbsim.seed`)."""

from __future__ import annotations

from dbsim.seed import derive_seed, make_rng


def test_make_rng_is_reproducible() -> None:
    a = [make_rng(123).random() for _ in range(5)]
    b = [make_rng(123).random() for _ in range(5)]
    assert a == b


def test_make_rng_differs_by_seed() -> None:
    assert make_rng(1).random() != make_rng(2).random()


def test_derive_seed_is_stable() -> None:
    # Stable across calls (and, by construction, across processes/Python versions).
    assert derive_seed(42, "train:ICE599") == derive_seed(42, "train:ICE599")


def test_derive_seed_distinguishes_labels_and_bases() -> None:
    assert derive_seed(42, "a") != derive_seed(42, "b")
    assert derive_seed(1, "a") != derive_seed(2, "a")


def test_derived_streams_are_independent() -> None:
    base = 7
    rng_a = make_rng(derive_seed(base, "a"))
    rng_b = make_rng(derive_seed(base, "b"))
    assert [rng_a.random() for _ in range(5)] != [rng_b.random() for _ in range(5)]
