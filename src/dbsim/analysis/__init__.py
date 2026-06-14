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
from dbsim.analysis.micro_validation import (
    MicroValidationReport,
    micro_min_headway_s,
    validate_micro_zone,
)
from dbsim.analysis.montecarlo import (
    DelayModel,
    MonteCarloResult,
    RepOutcome,
    calibrate,
    origin_delays_from_snapshot,
    run_montecarlo,
)
from dbsim.analysis.stairway import StairwayTrain, minimum_headway_s, render_stairway
from dbsim.analysis.strategy_study import (
    StrategyStudyResult,
    StudyScenario,
    default_scenario,
    format_report,
    run_strategy_study,
)
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
    "DelayModel",
    "MicroValidationReport",
    "MonteCarloResult",
    "Occupation",
    "RepOutcome",
    "SegmentOccupancy",
    "StairwayTrain",
    "StrategyStudyResult",
    "StudyScenario",
    "TrainPath",
    "ValidationPair",
    "ValidationReport",
    "build_corridor",
    "calibrate",
    "default_scenario",
    "detect_conflicts",
    "extract_train_paths",
    "format_report",
    "micro_min_headway_s",
    "minimum_headway_s",
    "origin_delays_from_snapshot",
    "planned_occupations",
    "render_bildfahrplan",
    "render_scatter",
    "render_stairway",
    "run_montecarlo",
    "run_strategy_study",
    "run_validation",
    "segment_entries_from_paths",
    "uic406_occupancy",
    "validate_micro_zone",
]
