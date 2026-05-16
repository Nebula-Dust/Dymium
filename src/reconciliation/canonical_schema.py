"""Canonical geological schema models for heterogeneous source reconciliation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

try:  # Pydantic v2
    from pydantic import ConfigDict, field_validator
except ImportError:  # pragma: no cover
    ConfigDict = None  # type: ignore[assignment]
    from pydantic import validator as field_validator  # type: ignore[assignment]

from src.etl.provenance import deterministic_uuid, utc_now

CANONICAL_SCHEMA_VERSION = "dymium-canonical-geology-v1"
RECONCILIATION_VERSION = "dymium-schema-reconciliation-v1"


class FieldProvenance(BaseModel):
    """Where a reconciled field came from and how it was transformed."""

    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow")

    source_dataset: str
    source_field: str | None = None
    source_record_id: str | None = None
    source_file: str | None = None
    source_row_index: int | None = None
    adapter: str | None = None
    transformation_method: str
    reconciliation_version: str = RECONCILIATION_VERSION
    timestamp: str = Field(default_factory=utc_now)


class ReconciledField(BaseModel):
    """Raw-to-normalized field mapping with confidence and provenance."""

    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow")

    raw_field: str | None = None
    raw_value: Any = None
    normalized_value: Any = None
    normalized_values: list[Any] = Field(default_factory=list)
    mapping_method: str
    mapping_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_trust: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    provenance: FieldProvenance
    normalization_events: list[dict[str, Any]] = Field(default_factory=list)


class CanonicalGeometry(BaseModel):
    """Minimal canonical geometry metadata for point deposits/samples."""

    geometry_type: Literal["Point"] = "Point"
    coordinates: tuple[float, float] | None = None  # longitude, latitude
    crs: str = "EPSG:4326"
    valid: bool = False
    warnings: list[str] = Field(default_factory=list)


class SourceRecordMetadata(BaseModel):
    """Source-level schema and drift metadata."""

    source_dataset: str
    source_file: str | None = None
    source_row_index: int | None = None
    source_schema_version: str | None = None
    adapter_version: str
    ingested_at: str = Field(default_factory=utc_now)
    raw_fields: dict[str, Any] = Field(default_factory=dict)
    mapped_source_fields: list[str] = Field(default_factory=list)
    unmapped_fields: list[str] = Field(default_factory=list)
    compatibility_warnings: list[str] = Field(default_factory=list)


class CanonicalGeologicalRecord(BaseModel):
    """Canonical, traceable geological record emitted by source adapters."""

    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow", populate_by_name=True)

    canonical_id: str
    schema_version: str = CANONICAL_SCHEMA_VERSION
    reconciliation_version: str = RECONCILIATION_VERSION
    source_dataset: str
    dataset_origin: str
    source_record_id: str | None = None
    source_file: str | None = None
    source_timestamp: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    site_name: str | None = None
    normalized_commodities: list[str] = Field(default_factory=list)
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    lithology: str | None = None
    geologic_age: str | None = None
    deposit_model: str | None = None
    measurement_units: list[str] = Field(default_factory=list)
    source_url: str | None = None
    geometry: CanonicalGeometry = Field(default_factory=CanonicalGeometry)

    raw_fields: dict[str, Any] = Field(default_factory=dict)
    reconciled_fields: dict[str, ReconciledField] = Field(default_factory=dict)
    source_metadata: SourceRecordMetadata
    field_confidence: dict[str, float] = Field(default_factory=dict)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_metadata: dict[str, Any] = Field(default_factory=dict)

    validation_status: Literal["valid", "warning", "invalid"] = "warning"
    validation_warnings: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    schema_drift_warnings: list[str] = Field(default_factory=list)
    unmapped_fields: list[str] = Field(default_factory=list)

    duplicate_group_id: str | None = None
    duplicate_candidates: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("normalized_commodities", "measurement_units", "validation_warnings", "validation_errors", "schema_drift_warnings", "unmapped_fields", mode="before")
    @classmethod
    def _coerce_string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value if str(item).strip()]

    @field_validator("raw_fields", "field_confidence", "confidence_metadata", mode="before")
    @classmethod
    def _coerce_dict(cls, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def to_export_dict(self) -> dict[str, Any]:
        """Return a JSON-safe flat-ish dict suitable for DataFrame/GeoParquet export."""

        import json

        data = _model_dump(self)
        for key in (
            "raw_fields",
            "reconciled_fields",
            "source_metadata",
            "field_confidence",
            "confidence_metadata",
            "duplicate_candidates",
            "conflicts",
        ):
            data[f"{key}_json"] = json.dumps(data.get(key), sort_keys=True, default=str)
            data.pop(key, None)
        data["geometry_metadata_json"] = json.dumps(data.pop("geometry", None), sort_keys=True, default=str)
        return data


def make_canonical_id(source_dataset: str, source_record_id: Any, site_name: Any, latitude: Any, longitude: Any) -> str:
    return deterministic_uuid("canonical-geology", source_dataset, source_record_id, site_name, latitude, longitude)


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
