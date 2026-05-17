"""Geospatial context source adapters."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from src.etl.provenance import deterministic_uuid
from src.sources.adapters.base import SourceAdapter
from src.sources.provenance import build_source_provenance, make_ingestion_id
from src.sources.schemas import SourceIngestionResult, SourceRecord, SourceUpdateState, SourceValidationIssue
class NaturalEarthAdapter(SourceAdapter):
    source_name = "NaturalEarth"
    adapter_version = "natural-earth-source-adapter-v1"
    supported_formats = ("shp", "geojson", "gpkg", "zip")
    extraction_method = "geospatial_context_loader"
    def ingest(self, source: str | Path, *, prior_state: SourceUpdateState | None = None, source_version: str | None = None) -> SourceIngestionResult:
        descriptor = self.inspect(source, inspect_pdf=False)
        ingestion_id = make_ingestion_id(self.source_name, descriptor, source_version=source_version)
        if prior_state and prior_state.checksum_sha256 == descriptor.checksum_sha256:
            return SourceIngestionResult(ingestion_id=ingestion_id, source_dataset=self.source_name, descriptor=descriptor, metrics={"unchanged": True}, state={"checksum_sha256": descriptor.checksum_sha256}, warnings=["source_unchanged_skipped_incremental_ingestion"])
        records: list[SourceRecord] = []
        issues: list[SourceValidationIssue] = []
        try:
            import geopandas as gpd  # type: ignore
            frame = gpd.read_file(source)
            raw_crs = str(frame.crs) if frame.crs is not None else None
            if raw_crs is None:
                issues.append(SourceValidationIssue(severity="severe", code="missing_crs", message="Geospatial layer has no CRS."))
            for index, row in frame.iterrows():
                raw = {str(key): _json_ready(value) for key, value in row.drop(labels=["geometry"], errors="ignore").to_dict().items()}
                geometry = row.get("geometry")
                provenance = build_source_provenance(source_dataset=self.source_name, descriptor=descriptor, ingestion_id=ingestion_id, extraction_method=self.extraction_method, adapter_name=self.__class__.__name__, adapter_version=self.adapter_version, source_row_index=int(index) if isinstance(index, int) else None, source_schema_version=self.registry_metadata.get("schema_version"), registry_metadata=self.registry_metadata)
                records.append(SourceRecord(record_id=deterministic_uuid("source-geospatial", self.source_name, descriptor.checksum_sha256, index), source_dataset=self.source_name, source_kind="geospatial_layer", raw_fields=raw, source_terms={"context_layer": True, "geometry_role": "enrichment_context"}, geometry_metadata={"geometry_type": getattr(geometry, "geom_type", None), "bounds": list(geometry.bounds) if geometry is not None and not geometry.is_empty else None, "valid": bool(geometry is not None and geometry.is_valid), "crs": raw_crs}, provenance=provenance, validation_issues=[] if geometry is not None else [SourceValidationIssue(severity="critical", code="missing_geometry", message="Feature has no geometry.")], confidence_hints={"source_trust": self.registry.trust_level(self.source_name)}))
        except Exception as exc:
            issues.append(SourceValidationIssue(severity="critical", code="geospatial_load_failed", message=str(exc)))
        metrics = {"source_dataset": self.source_name, "input_features": len(records), "geometry_presence_rate": round(sum(1 for record in records if record.geometry_metadata.get("valid")) / len(records), 4) if records else 0.0, "crs": descriptor.crs, "validation_issue_count": len(issues) + sum(len(record.validation_issues) for record in records), "enrichment_context": True}
        return SourceIngestionResult(ingestion_id=ingestion_id, source_dataset=self.source_name, descriptor=descriptor, records=records, validation_issues=issues, metrics=metrics, state={"source_dataset": self.source_name, "source_file": descriptor.path, "checksum_sha256": descriptor.checksum_sha256, "source_version": source_version, "ingested_record_ids": [record.record_id for record in records]}, warnings=descriptor.schema_warnings)
def _json_ready(value: Any) -> Any:
    if value is None or value != value:
        return None
    if hasattr(value, "item"):
        return value.item()
    return value
