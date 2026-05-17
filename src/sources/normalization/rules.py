"""Source-specific normalization and reconciliation rule helpers."""
from __future__ import annotations
from typing import Any
from src.sources.registries import SourceRegistry
def source_reconciliation_rules(source_name: str, registry: SourceRegistry | None = None) -> dict[str, Any]:
    registry = registry or SourceRegistry()
    return registry.reconciliation_rules(source_name)
def merge_source_rules(*rules: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for rule_set in rules:
        for key, value in (rule_set or {}).items():
            if isinstance(merged.get(key), dict) and isinstance(value, dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
    return merged
