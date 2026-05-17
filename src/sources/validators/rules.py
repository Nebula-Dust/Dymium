"""Source-specific validation rules for geological/geospatial ingestion."""
from __future__ import annotations
from typing import Any
from src.sources.schemas import SourceIngestionResult, SourceRecord, SourceValidationIssue

def validate_source_result(result: SourceIngestionResult) -> list[SourceValidationIssue]:
    """Run source-specific checks without discarding uncertain records."""
    issues: list[SourceValidationIssue] = []
    for record in result.records:
        record_issues = validate_source_record(record)
        record.validation_issues.extend(record_issues)
        issues.extend(record_issues)
    if result.descriptor.malformed:
        issues.append(SourceValidationIssue(severity="critical", code="source_descriptor_malformed", message="Source descriptor indicates malformed or unreadable source."))
    return issues

def validate_source_record(record: SourceRecord) -> list[SourceValidationIssue]:
    source = record.source_dataset.upper()
    if source == "MRDS":
        return _validate_mrds(record)
    if source == "GEOROC":
        return _validate_georoc(record)
    if source == "PETDB":
        return _validate_petdb(record)
    if source in {"NATURALEARTH"}:
        return _validate_geospatial_context(record)
    if source in {"MINERALSYEARBOOK", "OPERATORFILING", "PDFDOCUMENT"}:
        return _validate_report(record)
    return []

def _validate_mrds(record: SourceRecord) -> list[SourceValidationIssue]:
    issues = []
    if not _present(record.raw_fields.get("dep_id") or record.raw_fields.get("record_id")):
        issues.append(_issue("mrds_missing_deposit_id", "MRDS row is missing a deposit identifier.", record, severity="severe"))
    if not (record.geometry_metadata.get("valid") or record.geometry_metadata.get("coordinates")):
        issues.append(_issue("mrds_missing_valid_coordinates", "MRDS row lacks valid canonical coordinates.", record, severity="critical"))
    return issues

def _validate_georoc(record: SourceRecord) -> list[SourceValidationIssue]:
    issues = []
    if not _present(record.raw_fields.get("sample_id") or record.raw_fields.get("sample_name") or record.raw_fields.get("georoc_id")):
        issues.append(_issue("georoc_missing_sample_id", "GEOROC row is missing a sample identifier.", record, severity="severe"))
    if not _present(record.raw_fields.get("elements") or record.raw_fields.get("element") or record.raw_fields.get("analytes")):
        issues.append(_issue("georoc_missing_geochemical_terms", "GEOROC row has no geochemical element/analyte fields.", record))
    return issues

def _validate_petdb(record: SourceRecord) -> list[SourceValidationIssue]:
    issues = []
    if not _present(record.raw_fields.get("sample_id") or record.raw_fields.get("station_id") or record.raw_fields.get("petdb_id")):
        issues.append(_issue("petdb_missing_sample_id", "PetDB row is missing a sample or station identifier.", record, severity="severe"))
    if not _present(record.raw_fields.get("material") or record.raw_fields.get("rock_type") or record.raw_fields.get("rock_name")):
        issues.append(_issue("petdb_missing_sample_material", "PetDB row lacks material or rock type context.", record))
    return issues

def _validate_geospatial_context(record: SourceRecord) -> list[SourceValidationIssue]:
    if not record.geometry_metadata.get("valid"):
        return [_issue("context_invalid_geometry", "Geospatial context feature has missing or invalid geometry.", record, severity="critical")]
    if not _present(record.geometry_metadata.get("crs")):
        return [_issue("context_missing_crs", "Geospatial context feature has no CRS metadata.", record, severity="severe")]
    return []

def _validate_report(record: SourceRecord) -> list[SourceValidationIssue]:
    issues = []
    if record.raw_fields.get("document_type") == "malformed":
        issues.append(_issue("report_malformed_document", "Report could not be parsed as a valid PDF.", record, severity="critical"))
    if record.source_terms.get("pages_needing_ocr", 0):
        issues.append(_issue("report_ocr_dependent", "Report requires OCR for one or more pages.", record, severity="warning"))
    return issues

def _issue(code: str, message: str, record: SourceRecord, *, severity: str = "warning") -> SourceValidationIssue:
    return SourceValidationIssue(severity=severity, code=code, message=message, record_id=record.record_id, metadata={"source_dataset": record.source_dataset})

def _present(value: Any) -> bool:
    return value is not None and str(value).strip().lower() not in {"", "none", "null", "nan", "n/a"}
