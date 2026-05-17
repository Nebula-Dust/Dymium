"""Record-quality validation and completeness metrics."""

from __future__ import annotations

from collections import Counter
from typing import Any

from src.benchmarking.metrics import BenchmarkEvent, distribution

DEFAULT_REQUIRED_FIELDS = ("site_name", "commodities", "latitude", "longitude", "source_url")


def record_quality_metrics(records: list[dict[str, Any]], *, required_fields: tuple[str, ...] = DEFAULT_REQUIRED_FIELDS, stage: str = "record_quality") -> dict[str, Any]:
    total = len(records)
    missing_counts = {field: 0 for field in required_fields}
    duplicate_keys: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    conflict_count = 0
    confidence_values: list[float] = []
    events: list[BenchmarkEvent] = []

    for index, record in enumerate(records):
        for field in required_fields:
            if not _present(record.get(field)):
                missing_counts[field] += 1
        duplicate_key = _duplicate_key(record)
        if duplicate_key:
            duplicate_keys[duplicate_key] += 1
        warnings = _as_list(record.get("extraction_warnings")) + _as_list(record.get("validation_warnings"))
        for warning in warnings:
            warning_counts[str(warning).split(":", 1)[0]] += 1
        provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
        conflicts = _as_list(record.get("conflicts")) + _as_list(provenance.get("conflicts"))
        conflict_count += len(conflicts)
        confidence = _confidence_value(record)
        if confidence is not None:
            confidence_values.append(confidence)
        if any(not _present(record.get(field)) for field in required_fields):
            events.append(
                BenchmarkEvent(
                    event_type="missing_required_fields",
                    severity="warning",
                    stage=stage,
                    message="Record is missing one or more benchmark-required fields.",
                    record_id=str(record.get("record_id") or record.get("canonical_id") or index),
                    metadata={"missing_fields": [field for field in required_fields if not _present(record.get(field))]},
                )
            )

    duplicate_records = sum(count for count in duplicate_keys.values() if count > 1)
    missing_rates = {field: round(count / total, 4) if total else 0.0 for field, count in missing_counts.items()}
    completeness = {field: round(1.0 - rate, 4) for field, rate in missing_rates.items()}
    return {
        "total_records": total,
        "required_fields": list(required_fields),
        "missing_field_counts": missing_counts,
        "missing_field_rates": missing_rates,
        "extraction_completeness": completeness,
        "duplicate_records": duplicate_records,
        "duplicate_rate": round(duplicate_records / total, 4) if total else 0.0,
        "unresolved_conflicts": conflict_count,
        "warning_counts": dict(warning_counts),
        "confidence_distribution": distribution(confidence_values),
        "events": events,
    }


def dataframe_record_quality(dataframe, *, required_fields: tuple[str, ...] = DEFAULT_REQUIRED_FIELDS, stage: str = "record_quality") -> dict[str, Any]:
    return record_quality_metrics(dataframe.to_dict(orient="records"), required_fields=required_fields, stage=stage)


def _duplicate_key(record: dict[str, Any]) -> str | None:
    name = record.get("site_name") or record.get("location")
    lat = _to_float(record.get("latitude"))
    lon = _to_float(record.get("longitude"))
    if not name:
        return None
    coord = f":{round(lat, 4)}:{round(lon, 4)}" if lat is not None and lon is not None else ""
    return f"{str(name).strip().lower()}{coord}"


def _confidence_value(record: dict[str, Any]) -> float | None:
    for key in ("record_confidence", "confidence_score"):
        value = record.get(key)
        if isinstance(value, dict):
            value = value.get("score")
        number = _to_float(value)
        if number is not None:
            return number
    return None


def _present(value: Any) -> bool:
    if value is None:
        return False
    try:
        if value != value:
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(value, list):
        return bool(value)
    return str(value).strip().lower() not in {"", "none", "null", "nan", "na", "n/a"}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    try:
        if value != value:
            return []
    except (TypeError, ValueError):
        pass
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
