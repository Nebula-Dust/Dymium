"""Geospatial validation metrics for Dymium benchmark reports."""

from __future__ import annotations

from collections import Counter
from typing import Any

from src.benchmarking.metrics import BenchmarkEvent

TARGET_CRS = "EPSG:4326"


def validate_geospatial_records(records: list[dict[str, Any]], *, stage: str = "geospatial_validation", target_crs: str = TARGET_CRS) -> dict[str, Any]:
    total = len(records)
    missing_geometry = 0
    invalid_coordinate_pairs = 0
    crs_failures = 0
    duplicate_geometries: Counter[str] = Counter()
    impossible_spatial_joins = 0
    events: list[BenchmarkEvent] = []

    for index, record in enumerate(records):
        lat = _to_float(record.get("latitude"))
        lon = _to_float(record.get("longitude"))
        geometry = record.get("geometry")
        crs = _record_crs(record) or target_crs
        record_id = str(record.get("record_id") or record.get("canonical_id") or index)
        valid_pair = lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180
        if not valid_pair:
            invalid_coordinate_pairs += 1
            events.append(BenchmarkEvent(event_type="invalid_coordinates", severity="critical", stage=stage, message="Record has missing or invalid latitude/longitude.", record_id=record_id, metadata={"latitude": lat, "longitude": lon}))
        if geometry is None and not valid_pair:
            missing_geometry += 1
        if str(crs).upper() not in {target_crs.upper(), "WGS84", "WGS 84"}:
            crs_failures += 1
            events.append(BenchmarkEvent(event_type="crs_mismatch", severity="severe", stage=stage, message="Record CRS does not match expected geospatial benchmark CRS.", record_id=record_id, metadata={"crs": crs, "target_crs": target_crs}))
        if valid_pair:
            duplicate_geometries[f"{round(lat, 6)}:{round(lon, 6)}"] += 1
        if _present(record.get("geologic_unit")) and not valid_pair:
            impossible_spatial_joins += 1
            events.append(BenchmarkEvent(event_type="impossible_spatial_join", severity="critical", stage=stage, message="Record has geology enrichment but no valid point geometry.", record_id=record_id))

    duplicate_geometry_records = sum(count for count in duplicate_geometries.values() if count > 1)
    return {
        "total_records": total,
        "missing_geometry_count": missing_geometry,
        "missing_geometry_rate": round(missing_geometry / total, 4) if total else 0.0,
        "invalid_coordinate_pair_count": invalid_coordinate_pairs,
        "invalid_coordinate_pair_rate": round(invalid_coordinate_pairs / total, 4) if total else 0.0,
        "crs_failure_count": crs_failures,
        "duplicate_geometry_records": duplicate_geometry_records,
        "impossible_spatial_join_count": impossible_spatial_joins,
        "events": events,
    }


def validate_geodataframe(dataframe, *, stage: str = "geospatial_validation", target_crs: str = TARGET_CRS) -> dict[str, Any]:
    records = dataframe.to_dict(orient="records")
    metrics = validate_geospatial_records(records, stage=stage, target_crs=target_crs)
    crs = getattr(dataframe, "crs", None)
    if crs is not None and str(crs).upper() not in {target_crs.upper(), "WGS84", "WGS 84"}:
        metrics["crs_failure_count"] += 1
        metrics["events"].append(BenchmarkEvent(event_type="dataframe_crs_mismatch", severity="severe", stage=stage, message="GeoDataFrame CRS does not match expected benchmark CRS.", metadata={"crs": str(crs), "target_crs": target_crs}))
    if "geometry" in dataframe.columns:
        invalid_geom = 0
        try:
            invalid_geom = int((~dataframe.geometry.is_valid.fillna(False)).sum())
        except Exception:
            invalid_geom = 0
        metrics["invalid_geometry_count"] = invalid_geom
        if invalid_geom:
            metrics["events"].append(BenchmarkEvent(event_type="invalid_geometry", severity="critical", stage=stage, message="GeoDataFrame contains invalid geometries.", metric_name="invalid_geometry_count", metric_value=invalid_geom))
    return metrics


def validate_spatial_enrichment(input_records: list[dict[str, Any]], enriched_records: list[dict[str, Any]], *, stage: str = "spatial_enrichment") -> dict[str, Any]:
    total = len(enriched_records)
    matched = sum(1 for record in enriched_records if _present(record.get("geologic_unit")))
    impossible = sum(1 for record in enriched_records if _present(record.get("geologic_unit")) and not _valid_coordinates(record))
    return {
        "input_records": len(input_records),
        "output_records": total,
        "matched_geology": matched,
        "enrichment_coverage": round(matched / total, 4) if total else 0.0,
        "impossible_spatial_join_count": impossible,
        "events": [BenchmarkEvent(event_type="impossible_spatial_join", severity="critical", stage=stage, message="Geology matched records without valid coordinates exist.", metric_value=impossible)] if impossible else [],
    }


def _record_crs(record: dict[str, Any]) -> str | None:
    for key in ("crs", "source_crs"):
        if _present(record.get(key)):
            return str(record.get(key))
    metadata = record.get("geometry")
    if isinstance(metadata, dict) and _present(metadata.get("crs")):
        return str(metadata.get("crs"))
    return None


def _valid_coordinates(record: dict[str, Any]) -> bool:
    lat = _to_float(record.get("latitude"))
    lon = _to_float(record.get("longitude"))
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180


def _present(value: Any) -> bool:
    return value is not None and str(value).strip().lower() not in {"", "none", "null", "nan", "na", "n/a"}


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
