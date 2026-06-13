"""Shared pytest fixtures.

Ingestion/query tests run against a tiny committed synthetic GTFS feed
(``tests/fixtures/gtfs_mini``) rather than the network, keeping them fast,
deterministic, and CI-safe. The fixture is crafted to exercise the load's
trickier paths: clock times past ``24:00:00``, parent/child station resolution,
and ``calendar`` add/remove exceptions.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from dbsim.ingest import load_feed
from dbsim.model import Timetable

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gtfs_mini"


@pytest.fixture(scope="session")
def mini_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Zip the synthetic GTFS fixture and load it into a DuckDB file once."""
    tmp = tmp_path_factory.mktemp("gtfs")
    zip_path = tmp / "mini.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for txt in sorted(FIXTURE_DIR.glob("*.txt")):
            zf.write(txt, arcname=txt.name)
    db_path = tmp / "mini.duckdb"
    load_feed(zip_path, db_path)
    return db_path


@pytest.fixture
def mini_tt(mini_db: Path) -> Iterator[Timetable]:
    """A read-only :class:`Timetable` over the loaded fixture."""
    with Timetable(mini_db) as tt:
        yield tt
