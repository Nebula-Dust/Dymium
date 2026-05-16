"""Source adapter base classes for canonical geological reconciliation."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from src.reconciliation.canonical_schema import (
    CanonicalGeologicalRecord,
    CanonicalGeometry,
    FieldProvenance,
    ReconciledField,
    SourceRecordMetadata,
    make_canonical_id,
)
from src.reconciliation.ontology import CoordinateResult, MappingResult, OntologyMapper
from src.reconciliation.validators.records import validate_record


@dataclass
class AdapterResult:
    records: list[CanonicalGeologicalRecord]
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


class SourceAdapter:
    """Dataset-specific adapter that preserves raw source semantics."""

    dataset_name = "UNKNOWN"
    adapter_version = "adapter-v1"
    source_schema_version = "unknown"
    field_aliases: dict[str, tuple[str, ...]] = {}
    required_any: tuple[str, ...] = ("site_name", "source_record_id")

    def __init__(self, mapper: OntologyMapper | None = None) -> None:
        self.mapper = mapper or OntologyMapper()

    def read_source(self, path: str | Path):
        pd = _require_pandas()
        source_path = Path(path)
        delimiter = _sniff_delimiter(source_path)
        return pd.read_csv(source_path, sep=delimiter, dtype="string", keep_default_na=False, na_values=[], low_memory=False, quoting=csv.QUOTE_MINIMAL)

    def adapt_file(self, path: str | Path) -> AdapterResult:
        dataframe = self.read_source(path)
        return self.adapt_dataframe(dataframe, source_file=str(path))

    def adapt_dataframe(self, dataframe, *, source_file: str | None = None, source_schema_version: str | None = None) -> AdapterResult:
        warnings = self.validate_source_structure(dataframe)
        records = [self.adapt_row(row.to_dict(), row_index=index, source_file=source_file, source_schema_version=source_schema_version) for index, row in dataframe.iterrows()]
        metrics = {
            "source_dataset": self.dataset_name,
            "rows": len(records),
            "schema_warnings": len(warnings),
            "invalid_records": sum(1 for record in records if record.validation_status == "invalid"),
            "unmapped_fields": sorted({field for record in records for field in record.unmapped_fields}),
        }
        return AdapterResult(records=records, metrics=metrics, warnings=warnings)

    def validate_source_structure(self, dataframe) -> list[str]:
        warnings: list[str] = []
        columns = {_clean_column(column) for column in dataframe.columns}
        for canonical_field in self.required_any:
            aliases = self.field_aliases.get(canonical_field, ())
            if not any(_clean_column(alias) in columns for alias in aliases):
                warnings.append(f"missing_expected_field_group:{canonical_field}")
        return warnings

    def adapt_row(
        self,
        row: dict[str, Any],
        *,
        row_index: int | None = None,
        source_file: str | None = None,
        source_schema_version: str | None = None,
    ) -> CanonicalGeologicalRecord:
        raw_fields = {str(key): _json_ready(value) for key, value in row.items()}
        resolver = _RowResolver(row, self.field_aliases)
        source_trust = self.mapper.source_trust(self.dataset_name)
        source_record_id, source_record_field = resolver.first("source_record_id")
        site_name, site_field = resolver.first("site_name")
        commodity_raw, commodity_field = resolver.first("commodities")
        lat_raw, lat_field = resolver.first("latitude")
        lon_raw, lon_field = resolver.first("longitude")
        crs_raw, crs_field = resolver.first("crs")
        lithology_raw, lithology_field = resolver.first("lithology")
        age_raw, age_field = resolver.first("geologic_age")
        source_url, source_url_field = resolver.first("source_url")
        units_raw, units_field = resolver.first("units")
        source_timestamp, source_timestamp_field = resolver.first("source_timestamp")

        commodity_mapping = self.mapper.map_commodities(commodity_raw)
        coordinate_mapping = self.mapper.normalize_coordinates(lat_raw, lon_raw, crs_raw)
        lithology_mapping = self.mapper.map_lithology(lithology_raw)
        age_mapping = self.mapper.map_geologic_age(age_raw)
        site_mapping = _identity_mapping(site_name, method="site_name_text_cleanup_v1", confidence=0.93 if _present(site_name) else 0.0, missing_warning="missing_site_name")
        source_url_mapping = _identity_mapping(source_url, method="source_reference_retention_v1", confidence=0.88 if _present(source_url) else 0.0, missing_warning="missing_source_reference")
        units_mapping = self.mapper.map_units(units_raw)

        mapped_source_fields = [field for field in (source_record_field, site_field, commodity_field, lat_field, lon_field, crs_field, lithology_field, age_field, source_url_field, units_field, source_timestamp_field) if field]
        unmapped_fields = [field for field in raw_fields if field not in mapped_source_fields]
        schema_warnings = self._schema_drift_warnings(resolver)
        canonical_id = make_canonical_id(self.dataset_name, source_record_id, site_name, coordinate_mapping.latitude, coordinate_mapping.longitude)
        reconciled_fields = {
            "site_name": self._reconciled_field("site_name", site_field, site_name, site_mapping, source_trust, source_record_id, source_file, row_index),
            "commodities": self._reconciled_field("commodities", commodity_field, commodity_raw, commodity_mapping, source_trust, source_record_id, source_file, row_index),
            "coordinates": self._coordinate_field(lat_field, lon_field, lat_raw, lon_raw, coordinate_mapping, source_trust, source_record_id, source_file, row_index),
            "lithology": self._reconciled_field("lithology", lithology_field, lithology_raw, lithology_mapping, source_trust, source_record_id, source_file, row_index),
            "geologic_age": self._reconciled_field("geologic_age", age_field, age_raw, age_mapping, source_trust, source_record_id, source_file, row_index),
            "source_url": self._reconciled_field("source_url", source_url_field, source_url, source_url_mapping, source_trust, source_record_id, source_file, row_index),
            "measurement_units": self._reconciled_field("measurement_units", units_field, units_raw, units_mapping, source_trust, source_record_id, source_file, row_index),
        }
        field_confidence = {field: round(value.mapping_confidence * value.source_trust, 4) for field, value in reconciled_fields.items()}
        confidence_score = _record_confidence(field_confidence, coordinate_mapping)
        metadata = SourceRecordMetadata(
            source_dataset=self.dataset_name,
            source_file=source_file,
            source_row_index=row_index,
            source_schema_version=source_schema_version or self.source_schema_version,
            adapter_version=self.adapter_version,
            raw_fields=raw_fields,
            mapped_source_fields=mapped_source_fields,
            unmapped_fields=unmapped_fields,
            compatibility_warnings=schema_warnings,
        )
        geometry = CanonicalGeometry(
            coordinates=(coordinate_mapping.longitude, coordinate_mapping.latitude) if coordinate_mapping.valid and coordinate_mapping.latitude is not None and coordinate_mapping.longitude is not None else None,
            crs=coordinate_mapping.crs,
            valid=coordinate_mapping.valid,
            warnings=coordinate_mapping.warnings,
        )
        record = CanonicalGeologicalRecord(
            canonical_id=canonical_id,
            source_dataset=self.dataset_name,
            dataset_origin=self.dataset_name,
            source_record_id=_clean_scalar(source_record_id),
            source_file=source_file,
            source_timestamp=_clean_scalar(source_timestamp),
            site_name=_clean_scalar(site_mapping.normalized_value),
            normalized_commodities=[str(value) for value in commodity_mapping.normalized_values],
            latitude=coordinate_mapping.latitude,
            longitude=coordinate_mapping.longitude,
            lithology=_clean_scalar(lithology_mapping.normalized_value),
            geologic_age=_clean_scalar(age_mapping.normalized_value),
            deposit_model=_clean_scalar(lithology_mapping.metadata.get("deposit_model")),
            measurement_units=[str(value) for value in units_mapping.normalized_values],
            source_url=_clean_scalar(source_url_mapping.normalized_value),
            geometry=geometry,
            raw_fields=raw_fields,
            reconciled_fields=reconciled_fields,
            source_metadata=metadata,
            field_confidence=field_confidence,
            confidence_score=confidence_score,
            confidence_metadata={
                "source_trust": source_trust,
                "record_confidence_method": "weighted_field_mapping_confidence_v1",
                "critical_uncertainty": [*coordinate_mapping.warnings, *schema_warnings],
            },
            schema_drift_warnings=schema_warnings,
            unmapped_fields=unmapped_fields,
        )
        return validate_record(record)

    def _schema_drift_warnings(self, resolver: "_RowResolver") -> list[str]:
        warnings: list[str] = []
        for canonical_field, aliases in self.field_aliases.items():
            if canonical_field in {"crs", "source_timestamp"}:
                continue
            if not resolver.has_any(canonical_field):
                warnings.append(f"missing_field_group:{canonical_field}")
        return warnings

    def _reconciled_field(
        self,
        canonical_field: str,
        raw_field: str | None,
        raw_value: Any,
        mapping: MappingResult,
        source_trust: float,
        source_record_id: Any,
        source_file: str | None,
        row_index: int | None,
    ) -> ReconciledField:
        return ReconciledField(
            raw_field=raw_field,
            raw_value=_json_ready(raw_value),
            normalized_value=_json_ready(mapping.normalized_value),
            normalized_values=[_json_ready(value) for value in mapping.normalized_values],
            mapping_method=mapping.method,
            mapping_confidence=mapping.confidence,
            source_trust=source_trust,
            warnings=mapping.warnings,
            provenance=FieldProvenance(
                source_dataset=self.dataset_name,
                source_field=raw_field,
                source_record_id=_clean_scalar(source_record_id),
                source_file=source_file,
                source_row_index=row_index,
                adapter=self.__class__.__name__,
                transformation_method=mapping.method,
            ),
            normalization_events=mapping.normalization_events,
        )

    def _coordinate_field(self, lat_field, lon_field, lat_raw, lon_raw, mapping: CoordinateResult, source_trust, source_record_id, source_file, row_index) -> ReconciledField:
        return ReconciledField(
            raw_field="/".join(field for field in (lat_field, lon_field) if field) or None,
            raw_value={"latitude": _json_ready(lat_raw), "longitude": _json_ready(lon_raw)},
            normalized_value={"latitude": mapping.latitude, "longitude": mapping.longitude, "crs": mapping.crs},
            normalized_values=[mapping.latitude, mapping.longitude] if mapping.valid else [],
            mapping_method=mapping.method,
            mapping_confidence=mapping.confidence,
            source_trust=source_trust,
            warnings=mapping.warnings,
            provenance=FieldProvenance(
                source_dataset=self.dataset_name,
                source_field="/".join(field for field in (lat_field, lon_field) if field) or None,
                source_record_id=_clean_scalar(source_record_id),
                source_file=source_file,
                source_row_index=row_index,
                adapter=self.__class__.__name__,
                transformation_method=mapping.method,
            ),
            normalization_events=[],
        )


class _RowResolver:
    def __init__(self, row: dict[str, Any], aliases: dict[str, tuple[str, ...]]) -> None:
        self.row = row
        self.aliases = aliases
        self.columns_by_clean = {_clean_column(column): column for column in row}

    def first(self, canonical_field: str) -> tuple[Any, str | None]:
        for alias in self.aliases.get(canonical_field, ()):
            source_field = self.columns_by_clean.get(_clean_column(alias))
            if source_field is None:
                continue
            value = self.row.get(source_field)
            if _present(value):
                return value, source_field
        return None, None

    def has_any(self, canonical_field: str) -> bool:
        return any(_clean_column(alias) in self.columns_by_clean for alias in self.aliases.get(canonical_field, ()))


def _identity_mapping(value: Any, *, method: str, confidence: float, missing_warning: str) -> MappingResult:
    cleaned = _clean_scalar(value)
    return MappingResult(raw_value=value, normalized_value=cleaned, normalized_values=[cleaned] if cleaned else [], method=method if cleaned else "missing_value", confidence=confidence, warnings=[] if cleaned else [missing_warning])


def _record_confidence(field_confidence: dict[str, float], coordinates: CoordinateResult) -> float:
    weights = {"site_name": 0.18, "commodities": 0.22, "coordinates": 0.24, "lithology": 0.10, "geologic_age": 0.08, "source_url": 0.08}
    total = sum(field_confidence.get(field, 0.0) * weight for field, weight in weights.items())
    weight_total = sum(weights.values())
    score = total / weight_total if weight_total else 0.0
    if not coordinates.valid:
        score = min(score, 0.62)
    return round(max(0.0, min(score, 1.0)), 4)


def _clean_column(column: Any) -> str:
    return re.sub(r"[^0-9a-zA-Z]+", "_", str(column).strip().lower()).strip("_")


def _clean_scalar(value: Any) -> str | None:
    if not _present(value):
        return None
    return str(value).strip().strip('"')


def _present(value: Any) -> bool:
    if value is None:
        return False
    try:
        if value != value:
            return False
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() not in {"", "na", "n/a", "null", "none", "nan", "-999", "-9999"}


def _json_ready(value: Any) -> Any:
    if not _present(value):
        return None
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    return value


def _sniff_delimiter(path: Path) -> str:
    sample = path.read_text(encoding="utf-8-sig", errors="ignore")[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t|;").delimiter
    except csv.Error:
        first_line = sample.splitlines()[0] if sample else ""
        return "\t" if first_line.count("\t") >= first_line.count(",") else ","


def _require_pandas():
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Schema reconciliation adapters require pandas.") from exc
    return pd
