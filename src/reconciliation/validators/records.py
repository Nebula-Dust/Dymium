"""Validation for canonical geological reconciliation records."""

from __future__ import annotations

from typing import Any

from src.reconciliation.canonical_schema import CanonicalGeologicalRecord

REQUIRED_CANONICAL_FIELDS = ("source_dataset",)


def validate_record(record: CanonicalGeologicalRecord) -> CanonicalGeologicalRecord:
    """Validate without dropping records; errors and warnings stay on record."""

    warnings = list(record.validation_warnings)
    errors = list(record.validation_errors)

    for field in REQUIRED_CANONICAL_FIELDS:
        if not getattr(record, field, None):
            errors.append(f"missing_required_field:{field}")
    if not record.site_name and not record.source_record_id:
        errors.append("missing_identity:site_name_or_source_record_id")
    if not record.normalized_commodities:
        warnings.append("missing_normalized_commodities")
    if record.latitude is None or record.longitude is None:
        warnings.append("missing_valid_coordinates")
    else:
        if not -90 <= record.latitude <= 90:
            errors.append(f"latitude_out_of_range:{record.latitude}")
        if not -180 <= record.longitude <= 180:
            errors.append(f"longitude_out_of_range:{record.longitude}")
    if record.geometry.crs.upper() not in {"EPSG:4326", "WGS84", "WGS 84"}:
        warnings.append(f"non_standard_crs:{record.geometry.crs}")
    if record.geometry.valid and record.geometry.coordinates is None:
        errors.append("geometry_marked_valid_without_coordinates")
    if record.geometry.coordinates is not None and not record.geometry.valid:
        errors.append("geometry_coordinates_present_but_invalid")
    warnings.extend(record.geometry.warnings)
    warnings.extend(record.schema_drift_warnings)
    for field_name, reconciled in record.reconciled_fields.items():
        warnings.extend(f"{field_name}:{warning}" for warning in reconciled.warnings)

    record.validation_warnings = sorted(set(str(item) for item in warnings if str(item).strip()))
    record.validation_errors = sorted(set(str(item) for item in errors if str(item).strip()))
    if record.validation_errors:
        record.validation_status = "invalid"
    elif record.validation_warnings:
        record.validation_status = "warning"
    else:
        record.validation_status = "valid"
    return record


def schema_coverage(records: list[CanonicalGeologicalRecord]) -> dict[str, float]:
    fields = ["site_name", "normalized_commodities", "latitude", "longitude", "lithology", "geologic_age", "measurement_units", "source_url"]
    total = len(records)
    if total == 0:
        return {field: 0.0 for field in fields}
    coverage: dict[str, float] = {}
    for field in fields:
        present = 0
        for record in records:
            value = getattr(record, field)
            if isinstance(value, list):
                present += bool(value)
            else:
                present += value is not None
        coverage[field] = round(present / total, 4)
    return coverage


def detect_schema_drift(raw_fields: dict[str, Any], mapped_fields: list[str]) -> dict[str, list[str]]:
    mapped = set(mapped_fields)
    raw = set(raw_fields)
    return {"unmapped_fields": sorted(raw - mapped), "mapped_fields": sorted(mapped & raw)}
