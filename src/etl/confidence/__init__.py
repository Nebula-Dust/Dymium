"""Explainable confidence infrastructure for Dymium ETL records."""

from .calibration import CalibrationRegistry, ConfidenceCalibrator, IdentityCalibrator
from .config import ConfidenceConfig, load_confidence_config
from .events import evidence_event, normalization_event, penalty_event, warning_events_from_values
from .scoring import (
    CONFIDENCE_COLUMNS,
    FIELD_CONFIDENCE_COLUMNS,
    RECORD_CONFIDENCE_COLUMN,
    STAGE_CONFIDENCE_COLUMN,
    attach_dataframe_confidence,
    attach_record_confidence,
    calibration_diagnostics,
    confidence_assessment,
    confidence_drift_report,
    confidence_histogram,
    dependency_failure_summary,
    reconciliation_degradation_metrics,
    validation_report,
)

__all__ = [
    "CalibrationRegistry",
    "ConfidenceCalibrator",
    "ConfidenceConfig",
    "CONFIDENCE_COLUMNS",
    "FIELD_CONFIDENCE_COLUMNS",
    "IdentityCalibrator",
    "RECORD_CONFIDENCE_COLUMN",
    "STAGE_CONFIDENCE_COLUMN",
    "attach_dataframe_confidence",
    "attach_record_confidence",
    "calibration_diagnostics",
    "confidence_assessment",
    "confidence_drift_report",
    "confidence_histogram",
    "dependency_failure_summary",
    "evidence_event",
    "load_confidence_config",
    "normalization_event",
    "penalty_event",
    "reconciliation_degradation_metrics",
    "validation_report",
    "warning_events_from_values",
]
