"""Provenance and confidence metadata integrity validation."""

from __future__ import annotations

from typing import Any

from src.benchmarking.metrics import BenchmarkEvent


def validate_provenance_integrity(records: list[dict[str, Any]], *, stage: str = "provenance_integrity") -> dict[str, Any]:
    missing_provenance = 0
    missing_source_reference = 0
    malformed_confidence = 0
    invalid_chains = 0
    events: list[BenchmarkEvent] = []

    for index, record in enumerate(records):
        record_id = str(record.get("record_id") or record.get("canonical_id") or index)
        provenance = record.get("provenance")
        reconciled = record.get("reconciled_fields")
        if not isinstance(provenance, dict) and not isinstance(reconciled, dict):
            missing_provenance += 1
            events.append(BenchmarkEvent(event_type="missing_provenance", severity="severe", stage=stage, message="Record lacks structured provenance or reconciled field metadata.", record_id=record_id))
        if not _has_source_reference(record, provenance, reconciled):
            missing_source_reference += 1
            events.append(BenchmarkEvent(event_type="missing_source_reference", severity="warning", stage=stage, message="Record lacks a usable source reference.", record_id=record_id))
        invalid_chains += _invalid_transformation_chain_count(provenance, reconciled)
        malformed_confidence += _malformed_confidence_count(record)

    return {
        "total_records": len(records),
        "missing_provenance_count": missing_provenance,
        "missing_source_reference_count": missing_source_reference,
        "invalid_transformation_chain_count": invalid_chains,
        "malformed_confidence_metadata_count": malformed_confidence,
        "events": events,
    }


def _has_source_reference(record: dict[str, Any], provenance: Any, reconciled: Any) -> bool:
    if record.get("source_url") or record.get("source_file"):
        return True
    if isinstance(provenance, dict):
        for entry in provenance.get("fields", {}).values():
            if isinstance(entry, dict) and (entry.get("source_file") or entry.get("source_record_id")):
                return True
    if isinstance(reconciled, dict):
        for entry in reconciled.values():
            if isinstance(entry, dict):
                prov = entry.get("provenance", {})
                if isinstance(prov, dict) and (prov.get("source_file") or prov.get("source_record_id") or prov.get("source_dataset")):
                    return True
    return False


def _invalid_transformation_chain_count(provenance: Any, reconciled: Any) -> int:
    invalid = 0
    if isinstance(provenance, dict):
        for field, entry in provenance.get("fields", {}).items():
            if not isinstance(entry, dict):
                invalid += 1
                continue
            if not entry.get("method") and not entry.get("history"):
                invalid += 1
            for event in entry.get("history", []) or []:
                if isinstance(event, dict) and not event.get("method"):
                    invalid += 1
    if isinstance(reconciled, dict):
        for field, entry in reconciled.items():
            if not isinstance(entry, dict):
                invalid += 1
                continue
            if not entry.get("mapping_method"):
                invalid += 1
    return invalid


def _malformed_confidence_count(record: dict[str, Any]) -> int:
    malformed = 0
    for key, value in record.items():
        if not str(key).endswith("confidence") and key not in {"record_confidence", "stage_confidence", "confidence_score"}:
            continue
        if isinstance(value, dict):
            score = value.get("score")
        else:
            score = value
        if score is None:
            continue
        try:
            number = float(score)
        except (TypeError, ValueError):
            malformed += 1
            continue
        if not 0 <= number <= 1:
            malformed += 1
    return malformed
