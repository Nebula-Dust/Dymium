"""Structured evidence, penalty, and normalization event helpers."""

from __future__ import annotations

from fnmatch import fnmatch
from typing import Any

from ..provenance import deterministic_uuid, utc_now
from .config import ConfidenceConfig

SEVERITIES = ("info", "warning", "severe", "critical")


def normalization_event(
    event_type: str,
    *,
    source_value: Any = None,
    normalized_value: Any = None,
    ontology_version: str | None = None,
    confidence_delta: float | int | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create a structured normalization event for provenance."""

    payload = {
        "type": event_type,
        "source_value": source_value,
        "normalized_value": normalized_value,
        "ontology_version": ontology_version or "dymium-v1",
        "confidence_delta": _clamp_delta(confidence_delta),
        "notes": notes,
        "timestamp": utc_now(),
    }
    payload["event_id"] = deterministic_uuid("normalization-event", event_type, source_value, normalized_value, ontology_version)
    return payload


def evidence_event(kind: str, detail: str, *, field: str | None = None, score_delta: float | int | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "event_id": deterministic_uuid("confidence-evidence", kind, field, detail, metadata),
        "kind": kind,
        "field": field,
        "detail": detail,
        "score_delta": _clamp_delta(score_delta),
        "metadata": metadata if metadata else None,
        "timestamp": utc_now(),
    }


def penalty_event(reason: str, *, severity: str = "warning", field: str | None = None, amount: float | int | None = None, source: str | None = None) -> dict[str, Any]:
    severity = severity if severity in SEVERITIES else "warning"
    return {
        "event_id": deterministic_uuid("confidence-penalty", reason, severity, field, source),
        "reason": reason,
        "severity": severity,
        "field": field,
        "amount": _clamp_amount(amount),
        "source": source,
        "timestamp": utc_now(),
    }


def warning_events_from_values(values: Any, config: ConfidenceConfig) -> list[dict[str, Any]]:
    warnings = _listify(values)
    return [penalty_event(str(value), severity=_severity_for_warning(str(value), config), source="validation_warning") for value in warnings if str(value).strip()]


def _severity_for_warning(warning: str, config: ConfidenceConfig) -> str:
    rules = config.gates.get("warning_severity_rules", {}) if isinstance(config.gates, dict) else {}
    for pattern, severity in rules.items():
        if fnmatch(warning, pattern):
            return severity if severity in SEVERITIES else "warning"
    return "warning"


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    try:
        if value != value:
            return []
    except (TypeError, ValueError):
        pass
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    if hasattr(value, "tolist"):
        converted = value.tolist()
        return converted if isinstance(converted, list) else [converted]
    return [value]


def _clamp_delta(value: Any) -> float:
    number = _float_or_zero(value)
    return round(max(-1.0, min(number, 1.0)), 4)


def _clamp_amount(value: Any) -> float | None:
    if value is None:
        return None
    return round(max(0.0, min(_float_or_zero(value), 1.0)), 4)


def _float_or_zero(value: Any) -> float:
    try:
        if value is None or value != value:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
