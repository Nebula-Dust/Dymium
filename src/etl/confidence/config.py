"""Configuration loading for Dymium confidence scoring."""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha1
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

CONFIG_FILES = {
    "source_trust": "source_trust.json",
    "method_reliability": "method_reliability.json",
    "field_weights": "field_weights.json",
    "modifiers": "modifiers.json",
    "stage_modifiers": "stage_modifiers.json",
    "penalties": "penalties.json",
    "dependencies": "dependencies.json",
    "gates": "gates.json",
    "thresholds": "thresholds.json",
    "temporal": "temporal.json",
}

SAFE_FALLBACK = {
    "source_trust": {"UNKNOWN": 0.35},
    "method_reliability": {"unknown": 0.50},
    "field_weights": {"record_confidence": 1.0},
    "modifiers": {},
    "stage_modifiers": {},
    "penalties": {"severity": {"info": 0.01, "warning": 0.05, "severe": 0.15, "critical": 0.45}, "warning_max": 0.18},
    "dependencies": {},
    "gates": {"critical_gates": [], "warning_severity_rules": {}},
    "thresholds": {"low": 0.40, "medium": 0.70, "high": 0.85, "default_ceiling": 1.0},
    "temporal": {"recency_weighting_enabled": False, "source_timestamp_fields": [], "stale_after_days": 3650, "stale_penalty": 0.05},
}


@dataclass(frozen=True)
class ConfidenceConfig:
    """Validated confidence configuration bundle."""

    source_trust: dict[str, float] = field(default_factory=dict)
    method_reliability: dict[str, float] = field(default_factory=dict)
    field_weights: dict[str, float] = field(default_factory=dict)
    modifiers: dict[str, float] = field(default_factory=dict)
    stage_modifiers: dict[str, float] = field(default_factory=dict)
    penalties: dict[str, Any] = field(default_factory=dict)
    dependencies: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    gates: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)
    temporal: dict[str, Any] = field(default_factory=dict)
    config_dir: str | None = None
    errors: list[str] = field(default_factory=list)
    config_hash: str = ""

    def source_score(self, source: Any) -> float:
        key = str(source or "UNKNOWN").upper()
        return self.source_trust.get(key, self.source_trust.get("UNKNOWN", 0.35))

    def method_score(self, method: Any) -> float:
        key = str(method or "unknown")
        return self.method_reliability.get(key, self.method_reliability.get("unknown", 0.50))

    def modifier(self, key: str) -> float:
        return _clamp(self.modifiers.get(key, 0.0))

    def penalty(self, key: str) -> float:
        return _clamp(self.penalties.get(key, 0.0))

    def stage_modifier(self, stage: str) -> float:
        return _clamp(self.stage_modifiers.get(stage, 0.0))

    def severity_penalty(self, severity: str) -> float:
        severity_values = self.penalties.get("severity", {}) if isinstance(self.penalties, dict) else {}
        return _clamp(severity_values.get(severity, severity_values.get("warning", 0.05)))


