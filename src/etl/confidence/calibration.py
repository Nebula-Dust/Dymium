"""Calibration hooks for future empirical confidence tuning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import ConfidenceConfig


class ConfidenceCalibrator(Protocol):
    """Interface for deterministic confidence calibration adapters."""

    name: str

    def adjust(self, assessment: dict[str, Any], *, context: dict[str, Any], config: ConfidenceConfig) -> dict[str, Any]:
        """Return an adjusted confidence assessment."""


@dataclass
class IdentityCalibrator:
    """No-op calibrator used until benchmark-backed calibration exists."""

    name: str = "identity"

    def adjust(self, assessment: dict[str, Any], *, context: dict[str, Any], config: ConfidenceConfig) -> dict[str, Any]:
        assessment = dict(assessment)
        assessment.setdefault("calibration", {})
        assessment["calibration"].update(
            {
                "calibrator": self.name,
                "applied": False,
                "reason": "no empirical calibration registered",
                "config_hash": config.config_hash,
            }
        )
        return assessment


@dataclass
class CalibrationRegistry:
    """Ordered calibration hook registry."""

    calibrators: list[ConfidenceCalibrator] = field(default_factory=lambda: [IdentityCalibrator()])

    def register(self, calibrator: ConfidenceCalibrator) -> None:
        self.calibrators.append(calibrator)

    def apply(self, assessment: dict[str, Any], *, context: dict[str, Any], config: ConfidenceConfig) -> dict[str, Any]:
        adjusted = dict(assessment)
        for calibrator in self.calibrators:
            adjusted = calibrator.adjust(adjusted, context=context, config=config)
        return adjusted


DEFAULT_CALIBRATION_REGISTRY = CalibrationRegistry()
