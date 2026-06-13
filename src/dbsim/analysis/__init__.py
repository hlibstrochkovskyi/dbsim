"""Analysis & visualization: metrics, validation, and timetable diagrams."""

from __future__ import annotations

from dbsim.analysis.bildfahrplan import (
    DEFAULT_CORRIDOR,
    Corridor,
    CorridorStation,
    TrainPath,
    build_corridor,
    extract_train_paths,
    render_bildfahrplan,
)

__all__ = [
    "DEFAULT_CORRIDOR",
    "Corridor",
    "CorridorStation",
    "TrainPath",
    "build_corridor",
    "extract_train_paths",
    "render_bildfahrplan",
]
