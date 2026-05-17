"""High-level benchmark suite orchestration."""

from __future__ import annotations

from typing import Any

from src.benchmarking.drift import compare_benchmark_reports
from src.benchmarking.metrics import BenchmarkReport, StageBenchmark, model_to_dict
from src.benchmarking.observability import aggregate_events
from src.benchmarking.stages import stage_from_confidence_report, stage_from_dataframe, stage_from_document_ingestion, stage_from_geoparquet_export, stage_from_ocr_extraction, stage_from_reconciliation_result, stage_from_spatial_enrichment
from src.benchmarking.validators import dataframe_record_quality, validate_geodataframe, validate_geospatial_records, validate_provenance_integrity
from src.etl.confidence import validation_report as confidence_validation_report


class BenchmarkSuite:
    """Composable benchmark runner for Dymium ETL artifacts."""

    def __init__(self, *, run_name: str = "adhoc", dataset_name: str | None = None, pipeline_version: str | None = None) -> None:
        self.report = BenchmarkReport(run_name=run_name, dataset_name=dataset_name, pipeline_version=pipeline_version)

    def add_stage(self, stage: StageBenchmark) -> StageBenchmark:
        self.report.add_stage(stage)
        return stage

    def add_document_ingestion(self, result: Any, *, duration_seconds: float | None = None) -> StageBenchmark:
        return self.add_stage(stage_from_document_ingestion(result, duration_seconds=duration_seconds))

    def add_ocr_extraction(self, result: Any, *, duration_seconds: float | None = None, thresholds: dict[str, Any] | None = None, config_path: str | None = None) -> StageBenchmark:
        return self.add_stage(stage_from_ocr_extraction(result, duration_seconds=duration_seconds, thresholds=thresholds, config_path=config_path))

    def add_spatial_enrichment(self, input_records: list[dict[str, Any]], enriched_records: list[dict[str, Any]], *, duration_seconds: float | None = None) -> StageBenchmark:
        return self.add_stage(stage_from_spatial_enrichment(input_records, enriched_records, duration_seconds=duration_seconds))

    def add_dataframe_stage(self, dataframe, *, stage_name: str, duration_seconds: float | None = None) -> StageBenchmark:
        stage = stage_from_dataframe(dataframe, stage_name=stage_name, duration_seconds=duration_seconds)
        self.add_stage(stage)
        self.report.record_quality = dataframe_record_quality(dataframe, stage=stage_name)
        try:
            self.report.geospatial_validation = validate_geodataframe(dataframe, stage=stage_name)
        except Exception:
            self.report.geospatial_validation = validate_geospatial_records(dataframe.to_dict(orient="records"), stage=stage_name)
        self.report.provenance_integrity = validate_provenance_integrity(dataframe.to_dict(orient="records"), stage=stage_name)
        return stage

    def add_reconciliation(self, result: Any, *, duration_seconds: float | None = None) -> StageBenchmark:
        stage = stage_from_reconciliation_result(result, duration_seconds=duration_seconds)
        self.add_stage(stage)
        self.report.record_quality = result.metrics
        self.report.confidence_summary = result.metrics.get("confidence_distribution", {})
        return stage

    def add_confidence_dataframe(self, dataframe, *, stage_name: str = "confidence_scoring", duration_seconds: float | None = None) -> StageBenchmark:
        report = confidence_validation_report(dataframe, stage=stage_name)
        stage = stage_from_confidence_report(report, stage_name=stage_name, duration_seconds=duration_seconds)
        self.add_stage(stage)
        self.report.confidence_summary = report
        return stage

    def add_geoparquet_export(self, path: str, *, input_records: int, output_records: int | None = None, duration_seconds: float | None = None, warnings: list[str] | None = None) -> StageBenchmark:
        return self.add_stage(stage_from_geoparquet_export(path, input_records=input_records, output_records=output_records, duration_seconds=duration_seconds, warnings=warnings))

    def finalize(self, *, baseline: BenchmarkReport | dict[str, Any] | None = None) -> BenchmarkReport:
        self.report.events = [event for stage in self.report.stages for event in stage.events]
        self.report.comparisons = compare_benchmark_reports(_as_dict(baseline), model_to_dict(self.report)) if baseline is not None else {}
        self.report.events_summary = aggregate_events(self.report.events)  # type: ignore[attr-defined]
        return self.report


def _as_dict(value: BenchmarkReport | dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {"stages": []}
    return model_to_dict(value) if isinstance(value, BenchmarkReport) else value
