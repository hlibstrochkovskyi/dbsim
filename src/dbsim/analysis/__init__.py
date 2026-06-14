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
from dbsim.analysis.capacity import (
    CapacityReport,
    SegmentOccupancy,
    segment_entries_from_paths,
    uic406_occupancy,
)
from dbsim.analysis.conflicts import (
    Conflict,
    Occupation,
    detect_conflicts,
    planned_occupations,
)
from dbsim.analysis.stairway import StairwayTrain, minimum_headway_s, render_stairway
from dbsim.analysis.validation import (
    ValidationPair,
    ValidationReport,
    render_scatter,
    run_validation,
)

__all__ = [
    "DEFAULT_CORRIDOR",
    "CapacityReport",
    "Conflict",
    "Corridor",
    "CorridorStation",
    "Occupation",
    "SegmentOccupancy",
    "StairwayTrain",
    "TrainPath",
    "ValidationPair",
    "ValidationReport",
    "build_corridor",
    "detect_conflicts",
    "extract_train_paths",
    "minimum_headway_s",
    "planned_occupations",
    "render_bildfahrplan",
    "render_scatter",
    "render_stairway",
    "run_validation",
    "segment_entries_from_paths",
    "uic406_occupancy",
]
