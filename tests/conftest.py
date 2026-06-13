"""Shared pytest fixtures.

Ingestion/query/graph tests run against tiny committed synthetic GTFS feeds
(``tests/fixtures/``) rather than the network, keeping them fast, deterministic,
and CI-safe:

- ``gtfs_mini`` exercises the loader's trickier paths — clock times past
  ``24:00:00``, parent/child station resolution, ``calendar`` add/remove
  exceptions.
- ``gtfs_transfer`` (A→B, B→C, no direct A→C) forces a real transfer for the
  journey planner.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from dbsim.ingest import load_feed
from dbsim.model import Timetable, TimetableGraph

FIXTURES = Path(__file__).parent / "fixtures"

# A weekday on which the synthetic ``DAILY``/``WD`` services run.
WEDNESDAY = 20260617


def _load_fixture(fixture: str, dest: Path) -> Path:
    """Zip a fixture GTFS directory and load it into a fresh DuckDB file."""
    src = FIXTURES / fixture
    zip_path = dest / f"{fixture}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for txt in sorted(src.glob("*.txt")):
            zf.write(txt, arcname=txt.name)
    db_path = dest / f"{fixture}.duckdb"
    load_feed(zip_path, db_path)
    return db_path


@pytest.fixture(scope="session")
def mini_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The loaded ``gtfs_mini`` fixture database."""
    return _load_fixture("gtfs_mini", tmp_path_factory.mktemp("gtfs_mini"))


@pytest.fixture(scope="session")
def transfer_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The loaded ``gtfs_transfer`` fixture database."""
    return _load_fixture("gtfs_transfer", tmp_path_factory.mktemp("gtfs_transfer"))


@pytest.fixture
def mini_tt(mini_db: Path) -> Iterator[Timetable]:
    """A read-only :class:`Timetable` over the ``gtfs_mini`` fixture."""
    with Timetable(mini_db) as tt:
        yield tt


@pytest.fixture
def mini_graph(mini_db: Path) -> Iterator[TimetableGraph]:
    """A :class:`TimetableGraph` over ``gtfs_mini`` on a Wednesday."""
    with Timetable(mini_db) as tt:
        yield TimetableGraph(tt, WEDNESDAY)


@pytest.fixture
def transfer_graph(transfer_db: Path) -> Iterator[TimetableGraph]:
    """A :class:`TimetableGraph` over ``gtfs_transfer`` on a Wednesday."""
    with Timetable(transfer_db) as tt:
        yield TimetableGraph(tt, WEDNESDAY)
