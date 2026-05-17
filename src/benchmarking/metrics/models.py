"""Typed benchmark report models for Dymium ingestion observability."""

from __future__ import annotations

from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, Field

try:  # Pydantic v2
    from pydantic import ConfigDict
except ImportError:  # pragma: no cover
    ConfigDict = None  # type: ignore[assignment]

from src.etl.provenance import deterministic_uuid, utc_now

BENCHMARK_SCHEMA_VERSION = "dymium-ingestion-benchmark-v1"
Severity = Literal["info", "warning", "severe", "critical"]


class BenchmarkEvent(BaseModel):
    """Structured benchmark/validation event suitable for logs or metrics export."""

    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow")

    event_id: str = Field(default_factory=lambda: deterministic_uuid("benchmark-event", utc_now()))
    event_type: str
    severity: Severity = "info"
    stage: str | None = None
    message: str
    field: str | None = None
    record_id: str | None = None
    metric_name: str | None = None
    metric_value: float | int | str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utc_now)


class QualityMetric(BaseModel):
    """Named metric with optional threshold context."""

    name: str
    value: float | int | str | None
    unit: str | None = None
    threshold: float | int | None = None
    status: Literal["pass", "warn", "fail", "unknown"] = "unknown"
    details: dict[str, Any] = Field(default_factory=dict)


class StageBenchmark(BaseModel):
    """Benchmark output for one pipeline stage."""

    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow")

    stage_name: str
    input_records: int = 0
    output_records: int = 0
    duration_seconds: float | None = None
    throughput_records_per_second: float | None = None
    warning_count: int = 0
    failure_count: int = 0
    extraction_coverage: dict[str, Any] = Field(default_factory=dict)
    validation_results: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    events: list[BenchmarkEvent] = Field(default_factory=list)
    events_summary: dict[str, Any] = Field(default_factory=dict)
    started_at: str | None = None
    completed_at: str = Field(default_factory=utc_now)


class BenchmarkReport(BaseModel):
    """Whole-run benchmark report."""

    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow")

    benchmark_id: str = Field(default_factory=lambda: deterministic_uuid("benchmark-report", utc_now()))
    schema_version: str = BENCHMARK_SCHEMA_VERSION
    run_name: str = "adhoc"
    dataset_name: str | None = None
    pipeline_version: str | None = None
    created_at: str = Field(default_factory=utc_now)
    stages: list[StageBenchmark] = Field(default_factory=list)
    record_quality: dict[str, Any] = Field(default_factory=dict)
    geospatial_validation: dict[str, Any] = Field(default_factory=dict)
    provenance_integrity: dict[str, Any] = Field(default_factory=dict)
    confidence_summary: dict[str, Any] = Field(default_factory=dict)
    schema_drift: dict[str, Any] = Field(default_factory=dict)
    comparisons: dict[str, Any] = Field(default_factory=dict)
    events: list[BenchmarkEvent] = Field(default_factory=list)
    events_summary: dict[str, Any] = Field(default_factory=dict)

    def add_stage(self, stage: StageBenchmark) -> None:
        self.stages.append(stage)
        self.events.extend(stage.events)


def distribution(values: list[float | int]) -> dict[str, Any]:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return {"count": 0, "mean": None, "min": None, "max": None, "p50": None, "p90": None, "p95": None, "histogram": confidence_histogram([])}
    sorted_values = sorted(numeric)
    return {
        "count": len(sorted_values),
        "mean": round(mean(sorted_values), 4),
        "min": round(sorted_values[0], 4),
        "max": round(sorted_values[-1], 4),
        "p50": round(_percentile(sorted_values, 50), 4),
        "p90": round(_percentile(sorted_values, 90), 4),
        "p95": round(_percentile(sorted_values, 95), 4),
        "histogram": confidence_histogram(sorted_values),
    }


def confidence_histogram(values: list[float | int]) -> dict[str, int]:
    buckets = {"0.00-0.24": 0, "0.25-0.49": 0, "0.50-0.74": 0, "0.75-1.00": 0}
    for value in values:
        score = max(0.0, min(float(value), 1.0))
        if score < 0.25:
            buckets["0.00-0.24"] += 1
        elif score < 0.5:
            buckets["0.25-0.49"] += 1
        elif score < 0.75:
            buckets["0.50-0.74"] += 1
        else:
            buckets["0.75-1.00"] += 1
    return buckets


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * percentile / 100
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    weight = rank - lower
    return values[lower] * (1 - weight) + values[upper] * weight
