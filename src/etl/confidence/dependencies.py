"""Dependency-aware confidence propagation and critical gates."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .config import ConfidenceConfig
from .events import penalty_event


def apply_dependency_rules(confidences: dict[str, dict[str, Any]], *, context: dict[str, Any], config: ConfidenceConfig) -> dict[str, dict[str, Any]]:
    """Apply configurable confidence ceilings, gates, and parent propagation."""

    updated = deepcopy(confidences)
    for field, rules in config.dependencies.items():
        if field not in updated:
            continue
        for rule in rules:
            parent = rule.get("parent")
            parent_score = _score(updated.get(parent))
            if parent_score is None or parent_score >= float(rule.get("min_parent_score", 0.0)):
                continue
            _cap_assessment(
                updated[field],
                float(rule.get("ceiling", 1.0)),
                reason=str(rule.get("reason", "dependency confidence propagation")),
                severity="severe",
                source=parent,
                dependency={"parent": parent, "parent_score": parent_score, "min_parent_score": rule.get("min_parent_score")},
            )

    for gate in config.gates.get("critical_gates", []):
        field = gate.get("field")
        if field not in updated or not condition_active(str(gate.get("condition")), context):
            continue
        _cap_assessment(
            updated[field],
            float(gate.get("ceiling", 1.0)),
            reason=str(gate.get("reason", gate.get("condition"))),
            severity=str(gate.get("severity", "warning")),
            source="critical_gate",
            dependency={"condition": gate.get("condition")},
        )
    return updated


def condition_active(condition: str, context: dict[str, Any]) -> bool:
    warnings = [str(value).lower() for value in context.get("warnings", [])]
    if condition == "invalid_coordinates":
        return not bool(context.get("valid_coordinates"))
    if condition == "invalid_geometry":
        return not bool(context.get("geometry_valid"))
    if condition == "missing_provenance":
        return not bool(context.get("has_provenance"))
    if condition == "unresolved_conflicts":
        return bool(context.get("conflicts"))
    if condition == "corrupted_ocr":
        return any("corrupted_ocr" in warning or "ocr_gibberish" in warning for warning in warnings)
    if condition == "malformed_schema":
        return any("malformed_schema" in warning for warning in warnings)
    return bool(context.get(condition))


def _cap_assessment(assessment: dict[str, Any], ceiling: float, *, reason: str, severity: str, source: str | None, dependency: dict[str, Any]) -> None:
    score = _score(assessment) or 0.0
    ceiling = max(0.0, min(ceiling, 1.0))
    if score > ceiling:
        assessment["score"] = round(ceiling, 4)
    assessment.setdefault("penalties", []).append(reason)
    assessment.setdefault("penalty_lineage", []).append(penalty_event(reason, severity=severity, field=assessment.get("field"), source=source))
    assessment.setdefault("derivation", {}).setdefault("dependencies", []).append(dependency)
    assessment.setdefault("inherited_penalties", []).append({"source": source, "reason": reason, "ceiling": ceiling, "severity": severity})


def _score(assessment: dict[str, Any] | None) -> float | None:
    if not isinstance(assessment, dict):
        return None
    try:
        return float(assessment.get("score"))
    except (TypeError, ValueError):
        return None