def load_confidence_config(config_dir: str | Path | None = None, overrides: dict[str, Any] | str | Path | None = None) -> ConfidenceConfig:
    """Load confidence configuration from JSON files with safe fallback.

    A malformed override or missing file never disables scoring. The loader logs
    configuration errors and returns conservative defaults for missing sections.
    """

    resolved_dir = _resolve_config_dir(config_dir)
    raw = deepcopy(SAFE_FALLBACK)
    errors: list[str] = []

    for section, filename in CONFIG_FILES.items():
        path = resolved_dir / filename if resolved_dir else None
        if path and path.exists():
            loaded, error = _read_json(path)
            if error:
                errors.append(error)
            elif isinstance(loaded, dict):
                raw[section] = loaded
            else:
                errors.append(f"{path}: expected object, got {type(loaded).__name__}")
        else:
            errors.append(f"missing confidence config: {filename}")

    if overrides is not None:
        override_data, error = _load_overrides(overrides)
        if error:
            errors.append(error)
        elif override_data:
            raw = _deep_merge(raw, override_data)

    validated, validation_errors = _validate(raw)
    errors.extend(validation_errors)
    config_hash = sha1(json.dumps(validated, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
    if errors:
        LOGGER.warning("Confidence config loaded with %s issue(s): %s", len(errors), "; ".join(errors[:5]))
    return ConfidenceConfig(**validated, config_dir=str(resolved_dir) if resolved_dir else None, errors=errors, config_hash=config_hash)


def _resolve_config_dir(config_dir: str | Path | None) -> Path | None:
    if config_dir:
        return Path(config_dir)
    env_value = os.getenv("DYMIUM_CONFIDENCE_CONFIG_DIR")
    if env_value:
        return Path(env_value)
    return Path(__file__).resolve().parents[3] / "config" / "confidence"


def _read_json(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:  # pragma: no cover - exact JSON errors vary by Python version.
        return None, f"{path}: {exc}"


def _load_overrides(overrides: dict[str, Any] | str | Path) -> tuple[dict[str, Any] | None, str | None]:
    if isinstance(overrides, dict):
        return overrides, None
    path = Path(overrides)
    loaded, error = _read_json(path)
    if error:
        return None, error
    if not isinstance(loaded, dict):
        return None, f"{path}: confidence override must be a JSON object"
    return loaded, None


def _validate(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    validated = deepcopy(SAFE_FALLBACK)
    validated["source_trust"], section_errors = _validate_score_map(raw.get("source_trust"), "source_trust")
    errors.extend(section_errors)
    validated["method_reliability"], section_errors = _validate_score_map(raw.get("method_reliability"), "method_reliability")
    errors.extend(section_errors)
    validated["field_weights"], section_errors = _validate_weight_map(raw.get("field_weights"), "field_weights")
    errors.extend(section_errors)
    validated["modifiers"], section_errors = _validate_score_map(raw.get("modifiers"), "modifiers")
    errors.extend(section_errors)
    validated["stage_modifiers"], section_errors = _validate_score_map(raw.get("stage_modifiers"), "stage_modifiers")
    errors.extend(section_errors)
    validated["penalties"] = _validate_penalties(raw.get("penalties"), errors)
    validated["dependencies"] = _validate_dependencies(raw.get("dependencies"), errors)
    validated["gates"] = _validate_gates(raw.get("gates"), errors)
    validated["thresholds"], section_errors = _validate_score_map(raw.get("thresholds"), "thresholds")
    errors.extend(section_errors)
    validated["temporal"] = _validate_temporal(raw.get("temporal"), errors)
    return validated, errors


def _validate_score_map(value: Any, section: str) -> tuple[dict[str, float], list[str]]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return deepcopy(SAFE_FALLBACK.get(section, {})), [f"{section}: expected object"]
    cleaned: dict[str, float] = {}
    for key, score in value.items():
        number = _float_or_none(score)
        if number is None:
            errors.append(f"{section}.{key}: expected numeric score")
            continue
        cleaned[str(key)] = _clamp(number)
    if not cleaned:
        errors.append(f"{section}: no valid scores; using fallback")
        return deepcopy(SAFE_FALLBACK.get(section, {})), errors
    return cleaned, errors


def _validate_weight_map(value: Any, section: str) -> tuple[dict[str, float], list[str]]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return deepcopy(SAFE_FALLBACK.get(section, {})), [f"{section}: expected object"]
    cleaned: dict[str, float] = {}
    for key, score in value.items():
        number = _float_or_none(score)
        if number is None or number < 0:
            errors.append(f"{section}.{key}: expected non-negative numeric weight")
            continue
        cleaned[str(key)] = number
    if not cleaned:
        errors.append(f"{section}: no valid weights; using fallback")
        return deepcopy(SAFE_FALLBACK.get(section, {})), errors
    return cleaned, errors


def _validate_penalties(value: Any, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append("penalties: expected object")
        return deepcopy(SAFE_FALLBACK["penalties"])
    cleaned: dict[str, Any] = {}
    for key, penalty in value.items():
        if key == "severity" and isinstance(penalty, dict):
            cleaned[key] = {str(name): _clamp(amount) for name, amount in penalty.items() if _float_or_none(amount) is not None}
            continue
        number = _float_or_none(penalty)
        if number is None:
            errors.append(f"penalties.{key}: expected numeric value")
            continue
        cleaned[str(key)] = _clamp(number)
    cleaned.setdefault("severity", deepcopy(SAFE_FALLBACK["penalties"]["severity"]))
    cleaned.setdefault("warning_max", SAFE_FALLBACK["penalties"]["warning_max"])
    return cleaned


def _validate_temporal(value: Any, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append("temporal: expected object")
        return deepcopy(SAFE_FALLBACK["temporal"])
    fields = value.get("source_timestamp_fields", [])
    if not isinstance(fields, list):
        errors.append("temporal.source_timestamp_fields: expected list")
        fields = []
    stale_after_days = _float_or_none(value.get("stale_after_days"))
    stale_penalty = _float_or_none(value.get("stale_penalty"))
    historical_conflict_penalty = _float_or_none(value.get("historical_conflict_penalty"))
    return {
        "recency_weighting_enabled": bool(value.get("recency_weighting_enabled", False)),
        "source_timestamp_fields": [str(item) for item in fields],
        "stale_after_days": int(stale_after_days if stale_after_days is not None else 3650),
        "stale_penalty": _clamp(stale_penalty if stale_penalty is not None else 0.05),
        "historical_conflict_penalty": _clamp(historical_conflict_penalty if historical_conflict_penalty is not None else 0.08),
    }


def _validate_dependencies(value: Any, errors: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        errors.append("dependencies: expected object")
        return {}
    cleaned: dict[str, list[dict[str, Any]]] = {}
    for child, dependencies in value.items():
        if not isinstance(dependencies, list):
            errors.append(f"dependencies.{child}: expected list")
            continue
        child_rules = []
        for item in dependencies:
            if not isinstance(item, dict) or not item.get("parent"):
                errors.append(f"dependencies.{child}: dependency missing parent")
                continue
            child_rules.append(
                {
                    "parent": str(item["parent"]),
                    "min_parent_score": _clamp(item.get("min_parent_score", 0.0)),
                    "ceiling": _clamp(item.get("ceiling", 1.0)),
                    "reason": str(item.get("reason", "dependency confidence propagation")),
                }
            )
        cleaned[str(child)] = child_rules
    return cleaned


def _validate_gates(value: Any, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append("gates: expected object")
        return deepcopy(SAFE_FALLBACK["gates"])
    gates = []
    for item in value.get("critical_gates", []):
        if not isinstance(item, dict) or not item.get("field") or not item.get("condition"):
            errors.append("gates.critical_gates: gate missing field or condition")
            continue
        gates.append(
            {
                "field": str(item["field"]),
                "condition": str(item["condition"]),
                "ceiling": _clamp(item.get("ceiling", 1.0)),
                "severity": str(item.get("severity", "warning")),
                "reason": str(item.get("reason", item["condition"])),
            }
        )
    rules = value.get("warning_severity_rules", {})
    if not isinstance(rules, dict):
        errors.append("gates.warning_severity_rules: expected object")
        rules = {}
    return {"critical_gates": gates, "warning_severity_rules": {str(k): str(v) for k, v in rules.items()}}


def _deep_merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(left)
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: Any) -> float:
    number = _float_or_none(value)
    if number is None:
        number = 0.0
    return round(max(0.0, min(number, 1.0)), 4)
