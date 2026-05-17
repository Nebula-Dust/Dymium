"""Source coverage and reliability metrics."""
from __future__ import annotations
from collections import Counter
from typing import Any
from src.sources.schemas import SourceIngestionResult

def source_coverage_metrics(result: SourceIngestionResult) -> dict[str, Any]:
    records = result.records
    total = len(records)
    canonical = len(result.canonical_records)
    issue_counts = Counter(issue.severity for issue in result.validation_issues)
    for record in records:
        issue_counts.update(issue.severity for issue in record.validation_issues)
    raw_fields = Counter(field for record in records for field in record.raw_fields)
    unmapped = sorted({field for record in records for field in record.source_terms.get("unmapped_fields", [])})
    geometry_present = sum(1 for record in records if record.geometry_metadata.get("valid") or record.geometry_metadata.get("coordinates") or record.geometry_metadata.get("bounds"))
    ontology_mapped = sum(1 for record in records if record.canonical_record and record.confidence_hints.get("canonical_confidence") is not None)
    return {
        "source_dataset": result.source_dataset,
        "records": total,
        "canonical_records": canonical,
        "canonical_mapping_rate": round(canonical / total, 4) if total else 0.0,
        "raw_field_coverage": dict(sorted(raw_fields.items())),
        "unmatched_fields": unmapped,
        "unmatched_field_count": len(unmapped),
        "ontology_coverage_rate": round(ontology_mapped / total, 4) if total else 0.0,
        "geographic_coverage_rate": round(geometry_present / total, 4) if total else 0.0,
        "validation_issue_counts": dict(issue_counts),
        "ingestion_success_rate": 0.0 if result.errors else 1.0,
        "descriptor_warnings": list(result.descriptor.schema_warnings),
    }

def source_reliability_profile(result: SourceIngestionResult) -> dict[str, Any]:
    coverage = source_coverage_metrics(result)
    source_trust_values = [record.confidence_hints.get("source_trust") for record in result.records if record.confidence_hints.get("source_trust") is not None]
    extraction_values = [record.confidence_hints.get("text_coverage_percent") for record in result.records if record.confidence_hints.get("text_coverage_percent") is not None]
    severe = coverage["validation_issue_counts"].get("severe", 0)
    critical = coverage["validation_issue_counts"].get("critical", 0)
    return {
        "source_dataset": result.source_dataset,
        "source_confidence_mean": round(sum(float(value) for value in source_trust_values) / len(source_trust_values), 4) if source_trust_values else None,
        "schema_stability": 1.0 if not result.descriptor.schema_warnings else round(max(0.0, 1 - len(result.descriptor.schema_warnings) * 0.05), 4),
        "extraction_quality_mean": round(sum(float(value) for value in extraction_values) / len(extraction_values), 4) if extraction_values else None,
        "ocr_dependence": bool(result.descriptor.capabilities.ocr_required),
        "update_consistency": "unchanged" if result.metrics.get("unchanged") else "new_or_changed",
        "critical_issue_count": critical,
        "severe_issue_count": severe,
        "reliability_notes": _notes(result, coverage),
    }

def _notes(result: SourceIngestionResult, coverage: dict[str, Any]) -> list[str]:
    notes = []
    if result.descriptor.capabilities.ocr_required:
        notes.append("source_requires_ocr")
    if coverage["unmatched_field_count"]:
        notes.append("unmatched_source_fields_present")
    if coverage["geographic_coverage_rate"] < 1.0:
        notes.append("partial_or_missing_geographic_coverage")
    if result.errors:
        notes.append("ingestion_errors_present")
    return notes
