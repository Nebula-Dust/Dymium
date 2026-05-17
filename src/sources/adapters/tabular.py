"""Tabular source adapters that delegate canonical mapping to reconciliation adapters."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from src.etl.provenance import deterministic_uuid
from src.reconciliation.adapters import GEOROCAdapter, MRDSAdapter, PetDBAdapter
from src.reconciliation.canonical_schema import CanonicalGeologicalRecord
from src.sources.adapters.base import SourceAdapter
from src.sources.provenance import build_source_provenance, make_ingestion_id
from src.sources.schemas import SourceIngestionResult, SourceRecord, SourceUpdateState, SourceValidationIssue, model_to_dict
class CanonicalTabularSourceAdapter(SourceAdapter):
    reconciliation_adapter_cls = MRDSAdapter
    supported_formats = ("csv", "tsv", "txt")
    canonical_mapping = True
    extraction_method = "tabular_schema_adapter"
    def ingest(self, source: str | Path, *, prior_state: SourceUpdateState | None = None, source_version: str | None = None) -> SourceIngestionResult:
        descriptor = self.inspect(source, inspect_pdf=False)
        ingestion_id = make_ingestion_id(self.source_name, descriptor, source_version=source_version)
        if prior_state and prior_state.checksum_sha256 == descriptor.checksum_sha256:
            return SourceIngestionResult(ingestion_id=ingestion_id, source_dataset=self.source_name, descriptor=descriptor, metrics={"unchanged": True, "input_records": 0, "output_records": 0}, state=_state(self.source_name, descriptor, prior_state.ingested_record_ids, source_version), warnings=["source_unchanged_skipped_incremental_ingestion"])
        adapter = self.reconciliation_adapter_cls()
        adapted = adapter.adapt_file(source)
        records: list[SourceRecord] = []
        canonical_records: list[dict[str, Any]] = []
        issues: list[SourceValidationIssue] = []
        for canonical in adapted.records:
            canonical_dict = canonical.to_export_dict() if isinstance(canonical, CanonicalGeologicalRecord) else model_to_dict(canonical)
            source_record_id = str(getattr(canonical, "source_record_id", None) or getattr(canonical, "canonical_id", ""))
            provenance = build_source_provenance(source_dataset=self.source_name, descriptor=descriptor, ingestion_id=ingestion_id, extraction_method=self.extraction_method, adapter_name=self.__class__.__name__, adapter_version=self.adapter_version, source_record_id=source_record_id, source_row_index=getattr(getattr(canonical, "source_metadata", None), "source_row_index", None), source_schema_version=getattr(getattr(canonical, "source_metadata", None), "source_schema_version", None), source_timestamp=getattr(canonical, "source_timestamp", None), registry_metadata=self.registry_metadata)
            validation_issues = [_issue("validation_warning", warning, "warning", source_record_id) for warning in getattr(canonical, "validation_warnings", [])]
            validation_issues.extend(_issue("validation_error", error, "critical", source_record_id) for error in getattr(canonical, "validation_errors", []))
            issues.extend(validation_issues)
            records.append(SourceRecord(record_id=deterministic_uuid("source-record", self.source_name, source_record_id, descriptor.checksum_sha256), source_dataset=self.source_name, source_kind="structured_dataset", raw_fields=dict(getattr(canonical, "raw_fields", {}) or {}), source_terms={"unmapped_fields": list(getattr(canonical, "unmapped_fields", []) or []), "schema_drift_warnings": list(getattr(canonical, "schema_drift_warnings", []) or [])}, geometry_metadata=model_to_dict(getattr(canonical, "geometry", {})), source_timestamp=getattr(canonical, "source_timestamp", None), provenance=provenance, validation_issues=validation_issues, canonical_record=canonical_dict, confidence_hints={"source_trust": self.registry.trust_level(self.source_name), "canonical_confidence": getattr(canonical, "confidence_score", None)}))
            canonical_records.append(canonical_dict)
        metrics = _metrics(self.source_name, records, adapted.metrics, descriptor)
        return SourceIngestionResult(ingestion_id=ingestion_id, source_dataset=self.source_name, descriptor=descriptor, records=records, canonical_records=canonical_records, validation_issues=issues, metrics=metrics, state=_state(self.source_name, descriptor, [record.record_id for record in records], source_version), warnings=[*adapted.warnings, *descriptor.schema_warnings])
class MRDSSourceAdapter(CanonicalTabularSourceAdapter):
    source_name = "MRDS"
    adapter_version = "mrds-source-adapter-v1"
    reconciliation_adapter_cls = MRDSAdapter
class GEOROCSourceAdapter(CanonicalTabularSourceAdapter):
    source_name = "GEOROC"
    adapter_version = "georoc-source-adapter-v1"
    reconciliation_adapter_cls = GEOROCAdapter
class PetDBSourceAdapter(CanonicalTabularSourceAdapter):
    source_name = "PetDB"
    adapter_version = "petdb-source-adapter-v1"
    reconciliation_adapter_cls = PetDBAdapter
def _issue(code: str, message: str, severity: str, record_id: str | None) -> SourceValidationIssue:
    return SourceValidationIssue(code=code, message=message, severity=severity, record_id=record_id)
def _metrics(source_name: str, records: list[SourceRecord], adapter_metrics: dict[str, Any], descriptor) -> dict[str, Any]:
    total = len(records)
    canonical = sum(1 for record in records if record.canonical_record)
    raw_field_count = sum(len(record.raw_fields) for record in records)
    unmapped = sorted({field for record in records for field in record.source_terms.get("unmapped_fields", [])})
    with_geometry = sum(1 for record in records if record.geometry_metadata.get("valid") or record.geometry_metadata.get("coordinates"))
    return {"source_dataset": source_name, "input_records": total, "canonical_records": canonical, "canonical_mapping_rate": round(canonical / total, 4) if total else 0.0, "raw_field_count": raw_field_count, "unmatched_fields": unmapped, "unmatched_field_count": len(unmapped), "geographic_coverage_rate": round(with_geometry / total, 4) if total else 0.0, "validation_issue_count": sum(len(record.validation_issues) for record in records), "schema_field_count": len(descriptor.schema_fields), "adapter_metrics": adapter_metrics}
def _state(source_name: str, descriptor, record_ids: list[str], source_version: str | None) -> dict[str, Any]:
    return {"source_dataset": source_name, "source_file": descriptor.path, "source_uri": descriptor.uri, "checksum_sha256": descriptor.checksum_sha256, "source_version": source_version, "ingested_record_ids": record_ids}
