"""Metrics and reporting for schema reconciliation runs."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from src.reconciliation.canonical_schema import CanonicalGeologicalRecord
from src.reconciliation.validators import schema_coverage


def confidence_histogram(values: list[float]) -> dict[str, int]:
    buckets = {"0.00-0.24": 0, "0.25-0.49": 0, "0.50-0.74": 0, "0.75-1.00": 0}
    for value in values:
        score = max(0.0, min(float(value), 1.0))
        if score < 0.25:
            buckets["0.00-0.24"] += 1
        elif score < 0.5:
            buckets["0.25-0.49"] += 1
        elif score < 0.75:
            buckets["0.50-0.74"] += 1
        else:
            buckets["0.75-1.00"] += 1
    return buckets


def generate_reconciliation_metrics(records: list[CanonicalGeologicalRecord]) -> dict[str, Any]:
    confidence_values = [record.confidence_score for record in records]
    source_counts = Counter(record.source_dataset for record in records)
    unmapped = Counter(field for record in records for field in record.unmapped_fields)
    warnings = Counter(warning.split(":", 1)[0] for record in records for warning in record.validation_warnings)
    fields = ["site_name", "commodities", "coordinates", "lithology", "geologic_age", "measurement_units", "source_url"]
    matched_fields = {field: sum(1 for record in records if field in record.reconciled_fields and record.reconciled_fields[field].mapping_confidence > 0) for field in fields}
    low_confidence_fields = {
        field: sum(1 for record in records if record.field_confidence.get(field, 0.0) < 0.5)
        for field in fields
    }
    duplicate_groups = {record.duplicate_group_id for record in records if record.duplicate_group_id}
    return {
        "total_records": len(records),
        "source_counts": dict(source_counts),
        "validation_status": dict(Counter(record.validation_status for record in records)),
        "matched_fields": matched_fields,
        "low_confidence_fields": low_confidence_fields,
        "unmapped_fields": dict(unmapped),
        "schema_coverage": schema_coverage(records),
        "invalid_geometry_count": sum(1 for record in records if not record.geometry.valid),
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_candidate_records": sum(1 for record in records if record.duplicate_group_id),
        "ontology_conflicts": sum(len(record.conflicts) for record in records),
        "warning_counts": dict(warnings),
        "confidence_distribution": {
            "count": len(confidence_values),
            "mean": round(mean(confidence_values), 4) if confidence_values else None,
            "min": round(min(confidence_values), 4) if confidence_values else None,
            "max": round(max(confidence_values), 4) if confidence_values else None,
            "histogram": confidence_histogram(confidence_values),
        },
    }


def metrics_to_markdown(metrics: dict[str, Any]) -> str:
    lines = ["# Schema Reconciliation Metrics", ""]
    lines.append(f"Total records: **{metrics.get('total_records', 0)}**")
    lines.append("")
    lines.append("## Source Counts")
    for source, count in metrics.get("source_counts", {}).items():
        lines.append(f"- `{source}`: {count}")
    lines.append("")
    lines.append("## Validation")
    for status, count in metrics.get("validation_status", {}).items():
        lines.append(f"- `{status}`: {count}")
    lines.append(f"- invalid geometry: {metrics.get('invalid_geometry_count', 0)}")
    lines.append(f"- duplicate groups: {metrics.get('duplicate_group_count', 0)}")
    lines.append("")
    lines.append("## Confidence")
    distribution = metrics.get("confidence_distribution", {})
    lines.append(f"- mean: {distribution.get('mean')}")
    lines.append(f"- min: {distribution.get('min')}")
    lines.append(f"- max: {distribution.get('max')}")
    for bucket, count in distribution.get("histogram", {}).items():
        lines.append(f"- `{bucket}`: {count}")
    lines.append("")
    lines.append("## Schema Coverage")
    for field, coverage in metrics.get("schema_coverage", {}).items():
        lines.append(f"- `{field}`: {coverage:.1%}")
    lines.append("")
    lines.append("## Top Unmapped Fields")
    for field, count in sorted(metrics.get("unmapped_fields", {}).items(), key=lambda item: item[1], reverse=True)[:20]:
        lines.append(f"- `{field}`: {count}")
    return "\n".join(lines) + "\n"


def write_metrics_json(metrics: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


def write_metrics_markdown(metrics: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(metrics_to_markdown(metrics), encoding="utf-8")
    return path
