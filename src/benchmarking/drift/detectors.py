"""Drift and degradation detection for benchmark reports."""

from __future__ import annotations

from typing import Any

from src.benchmarking.policy import load_benchmark_policy


def compare_benchmark_reports(baseline: dict[str, Any], current: dict[str, Any], *, thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    policy = load_benchmark_policy(overrides={"thresholds": {"drift": thresholds}} if thresholds else None)
    thresholds = policy.section("drift")
    baseline_stages = {stage.get("stage_name"): stage for stage in baseline.get("stages", [])}
    current_stages = {stage.get("stage_name"): stage for stage in current.get("stages", [])}
    stage_names = sorted(set(baseline_stages) | set(current_stages))
    stage_comparisons = {}
    degradations = []
    for stage_name in stage_names:
        left = baseline_stages.get(stage_name, {})
        right = current_stages.get(stage_name, {})
        comparison = _compare_stage(left, right)
        stage_comparisons[stage_name] = comparison
        if comparison.get("warning_rate_delta", 0) > thresholds["warning_rate_delta"]:
            degradations.append({"stage": stage_name, "type": "warning_rate_increase", "delta": comparison["warning_rate_delta"]})
        if comparison.get("confidence_mean_delta") is not None and comparison["confidence_mean_delta"] < thresholds["confidence_mean_delta"]:
            degradations.append({"stage": stage_name, "type": "confidence_decrease", "delta": comparison["confidence_mean_delta"]})
    return {
        "stage_comparisons": stage_comparisons,
        "schema_drift": compare_schema_fields(baseline, current),
        "coordinate_anomaly_delta": _metric_delta(baseline, current, ["geospatial_validation", "invalid_coordinate_pair_rate"]),
        "degradations": degradations,
    }


def compare_schema_fields(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    baseline_fields = set(_collect_columns(baseline))
    current_fields = set(_collect_columns(current))
    return {"new_fields": sorted(current_fields - baseline_fields), "missing_fields": sorted(baseline_fields - current_fields), "field_count_delta": len(current_fields) - len(baseline_fields)}


def extraction_degradation(baseline_stage: dict[str, Any], current_stage: dict[str, Any], *, thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    policy = load_benchmark_policy(overrides={"thresholds": {"drift": thresholds}} if thresholds else None)
    thresholds = policy.section("drift")
    baseline_coverage = baseline_stage.get("extraction_coverage", {})
    current_coverage = current_stage.get("extraction_coverage", {})
    text_delta = _num(current_coverage.get("text_coverage_percent")) - _num(baseline_coverage.get("text_coverage_percent"))
    ocr_delta = _num(current_coverage.get("pages_needing_ocr")) - _num(baseline_coverage.get("pages_needing_ocr"))
    failed_delta = _num(current_coverage.get("failed_pages")) - _num(baseline_coverage.get("failed_pages"))
    degraded = (
        text_delta < thresholds["text_coverage_percent_delta"]
        or ocr_delta > thresholds["pages_needing_ocr_delta"]
        or failed_delta > thresholds["failed_pages_delta"]
    )
    return {"text_coverage_percent_delta": round(text_delta, 4), "pages_needing_ocr_delta": ocr_delta, "failed_pages_delta": failed_delta, "degraded": degraded, "thresholds": thresholds}


def _compare_stage(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_total = max(_num(left.get("output_records")), 1)
    right_total = max(_num(right.get("output_records")), 1)
    warning_rate_delta = _num(right.get("warning_count")) / right_total - _num(left.get("warning_count")) / left_total
    failure_rate_delta = _num(right.get("failure_count")) / right_total - _num(left.get("failure_count")) / left_total
    left_confidence = left.get("confidence", {})
    right_confidence = right.get("confidence", {})
    confidence_delta = None
    if left_confidence.get("mean") is not None and right_confidence.get("mean") is not None:
        confidence_delta = round(float(right_confidence["mean"]) - float(left_confidence["mean"]), 4)
    return {"warning_rate_delta": round(warning_rate_delta, 4), "failure_rate_delta": round(failure_rate_delta, 4), "confidence_mean_delta": confidence_delta, "throughput_delta": _nullable_delta(right.get("throughput_records_per_second"), left.get("throughput_records_per_second"))}


def _metric_delta(baseline: dict[str, Any], current: dict[str, Any], path: list[str]) -> float | None:
    left = _nested(baseline, path)
    right = _nested(current, path)
    if left is None or right is None:
        return None
    return round(_num(right) - _num(left), 4)


def _nested(value: dict[str, Any], path: list[str]) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _collect_columns(report: dict[str, Any]) -> list[str]:
    columns: list[str] = []
    for stage in report.get("stages", []):
        stage_columns = stage.get("metrics", {}).get("columns", [])
        columns.extend(str(column) for column in stage_columns)
    return columns


def _nullable_delta(right: Any, left: Any) -> float | None:
    if right is None or left is None:
        return None
    return round(_num(right) - _num(left), 4)


def _num(value: Any) -> float:
    try:
        if value is None or value != value:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
