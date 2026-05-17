"""Source ingestion provenance helpers."""
from __future__ import annotations
from typing import Any
from src.etl.provenance import deterministic_uuid, utc_now
from src.sources.schemas import SourceDescriptor, SourceProvenance
def make_ingestion_id(source_dataset: str, descriptor: SourceDescriptor, *, source_version: str | None = None) -> str:
    return deterministic_uuid("source-ingestion", source_dataset, descriptor.checksum_sha256, descriptor.path or descriptor.uri, source_version)
def build_source_provenance(*, source_dataset: str, descriptor: SourceDescriptor, ingestion_id: str, extraction_method: str, adapter_name: str, adapter_version: str, source_record_id: str | None = None, source_row_index: int | None = None, source_schema_version: str | None = None, source_timestamp: str | None = None, normalization_events: list[dict[str, Any]] | None = None, registry_metadata: dict[str, Any] | None = None) -> SourceProvenance:
    return SourceProvenance(source_dataset=source_dataset, source_file=descriptor.path, source_uri=descriptor.uri, source_record_id=source_record_id, source_row_index=source_row_index, ingestion_id=ingestion_id, ingestion_timestamp=utc_now(), extraction_method=extraction_method, adapter_name=adapter_name, adapter_version=adapter_version, source_schema_version=source_schema_version, source_timestamp=source_timestamp, checksum_sha256=descriptor.checksum_sha256, raw_crs=descriptor.crs, normalization_events=normalization_events or [], registry_metadata=registry_metadata or {})
def append_transformation_log(record: dict[str, Any], *, step: str, method: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    updated = dict(record)
    log = list(updated.get("transformation_log", []))
    log.append({"step": step, "method": method, "timestamp": utc_now(), "details": details or {}})
    updated["transformation_log"] = log
    return updated
