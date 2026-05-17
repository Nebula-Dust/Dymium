"""Benchmark policy loading for operational ETL quality thresholds."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config" / "benchmarking"
DEFAULT_POLICY_PATH = CONFIG_ROOT / "thresholds.json"

DEFAULT_BENCHMARK_POLICY: dict[str, Any] = {
    "thresholds": {
        "ocr": {
            "low_confidence": 0.35,
            "gibberish_confidence": 0.15,
        },
        "drift": {
            "confidence_mean_delta": -0.05,
            "invalid_geometry_rate_delta": 0.02,
            "warning_rate_delta": 0.05,
            "text_coverage_percent_delta": -5.0,
            "pages_needing_ocr_delta": 0.0,
            "failed_pages_delta": 0.0,
        },
    }
}


@dataclass(frozen=True)
class BenchmarkPolicy:
    """Validated benchmark policy for operational thresholds."""

    thresholds: dict[str, Any] = field(default_factory=dict)
    source: str = "defaults"
    warnings: tuple[str, ...] = ()

    def threshold(self, dotted_path: str) -> float:
        """Return a threshold from policy, falling back to built-in defaults."""

        return threshold_value(self.thresholds, dotted_path, default_threshold(dotted_path))

    def section(self, name: str) -> dict[str, float]:
        value = self.thresholds.get(name, {})
        return dict(value) if isinstance(value, dict) else {}


def load_benchmark_policy(path: str | Path | None = None, *, overrides: dict[str, Any] | None = None) -> BenchmarkPolicy:
    """Load the benchmark policy with validation and safe fallback behavior."""

    policy = deepcopy(DEFAULT_BENCHMARK_POLICY)
    warnings: list[str] = []
    policy_path = Path(path) if path is not None else DEFAULT_POLICY_PATH
    source = str(policy_path) if policy_path.exists() else "defaults"
    if policy_path.exists():
        try:
            loaded = json.loads(policy_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("benchmark policy root must be an object")
            _deep_merge(policy, loaded)
        except Exception as exc:  # pragma: no cover - parser messages differ by runtime
            message = f"Unable to load benchmark policy {policy_path}: {exc}. Using defaults."
            LOGGER.warning(message)
            warnings.append(message)
            source = "defaults"
    if overrides:
        _deep_merge(policy, overrides)
        source = f"{source}+overrides"
    validated = _validate_policy(policy)
    return BenchmarkPolicy(thresholds=validated["thresholds"], source=source, warnings=tuple(warnings))


def load_benchmark_config(path: str | Path | None = None, *, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Backward-compatible config loader returning a dictionary."""

    policy = load_benchmark_policy(path, overrides=overrides)
    return {"thresholds": policy.thresholds, "source": policy.source, "warnings": list(policy.warnings)}


def load_benchmark_thresholds(path: str | Path | None = None, *, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Backward-compatible helper returning validated threshold sections."""

    return load_benchmark_policy(path, overrides=overrides).thresholds


def default_threshold(dotted_path: str) -> float:
    """Read a numeric threshold from built-in safe defaults."""

    return threshold_value(DEFAULT_BENCHMARK_POLICY["thresholds"], dotted_path, 0.0)


def threshold_value(thresholds: dict[str, Any], dotted_path: str, default: float) -> float:
    """Read a numeric threshold from a nested threshold object."""

    value: Any = thresholds
    for part in dotted_path.split("."):
        if not isinstance(value, dict):
            return default
        value = value.get(part)
    return _number(value, default)


def _validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    validated = deepcopy(DEFAULT_BENCHMARK_POLICY)
    thresholds = policy.get("thresholds", {}) if isinstance(policy.get("thresholds"), dict) else {}
    for section, defaults in DEFAULT_BENCHMARK_POLICY["thresholds"].items():
        raw_section = thresholds.get(section, {}) if isinstance(thresholds.get(section), dict) else {}
        for key, default in defaults.items():
            value = _number(raw_section.get(key), default)
            if section == "ocr" and key.endswith("confidence"):
                value = min(max(value, 0.0), 1.0)
            validated["thresholds"][section][key] = value
    return validated


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, value in incoming.items():
        if isinstance(base.get(key), dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _number(value: Any, default: float) -> float:
    try:
        if value is None or value != value:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)
