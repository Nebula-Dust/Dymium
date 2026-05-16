"""Canonical schema reconciliation engine."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from src.etl.provenance import deterministic_uuid, utc_now
from src.reconciliation.adapters import ADAPTERS, AdapterResult, SourceAdapter
from src.reconciliation.canonical_schema import CanonicalGeologicalRecord
from src.reconciliation.metrics import generate_reconciliation_metrics
from src.reconciliation.validators import validate_record


@dataclass
class ReconciliationResult:
    records: list[CanonicalGeologicalRecord]
    metrics: dict[str, Any]
    adapter_metrics: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ReconciliationEngine:
    """Coordinates source adapters, validation, duplicate detection, and export."""

    def __init__(self, adapters: dict[str, type[SourceAdapter]] | None = None) -> None:
        self.adapters = adapters or ADAPTERS

    def adapt_dataframe(self, dataset: str, dataframe, *, source_file: str | None = None) -> AdapterResult:
        adapter = self._adapter_for(dataset)
        return adapter.adapt_dataframe(dataframe, source_file=source_file)

    def adapt_file(self, dataset: str, path: str | Path) -> AdapterResult:
        adapter = self._adapter_for(dataset)
        return adapter.adapt_file(path)

    def reconcile_adapter_results(self, results: list[AdapterResult]) -> ReconciliationResult:
        records = [record for result in results for record in result.records]
        warnings = [warning for result in results for warning in result.warnings]
        adapter_metrics = [result.metrics for result in results]
        return self.reconcile_records(records, adapter_metrics=adapter_metrics, warnings=warnings)

    def reconcile_records(
        self,
        records: list[CanonicalGeologicalRecord],
        *,
        adapter_metrics: list[dict[str, Any]] | None = None,
        warnings: list[str] | None = None,
    ) -> ReconciliationResult:
        validated = [validate_record(record) for record in records]
        self._annotate_duplicates(validated)
        metrics = generate_reconciliation_metrics(validated)
        metrics["generated_at"] = utc_now()
        return ReconciliationResult(records=validated, metrics=metrics, adapter_metrics=adapter_metrics or [], warnings=warnings or [])

    def records_to_dataframe(self, records: list[CanonicalGeologicalRecord], *, export_safe: bool = True):
        pd = _require_pandas()
        rows = [record.to_export_dict() if export_safe else _model_dump(record) for record in records]
        return pd.DataFrame.from_records(rows)

    def export_geoparquet(self, records: list[CanonicalGeologicalRecord], output_path: str | Path) -> Path:
        try:
            import geopandas as gpd
            from shapely.geometry import Point
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("GeoParquet reconciliation export requires geopandas and shapely.") from exc

        dataframe = self.records_to_dataframe(records, export_safe=True)
        geometry = []
        for record in records:
            if record.geometry.valid and record.longitude is not None and record.latitude is not None:
                geometry.append(Point(float(record.longitude), float(record.latitude)))
            else:
                geometry.append(None)
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        gdf = gpd.GeoDataFrame(dataframe, geometry=geometry, crs="EPSG:4326")
        gdf.to_parquet(output, index=False)
        return output

    def _adapter_for(self, dataset: str) -> SourceAdapter:
        key = str(dataset).strip()
        adapter_class = self.adapters.get(key) or self.adapters.get(key.upper())
        if adapter_class is None:
            raise ValueError(f"Unsupported reconciliation dataset: {dataset}")
        return adapter_class()

    def _annotate_duplicates(self, records: list[CanonicalGeologicalRecord]) -> None:
        groups: list[list[int]] = []
        used: set[int] = set()
        for left_index, left in enumerate(records):
            if left_index in used:
                continue
            group = [left_index]
            for right_index in range(left_index + 1, len(records)):
                if right_index in used:
                    continue
                right = records[right_index]
                similarity = _name_similarity(left.site_name, right.site_name)
                distance = _distance_km(left, right)
                same_name = similarity >= 0.90
                nearby = distance is not None and distance <= 5.0
                if same_name and (nearby or distance is None):
                    group.append(right_index)
                elif nearby and similarity >= 0.75:
                    group.append(right_index)
            if len(group) > 1:
                groups.append(group)
                used.update(group)

        for group in groups:
            group_records = [records[index] for index in group]
            group_id = deterministic_uuid("reconciliation-duplicate-group", *[record.canonical_id for record in group_records])
            conflicts = _group_conflicts(group_records)
            for record in group_records:
                candidates = []
                for candidate in group_records:
                    if candidate.canonical_id == record.canonical_id:
                        continue
                    candidates.append(
                        {
                            "canonical_id": candidate.canonical_id,
                            "source_dataset": candidate.source_dataset,
                            "source_record_id": candidate.source_record_id,
                            "site_name": candidate.site_name,
                            "distance_km": _distance_km(record, candidate),
                            "name_similarity": round(_name_similarity(record.site_name, candidate.site_name), 4),
                        }
                    )
                record.duplicate_group_id = group_id
                record.duplicate_candidates = candidates
                record.conflicts = [*record.conflicts, *conflicts]
                record.validation_warnings = sorted(set([*record.validation_warnings, "possible_duplicate_entity"]))
                if conflicts:
                    record.validation_warnings = sorted(set([*record.validation_warnings, "reconciliation_conflicts_present"]))
                    record.confidence_score = round(max(0.0, record.confidence_score - min(0.12, len(conflicts) * 0.04)), 4)
                    record.confidence_metadata["reconciliation_conflict_penalty"] = min(0.12, len(conflicts) * 0.04)
                validate_record(record)


def _group_conflicts(records: list[CanonicalGeologicalRecord]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    commodity_sets = {tuple(sorted(record.normalized_commodities)) for record in records if record.normalized_commodities}
    lithologies = {record.lithology for record in records if record.lithology}
    ages = {record.geologic_age for record in records if record.geologic_age}
    if len(commodity_sets) > 1:
        conflicts.append(_conflict("commodities", [list(values) for values in commodity_sets]))
    if len(lithologies) > 1:
        conflicts.append(_conflict("lithology", sorted(lithologies)))
    if len(ages) > 1:
        conflicts.append(_conflict("geologic_age", sorted(ages)))
    return conflicts


def _conflict(field: str, values: list[Any]) -> dict[str, Any]:
    return {
        "conflict_id": deterministic_uuid("schema-reconciliation-conflict", field, values),
        "field": field,
        "candidate_values": values,
        "resolution": "not_merged_review_required",
        "timestamp": utc_now(),
    }


def _name_similarity(left: Any, right: Any) -> float:
    left_key = _site_key(left)
    right_key = _site_key(right)
    if not left_key or not right_key:
        return 0.0
    return SequenceMatcher(None, left_key, right_key).ratio()


def _site_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _distance_km(left: CanonicalGeologicalRecord, right: CanonicalGeologicalRecord) -> float | None:
    if None in (left.latitude, left.longitude, right.latitude, right.longitude):
        return None
    radius_km = 6371.0088
    phi1 = math.radians(float(left.latitude))
    phi2 = math.radians(float(right.latitude))
    delta_phi = math.radians(float(right.latitude) - float(left.latitude))
    delta_lambda = math.radians(float(right.longitude) - float(left.longitude))
    haversine = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return round(2 * radius_km * math.asin(math.sqrt(haversine)), 4)


def _model_dump(record: CanonicalGeologicalRecord) -> dict[str, Any]:
    if hasattr(record, "model_dump"):
        return record.model_dump()
    return record.dict()


def _require_pandas():
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Schema reconciliation requires pandas.") from exc
    return pd
