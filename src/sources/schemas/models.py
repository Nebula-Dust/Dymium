"""Typed source-ingestion models for heterogeneous geological datasets."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field
try:
    from pydantic import ConfigDict
except ImportError:  # pragma: no cover
    ConfigDict = None  # type: ignore[assignment]
from src.etl.provenance import deterministic_uuid, utc_now
SOURCE_SCHEMA_VERSION = "dymium-source-ingestion-v1"
SourceKind = Literal["structured_dataset", "semi_structured_report", "pdf", "scanned_pdf", "geospatial_layer", "archive", "web_archive", "operator_filing", "unknown"]
ValidationSeverity = Literal["info", "warning", "severe", "critical"]
class SourceCapability(BaseModel):
    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow")
    structured_records: bool = False
    document_text: bool = False
    ocr_required: bool = False
    tabular_report: bool = False
    geospatial_geometry: bool = False
    archive_members: bool = False
    remote_fetch: bool = False
    incremental_updates: bool = False
    canonical_mapping: bool = False
class SourceDescriptor(BaseModel):
    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow")
    source_id: str = Field(default_factory=lambda: deterministic_uuid("source-descriptor", utc_now()))
    source_name: str | None = None
    source_kind: SourceKind = "unknown"
    file_format: str | None = None
    path: str | None = None
    uri: str | None = None
    exists: bool = False
    size_bytes: int | None = None
    checksum_sha256: str | None = None
    encoding: str | None = None
    delimiter: str | None = None
    crs: str | None = None
    geometry_presence: bool = False
    digital_pdf: bool | None = None
    scanned_pdf: bool | None = None
    malformed: bool = False
    nested_archive: bool = False
    archive_members: list[dict[str, Any]] = Field(default_factory=list)
    schema_fields: list[str] = Field(default_factory=list)
    schema_warnings: list[str] = Field(default_factory=list)
    capabilities: SourceCapability = Field(default_factory=SourceCapability)
    inspected_at: str = Field(default_factory=utc_now)
class SourceValidationIssue(BaseModel):
    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow")
    severity: ValidationSeverity = "warning"
    code: str
    message: str
    field: str | None = None
    record_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utc_now)
class SourceProvenance(BaseModel):
    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow")
    source_dataset: str
    source_file: str | None = None
    source_uri: str | None = None
    source_record_id: str | None = None
    source_row_index: int | None = None
    ingestion_id: str
    ingestion_timestamp: str = Field(default_factory=utc_now)
    extraction_method: str
    adapter_name: str
    adapter_version: str
    source_schema_version: str | None = None
    source_timestamp: str | None = None
    checksum_sha256: str | None = None
    raw_crs: str | None = None
    normalization_events: list[dict[str, Any]] = Field(default_factory=list)
    registry_metadata: dict[str, Any] = Field(default_factory=dict)
class SourceRecord(BaseModel):
    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow")
    record_id: str
    source_dataset: str
    source_kind: SourceKind = "unknown"
    raw_fields: dict[str, Any] = Field(default_factory=dict)
    source_terms: dict[str, Any] = Field(default_factory=dict)
    geometry_metadata: dict[str, Any] = Field(default_factory=dict)
    source_timestamp: str | None = None
    provenance: SourceProvenance
    validation_issues: list[SourceValidationIssue] = Field(default_factory=list)
    canonical_record: dict[str, Any] | None = None
    confidence_hints: dict[str, Any] = Field(default_factory=dict)
class SourceIngestionResult(BaseModel):
    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow")
    ingestion_id: str
    source_dataset: str
    descriptor: SourceDescriptor
    records: list[SourceRecord] = Field(default_factory=list)
    canonical_records: list[dict[str, Any]] = Field(default_factory=list)
    validation_issues: list[SourceValidationIssue] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
class SourceUpdateState(BaseModel):
    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow")
    source_dataset: str
    source_uri: str | None = None
    source_file: str | None = None
    checksum_sha256: str | None = None
    source_timestamp: str | None = None
    ingested_record_ids: list[str] = Field(default_factory=list)
    last_ingested_at: str | None = None
    source_version: str | None = None
def model_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(value)
