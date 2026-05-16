"""Deterministic, provenance-aware confidence scoring."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from statistics import mean
from typing import Any

from ..provenance import ensure_provenance, source_priority_for, utc_now
from .calibration import DEFAULT_CALIBRATION_REGISTRY, CalibrationRegistry
from .config import ConfidenceConfig, load_confidence_config
from .dependencies import apply_dependency_rules
from .events import evidence_event, penalty_event, warning_events_from_values

FIELD_CONFIDENCE_COLUMNS = [
    "site_name_confidence",
    "coordinates_confidence",
    "commodity_confidence",
    "lithology_confidence",
    "geologic_age_confidence",
    "operator_confidence",
    "assay_confidence",
    "source_url_confidence",
    "geometry_confidence",
]
RECORD_CONFIDENCE_COLUMN = "record_confidence"
STAGE_CONFIDENCE_COLUMN = "stage_confidence"
CONFIDENCE_COLUMNS = [*FIELD_CONFIDENCE_COLUMNS, RECORD_CONFIDENCE_COLUMN, STAGE_CONFIDENCE_COLUMN]


def confidence_assessment(
    score: float | int | None,
    *,
    field: str | None,
    stage: str,
    method: str,
    source: str | None = None,
    factors: list[str] | tuple[str, ...] | None = None,
    penalties: list[str] | tuple[str, ...] | None = None,
    evidence: dict[str, Any] | None = None,
    evidence_events: list[dict[str, Any]] | None = None,
    penalty_lineage: list[dict[str, Any]] | None = None,
    derivation: dict[str, Any] | None = None,
    config: ConfidenceConfig | None = None,
    context: dict[str, Any] | None = None,
    calibrator: CalibrationRegistry | None = None,
) -> dict[str, Any]:
    """Return a stable confidence object with explainability metadata."""

    cfg = config or load_confidence_config()
    assessment = {
        "score": _clamp(score),
        "field": field,
        "stage": stage,
        "method": method,
        "source": source or "unknown",
        "factors": _clean_strings(factors),
        "penalties": _clean_strings(penalties),
        "evidence": evidence or {"summary": None},
        "evidence_events": evidence_events or [],
        "penalty_lineage": penalty_lineage or [],
        "derivation": derivation or {"dependencies": []},
        "confidence_version": "dymium-confidence-v1",
        "config_hash": cfg.config_hash,
        "timestamp": utc_now(),
    }
    return (calibrator or DEFAULT_CALIBRATION_REGISTRY).apply(assessment, context=context or {}, config=cfg)


def attach_record_confidence(
    record: dict[str, Any],
    *,
    stage: str,
    config: ConfidenceConfig | None = None,
    calibrator: CalibrationRegistry | None = None,
) -> dict[str, Any]:
    """Attach field, record, and stage confidence without removing legacy fields."""

    cfg = config or load_confidence_config()
    enriched = dict(record)
    provenance = ensure_provenance(enriched.get("provenance"), record_uuid=enriched.get("record_uuid"))
    context = _record_context(enriched, provenance, cfg, stage=stage)
    calibrator = calibrator or DEFAULT_CALIBRATION_REGISTRY

    field_confidences = {
        "site_name_confidence": _score_site_name(enriched, provenance, stage, context, cfg, calibrator),
        "coordinates_confidence": _score_coordinates(enriched, provenance, stage, context, cfg, calibrator),
        "commodity_confidence": _score_commodities(enriched, provenance, stage, context, cfg, calibrator),
        "lithology_confidence": _score_geology_field(enriched, provenance, "lithology", stage, context, cfg, calibrator),
        "geologic_age_confidence": _score_geology_field(enriched, provenance, "geologic_age", stage, context, cfg, calibrator),
        "operator_confidence": _score_optional_field(enriched, provenance, "operator", stage, context, cfg, calibrator),
        "assay_confidence": _score_assay(enriched, provenance, stage, context, cfg, calibrator),
        "source_url_confidence": _score_optional_field(enriched, provenance, "source_url", stage, context, cfg, calibrator),
        "geometry_confidence": _score_geometry(enriched, provenance, stage, context, cfg, calibrator),
    }
    field_confidences = apply_dependency_rules(field_confidences, context=context, config=cfg)
    enriched.update(field_confidences)

    record_assessment = _record_confidence(field_confidences, provenance, stage, context, cfg, calibrator)
    record_assessment = apply_dependency_rules({"record_confidence": record_assessment}, context={**context, **field_confidences}, config=cfg)["record_confidence"]
    stage_assessment = _stage_confidence(stage, field_confidences, context, cfg, calibrator)
    stage_assessment = apply_dependency_rules({"stage_confidence": stage_assessment}, context=context, config=cfg)["stage_confidence"]
    enriched[RECORD_CONFIDENCE_COLUMN] = record_assessment
    enriched[STAGE_CONFIDENCE_COLUMN] = stage_assessment
    enriched["confidence_score"] = record_assessment["score"]
    return enriched


def attach_dataframe_confidence(dataframe, *, stage: str, config: ConfidenceConfig | None = None):
    pd = _require_pandas()
    cfg = config or load_confidence_config()
    records = [attach_record_confidence(record, stage=stage, config=cfg) for record in dataframe.to_dict(orient="records")]
    return pd.DataFrame.from_records(records, index=dataframe.index)


def validation_report(dataframe, *, stage: str | None = None, config: ConfidenceConfig | None = None) -> dict[str, Any]:
    cfg = config or load_confidence_config()
    return {
        "stage": stage,
        "total_records": len(dataframe),
        "config_hash": cfg.config_hash,
        "thresholds": cfg.thresholds,
        "record_confidence": _series_distribution(_extract_scores(dataframe, RECORD_CONFIDENCE_COLUMN)),
        "field_confidence": {column: _series_distribution(_extract_scores(dataframe, column)) for column in FIELD_CONFIDENCE_COLUMNS if column in dataframe.columns},
        "dependency_failures": dependency_failure_summary(dataframe),
        "reconciliation_degradation": reconciliation_degradation_metrics(dataframe),
        "validation": {
            "missing_coordinates": _count_missing_coordinates(dataframe),
            "invalid_geometry": _count_invalid_geometry(dataframe),
            "records_with_conflicts": _count_records_with_conflicts(dataframe),
            "records_with_warnings": _count_records_with_warnings(dataframe),
        },
    }


def confidence_histogram(values: list[float] | tuple[float, ...]) -> dict[str, int]:
    buckets = {"0.00-0.24": 0, "0.25-0.49": 0, "0.50-0.74": 0, "0.75-1.00": 0}
    for value in values:
        score = _clamp(value)
        if score < 0.25:
            buckets["0.00-0.24"] += 1
        elif score < 0.5:
            buckets["0.25-0.49"] += 1
        elif score < 0.75:
            buckets["0.50-0.74"] += 1
        else:
            buckets["0.75-1.00"] += 1
    return buckets


def confidence_drift_report(baseline_dataframe, current_dataframe, *, column: str = RECORD_CONFIDENCE_COLUMN) -> dict[str, Any]:
    baseline = _extract_scores(baseline_dataframe, column)
    current = _extract_scores(current_dataframe, column)
    baseline_mean = mean(baseline) if baseline else None
    current_mean = mean(current) if current else None
    return {
        "column": column,
        "baseline": _series_distribution(baseline),
        "current": _series_distribution(current),
        "mean_delta": round(current_mean - baseline_mean, 4) if baseline_mean is not None and current_mean is not None else None,
    }


def calibration_diagnostics(dataframe, *, config: ConfidenceConfig | None = None) -> dict[str, Any]:
    cfg = config or load_confidence_config()
    return {
        "config_hash": cfg.config_hash,
        "config_errors": cfg.errors,
        "calibration_status": "identity_calibrator_only",
        "record_confidence": _series_distribution(_extract_scores(dataframe, RECORD_CONFIDENCE_COLUMN)),
        "recommended_next_steps": [
            "benchmark against manually adjudicated deposit records",
            "fit source-specific calibration curves",
            "track drift by source and pipeline stage",
        ],
    }


def dependency_failure_summary(dataframe) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for column in CONFIDENCE_COLUMNS:
        if column not in dataframe.columns:
            continue
        for value in dataframe[column]:
            if not isinstance(value, dict):
                continue
            for inherited in value.get("inherited_penalties", []) or []:
                if isinstance(inherited, dict):
                    counts[str(inherited.get("reason", "unknown_dependency_failure"))] += 1
    return dict(counts)


def reconciliation_degradation_metrics(dataframe) -> dict[str, Any]:
    conflict_records = _count_records_with_conflicts(dataframe)
    warnings = _count_records_with_warnings(dataframe)
    total = len(dataframe)
    return {
        "records_with_conflicts": conflict_records,
        "records_with_warnings": warnings,
        "conflict_rate": round(conflict_records / total, 4) if total else 0.0,
        "warning_rate": round(warnings / total, 4) if total else 0.0,
    }


def _score_site_name(record, provenance, stage, context, config, calibrator):
    value = _clean_text(record.get("site_name"))
    base, factors, penalties, evidence = _base_from_provenance(provenance, "site_name", stage=stage, config=config)
    if value:
        delta = config.modifier("site_name_present")
        base += delta
        factors.append("site name present")
        evidence.append(evidence_event("presence", "site name present", field="site_name", score_delta=delta))
    else:
        base -= config.penalty("missing_required_field")
        penalties.append("missing site name")
    return _finalize(base, "site_name_confidence", stage, "field_weighted_evidence", context, config, factors, penalties, evidence, calibrator)


def _score_coordinates(record, provenance, stage, context, config, calibrator):
    base, factors, penalties, evidence = _base_from_provenance(provenance, "latitude", "longitude", stage=stage, config=config)
    if context["valid_coordinates"]:
        delta = config.modifier("coordinate_valid")
        base += delta
        factors.append("latitude and longitude are within EPSG:4326 bounds")
        evidence.append(evidence_event("validation", "coordinates within EPSG:4326 bounds", field="coordinates", score_delta=delta))
    elif context["partial_coordinates"]:
        base -= config.penalty("partial_coordinates")
        penalties.append("partial coordinate pair")
    else:
        base -= config.penalty("invalid_coordinates")
        penalties.append("missing or invalid coordinates")
    if context["has_geology_match"]:
        base += config.modifier("coordinate_geology_match")
        factors.append("coordinates joined to geology layer")
    if _has_conflict(provenance, {"latitude", "longitude"}):
        base -= config.penalty("source_conflict")
        penalties.append("conflicting coordinate candidates retained in provenance")
    return _finalize(base, "coordinates_confidence", stage, "coordinate_evidence_weighting", context, config, factors, penalties, evidence, calibrator)


def _score_commodities(record, provenance, stage, context, config, calibrator):
    values = _listify(record.get("commodities"))
    base, factors, penalties, evidence = _base_from_provenance(provenance, "commodities", "commodity_codes", "commod1", "commod2", stage=stage, config=config)
    if values:
        delta = config.modifier("commodity_present")
        base += delta
        factors.append("commodity values present")
        evidence.append(evidence_event("presence", "commodity values present", field="commodities", score_delta=delta, metadata={"count": len(values)}))
    else:
        base -= config.penalty("missing_required_field")
        penalties.append("missing commodity values")
    if _field_history_sources(provenance, "commodities") >= 2:
        base += config.modifier("commodity_multi_source")
        factors.append("commodity evidence from multiple sources")
    normalization_delta = _normalization_delta(provenance, "commodities")
    if normalization_delta:
        base += normalization_delta
        factors.append("structured ontology normalization event applied")
    if _has_conflict(provenance, {"commodities", "commod1", "commod2"}):
        base -= config.penalty("commodity_conflict")
        penalties.append("conflicting commodity labels retained in provenance")
    return _finalize(base, "commodity_confidence", stage, "commodity_evidence_weighting", context, config, factors, penalties, evidence, calibrator)


def _score_geology_field(record, provenance, field, stage, context, config, calibrator):
    value = _clean_text(record.get(field))
    base, factors, penalties, evidence = _base_from_provenance(provenance, field, stage=stage, config=config)
    if value:
        base += config.modifier("geology_field_present")
        factors.append(f"{field} present")
        if context["has_geology_match"]:
            base += config.modifier("geology_spatial_assignment")
            factors.append("assigned by spatial geology enrichment")
    else:
        base -= config.penalty("missing_geology_field")
        penalties.append(f"missing {field}")
    normalization_delta = _normalization_delta(provenance, field)
    if normalization_delta:
        base += normalization_delta
        factors.append("structured geologic normalization event applied")
    if _has_conflict(provenance, {field}):
        base -= config.penalty("geology_conflict")
        penalties.append(f"multiple {field} interpretations retained in provenance")
    return _finalize(base, f"{field}_confidence", stage, f"{field}_evidence_weighting", context, config, factors, penalties, evidence, calibrator)


def _score_optional_field(record, provenance, field, stage, context, config, calibrator):
    value = _clean_text(record.get(field))
    base, factors, penalties, evidence = _base_from_provenance(provenance, field, stage=stage, config=config)
    if value:
        base += config.modifier("optional_field_present")
        factors.append(f"{field} present")
    else:
        base -= config.penalty("missing_optional_field")
        penalties.append(f"missing {field}")
    return _finalize(base, f"{field}_confidence", stage, f"{field}_evidence_weighting", context, config, factors, penalties, evidence, calibrator)


def _score_assay(record, provenance, stage, context, config, calibrator):
    base, factors, penalties, evidence = _base_from_provenance(provenance, "grade", "tonnage", stage=stage, config=config)
    has_grade = _coerce_float(record.get("grade")) is not None
    has_tonnage = _coerce_float(record.get("tonnage")) is not None
    if has_grade or has_tonnage:
        base += config.modifier("assay_present")
        factors.append("assay or resource numeric field present")
    else:
        base -= config.penalty("missing_assay")
        penalties.append("no assay/resource numeric values")
    if has_grade and has_tonnage:
        base += config.modifier("grade_and_tonnage_present")
        factors.append("grade and tonnage both present")
    if _has_conflict(provenance, {"grade", "tonnage"}):
        base -= config.penalty("assay_conflict")
        penalties.append("conflicting assay/resource candidates retained in provenance")
    return _finalize(base, "assay_confidence", stage, "assay_evidence_weighting", context, config, factors, penalties, evidence, calibrator)


def _score_geometry(record, provenance, stage, context, config, calibrator):
    base, factors, penalties, evidence = _base_from_provenance(provenance, "geometry", "latitude", "longitude", stage=stage, config=config)
    if context["valid_coordinates"]:
        base += config.modifier("geometry_valid_coordinates")
        factors.append("valid point coordinates available")
    else:
        base -= config.penalty("invalid_geometry")
        penalties.append("geometry cannot be trusted without valid coordinates")
    if record.get("geometry") is not None:
        base += config.modifier("geometry_field_present")
        factors.append("geometry field present")
    if context["has_geology_match"]:
        base += config.modifier("geometry_geology_intersection")
        factors.append("geometry intersects geology polygon")
    return _finalize(base, "geometry_confidence", stage, "geometry_validation_weighting", context, config, factors, penalties, evidence, calibrator)


def _record_confidence(field_confidences, provenance, stage, context, config, calibrator):
    score_total = 0.0
    weight_total = 0.0
    evidence = []
    for field, weight in config.field_weights.items():
        assessment = field_confidences.get(field)
        if not assessment:
            continue
        score = _coerce_float(assessment.get("score"))
        if score is None:
            continue
        score_total += score * float(weight)
        weight_total += float(weight)
        evidence.append(evidence_event("field_rollup", f"{field}={score}", field="record_confidence", score_delta=0.0))
    score = score_total / weight_total if weight_total else 0.0
    factors = ["weighted field confidence aggregation"]
    penalties = []
    if ensure_provenance(provenance).get("conflicts"):
        score -= config.penalty("record_conflict")
        penalties.append("record contains resolved source conflicts")
    return confidence_assessment(score, field="record_confidence", stage=stage, method="record_weighted_aggregation", source="pipeline", factors=factors, penalties=penalties, evidence={"fields": {k: v.get("score") for k, v in field_confidences.items()}}, evidence_events=_dedupe_events(evidence), config=config, context=context, calibrator=calibrator)


def _stage_confidence(stage, field_confidences, context, config, calibrator):
    scores = [_coerce_float(value.get("score")) for value in field_confidences.values()]
    numeric = [score for score in scores if score is not None]
    score = mean(numeric) if numeric else 0.0
    stage_delta = config.stage_modifier(stage)
    if stage_delta:
        score += stage_delta
    penalty_lineage = _warning_penalties(context, config)
    if penalty_lineage:
        score -= min(sum(item.get("amount") or config.severity_penalty(item.get("severity", "warning")) for item in penalty_lineage), config.penalty("warning_max"))
    return confidence_assessment(score, field="stage_confidence", stage=stage, method="stage_confidence_average", source=context.get("source"), factors=[f"{stage} stage field confidence average", *( ["stage modifier applied"] if stage_delta else [] )], penalties=[item["reason"] for item in penalty_lineage], penalty_lineage=penalty_lineage, evidence={"field_count": len(numeric)}, config=config, context=context, calibrator=calibrator)


def _finalize(score, field, stage, method, context, config, factors, penalties, evidence, calibrator):
    penalty_lineage = _warning_penalties(context, config, field=field)
    if penalty_lineage:
        score -= min(sum(item.get("amount") or config.severity_penalty(item.get("severity", "warning")) for item in penalty_lineage), config.penalty("warning_max"))
        penalties.extend(item["reason"] for item in penalty_lineage)
    return confidence_assessment(score, field=field, stage=stage, method=method, source=context.get("source"), factors=factors, penalties=penalties, evidence_events=_dedupe_events(evidence), penalty_lineage=penalty_lineage, evidence={"context": _public_context(context)}, derivation={"parents": []}, config=config, context=context, calibrator=calibrator)


def _base_from_provenance(provenance, *fields, stage, config):
    provenance = ensure_provenance(provenance)
    entries = [provenance.get("fields", {}).get(field) for field in fields]
    entries = [entry for entry in entries if isinstance(entry, dict)]
    factors: list[str] = []
    penalties: list[str] = []
    evidence: list[dict[str, Any]] = []
    if not entries:
        penalties.append("no field-level provenance")
        return 0.34, factors, penalties, evidence
    scores: list[float] = []
    for entry in entries:
        source = entry.get("source") or "UNKNOWN"
        method = entry.get("method") or "unknown"
        source_score = config.source_score(source)
        method_score = config.method_score(method)
        scores.extend([source_score, method_score])
        event_score = _coerce_float(entry.get("confidence"))
        if event_score is not None:
            scores.append(event_score)
        factors.append(f"{source} source priority {source_priority_for(source)}")
        factors.append(f"{method} method evidence")
        evidence.append(evidence_event("source_trust", f"{source}={source_score}", field=fields[0], score_delta=0.0))
        evidence.append(evidence_event("method_reliability", f"{method}={method_score}", field=fields[0], score_delta=0.0))
    base = mean(scores) if scores else 0.34
    if stage == "fusion" and len({str(entry.get("source")).lower() for entry in entries}) > 1:
        base += config.modifier("cross_source_history")
        factors.append("cross-source field history")
    return base, list(dict.fromkeys(factors)), penalties, evidence


def _record_context(record, provenance, config, *, stage):
    lat = _coerce_float(record.get("latitude"))
    lon = _coerce_float(record.get("longitude"))
    warnings = _listify(record.get("extraction_warnings"))
    provenance = ensure_provenance(provenance, record_uuid=record.get("record_uuid"))
    has_provenance = bool(provenance.get("fields") or provenance.get("record_lineage") or provenance.get("conflicts"))
    return {
        "stage": stage,
        "source": str(record.get("source") or _source_from_provenance(provenance) or "unknown").lower(),
        "warnings": warnings,
        "warning_events": warning_events_from_values(warnings, config),
        "valid_coordinates": _valid_coordinates(lat, lon),
        "partial_coordinates": (lat is None) != (lon is None),
        "geometry_valid": _valid_coordinates(lat, lon) and record.get("geometry", True) is not None,
        "has_geology_match": _clean_text(record.get("geologic_unit")) is not None,
        "has_provenance": has_provenance,
        "conflicts": provenance.get("conflicts", []),
        "temporal": _temporal_metadata(record, provenance, config),
    }


def _warning_penalties(context, config, *, field=None):
    events = []
    temporal = context.get("temporal", {})
    if temporal.get("recency_weighting_enabled") and temporal.get("stale_source"):
        events.append(penalty_event("stale_source_data", severity="warning", field=field, amount=temporal.get("stale_penalty"), source="temporal_recency"))
    for event in context.get("warning_events", []):
        amount = config.severity_penalty(event.get("severity", "warning"))
        item = dict(event)
        item["field"] = field
        item["amount"] = amount
        events.append(item)
    return events


def _normalization_delta(provenance, field):
    delta = 0.0
    for event in _normalization_events(provenance, field):
        delta += _coerce_float(event.get("confidence_delta")) or 0.0
    return max(-0.25, min(delta, 0.15))


def _normalization_events(provenance, field):
    entry = ensure_provenance(provenance).get("fields", {}).get(field)
    if not isinstance(entry, dict):
        return []
    histories = entry.get("history") or [entry]
    events = []
    for event in histories:
        if isinstance(event, dict):
            events.extend(item for item in event.get("normalization_events", []) if isinstance(item, dict))
    return events


def _dedupe_events(events):
    deduped = []
    seen = set()
    for event in events or []:
        if not isinstance(event, dict):
            continue
        event_id = event.get("event_id") or repr(event)
        if event_id in seen:
            continue
        deduped.append(event)
        seen.add(event_id)
    return deduped


def _temporal_metadata(record, provenance, config):
    temporal_config = config.temporal or {}
    fields = temporal_config.get("source_timestamp_fields", [])
    timestamp = None
    for field in fields:
        value = record.get(field)
        if value not in (None, ""):
            timestamp = str(value)
            break
    provenance_timestamp = ensure_provenance(provenance).get("updated_at")
    return {
        "recency_weighting_enabled": bool(temporal_config.get("recency_weighting_enabled", False)),
        "source_timestamp": timestamp,
        "provenance_updated_at": provenance_timestamp,
        "stale_after_days": temporal_config.get("stale_after_days"),
        "stale_penalty": temporal_config.get("stale_penalty"),
        "stale_source": False,
    }


def _source_from_provenance(provenance):
    for entry in ensure_provenance(provenance).get("fields", {}).values():
        if isinstance(entry, dict) and entry.get("source"):
            return entry.get("source")
    return None


def _field_history_sources(provenance, field):
    entry = ensure_provenance(provenance).get("fields", {}).get(field)
    if not isinstance(entry, dict):
        return 0
    sources = {str(event.get("source")).lower() for event in entry.get("history", []) if isinstance(event, dict) and event.get("source")}
    if entry.get("source"):
        sources.add(str(entry.get("source")).lower())
    return len(sources)


def _has_conflict(provenance, fields):
    return any(isinstance(conflict, dict) and conflict.get("field") in fields for conflict in ensure_provenance(provenance).get("conflicts", []))


def _extract_scores(dataframe, column):
    if column not in dataframe.columns:
        return []
    scores = []
    for value in dataframe[column]:
        score = value.get("score") if isinstance(value, dict) else value
        number = _coerce_float(score)
        if number is not None:
            scores.append(number)
    return scores


def _series_distribution(scores):
    if not scores:
        return {"count": 0, "mean": None, "min": None, "max": None, "histogram": confidence_histogram([])}
    return {"count": len(scores), "mean": round(mean(scores), 4), "min": round(min(scores), 4), "max": round(max(scores), 4), "histogram": confidence_histogram(scores)}


def _count_records_with_warnings(dataframe):
    return int(sum(bool(_listify(value)) for value in dataframe.get("extraction_warnings", []))) if "extraction_warnings" in dataframe.columns else 0


def _count_missing_coordinates(dataframe):
    if not {"latitude", "longitude"}.issubset(dataframe.columns):
        return 0
    return int(sum(not _valid_coordinates(_coerce_float(row.latitude), _coerce_float(row.longitude)) for row in dataframe.itertuples()))


def _count_invalid_geometry(dataframe):
    if "geometry_confidence" in dataframe.columns:
        return int(sum((_coerce_float(value.get("score") if isinstance(value, dict) else value) or 0.0) < 0.25 for value in dataframe["geometry_confidence"]))
    return _count_missing_coordinates(dataframe)


def _count_records_with_conflicts(dataframe):
    if "provenance" not in dataframe.columns:
        return 0
    return int(sum(bool(ensure_provenance(value).get("conflicts")) for value in dataframe["provenance"]))


def _public_context(context):
    return {key: value for key, value in context.items() if key not in {"warning_events"}}


def _valid_coordinates(latitude, longitude):
    return latitude is not None and longitude is not None and -90 <= latitude <= 90 and -180 <= longitude <= 180


def _listify(value):
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
    if hasattr(value, "tolist"):
        converted = value.tolist()
        return converted if isinstance(converted, list) else [converted]
    return [value]


def _clean_text(value):
    if value is None:
        return None
    try:
        if value != value:
            return None
    except (TypeError, ValueError):
        return None
    text = str(value).strip()
    return text or None


def _clean_strings(values):
    return list(dict.fromkeys(str(value) for value in values or [] if str(value).strip()))


def _coerce_float(value):
    try:
        if value is None or value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value):
    number = _coerce_float(value)
    if number is None:
        number = 0.0
    return round(max(0.0, min(number, 1.0)), 4)


def _require_pandas():
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Confidence reporting requires pandas. Install requirements.txt first.") from exc
    return pd
