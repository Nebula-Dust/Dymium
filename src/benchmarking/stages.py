"""Pipeline-stage benchmark builders for Dymium ETL workflows."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from src.benchmarking.policy import load_benchmark_policy
from src.benchmarking.metrics import BenchmarkEvent, StageBenchmark, distribution
from src.benchmarking.validators import dataframe_record_quality, validate_geodataframe, validate_geospatial_records, validate_provenance_integrity, validate_spatial_enrichment


@contextmanager
def stage_timer(stage_name: str, *, input_records: int = 0) -> Iterator[dict[str, Any]]:
    state = {"stage_name": stage_name, "input_records": input_records, "started_at": _now(), "start": time.perf_counter()}
    try:
        yield state
    finally:
        state["duration_seconds"] = round(time.perf_counter() - state["start"], 6)


def stage_from_document_ingestion(result: Any, *, duration_seconds: float | None = None) -> StageBenchmark:
    metrics = dict(getattr(result, "metrics", {}) or {})
    page_count = int(metrics.get("page_count") or getattr(result, "page_count", 0) or 0)
    warning_count = int(metrics.get("warning_count") or len(getattr(result, "warnings", []) or []))
    failure_count = int(metrics.get("error_count") or len(getattr(result, "errors", []) or []))
    coverage = {
        "page_count": page_count,
        "text_pages": metrics.get("text_pages", 0),
        "text_coverage_percent": metrics.get("text_coverage_percent", 0.0),
        "raw_text_characters": metrics.get("raw_text_characters", 0),
        "chunk_count": metrics.get("chunk_count", 0),
        "table_count": metrics.get("table_count", 0),
        "pages_needing_ocr": metrics.get("pages_needing_ocr", 0),
        "failed_pages": metrics.get("failed_pages", 0),
    }
    events = []
    if getattr(result, "document_type", None) in {"missing", "malformed"}:
        events.append(BenchmarkEvent(event_type="document_ingestion_failure", severity="critical", stage="pdf_ingestion", message=f"Document type is {getattr(result, 'document_type', None)}."))
    if metrics.get("pages_needing_ocr", 0):
        events.append(BenchmarkEvent(event_type="ocr_routing", severity="warning", stage="ocr_extraction", message="One or more pages required OCR routing.", metric_value=metrics.get("pages_needing_ocr")))
    return _stage(
        "pdf_ingestion",
        input_records=1,
        output_records=page_count,
        duration_seconds=duration_seconds,
        warning_count=warning_count,
        failure_count=failure_count,
        extraction_coverage=coverage,
        metrics=metrics,
        events=events,
    )


def stage_from_ocr_extraction(result: Any, *, duration_seconds: float | None = None, thresholds: dict[str, Any] | None = None, config_path: str | None = None) -> StageBenchmark:
    """Build an OCR benchmark stage from a document ingestion result.

    OCR quality gates are loaded from config/benchmarking/thresholds.json so the
    subsystem can be recalibrated without changing stage logic.
    """

    metrics = dict(getattr(result, "metrics", {}) or {})
    policy = load_benchmark_policy(config_path, overrides={"thresholds": thresholds} if thresholds else None)
    low_confidence_threshold = policy.threshold("ocr.low_confidence")
    gibberish_threshold = policy.threshold("ocr.gibberish_confidence")
    pages = list(getattr(result, "pages", []) or [])
    ocr_pages = [page for page in pages if getattr(page, "ocr_attempted", False)]
    confidence_values = [float(getattr(page, "ocr_confidence")) for page in ocr_pages if getattr(page, "ocr_confidence", None) is not None]
    low_confidence_pages = [page for page in ocr_pages if getattr(page, "ocr_confidence", None) is not None and float(getattr(page, "ocr_confidence")) < low_confidence_threshold]
    gibberish_pages = [page for page in ocr_pages if getattr(page, "ocr_confidence", None) is not None and float(getattr(page, "ocr_confidence")) < gibberish_threshold]
    pages_needing_ocr = int(metrics.get("pages_needing_ocr") or len(ocr_pages))
    events: list[BenchmarkEvent] = []
    if pages_needing_ocr:
        events.append(BenchmarkEvent(event_type="ocr_pages_detected", severity="info", stage="ocr_extraction", message="One or more pages were routed through OCR.", metric_value=pages_needing_ocr, metadata={"configured_low_confidence_threshold": low_confidence_threshold}))
    if low_confidence_pages:
        events.append(BenchmarkEvent(event_type="low_ocr_confidence", severity="severe", stage="ocr_extraction", message="OCR pages fell below configured low-confidence threshold.", metric_value=len(low_confidence_pages), metadata={"threshold": low_confidence_threshold}))
    if gibberish_pages:
        events.append(BenchmarkEvent(event_type="ocr_gibberish_risk", severity="critical", stage="ocr_extraction", message="OCR pages fell below configured gibberish-risk threshold.", metric_value=len(gibberish_pages), metadata={"threshold": gibberish_threshold}))
    coverage = {
        "page_count": metrics.get("page_count", len(pages)),
        "pages_needing_ocr": pages_needing_ocr,
        "ocr_attempted_pages": len(ocr_pages),
        "low_ocr_confidence_pages": len(low_confidence_pages),
        "ocr_gibberish_risk_pages": len(gibberish_pages),
    }
    validation = {
        "low_ocr_confidence_threshold": low_confidence_threshold,
        "ocr_gibberish_confidence_threshold": gibberish_threshold,
        "low_ocr_confidence_page_count": len(low_confidence_pages),
        "ocr_gibberish_risk_page_count": len(gibberish_pages),
    }
    return _stage(
        "ocr_extraction",
        input_records=len(pages) or int(metrics.get("page_count") or 0),
        output_records=len(ocr_pages),
        duration_seconds=duration_seconds,
        warning_count=sum(1 for event in events if event.severity in {"warning", "severe"}),
        failure_count=sum(1 for event in events if event.severity == "critical"),
        extraction_coverage=coverage,
        validation_results=validation,
        confidence=distribution(confidence_values),
        metrics={"threshold_source": policy.source, "policy_warnings": list(policy.warnings)},
        events=events,
    )


def stage_from_spatial_enrichment(input_records: list[dict[str, Any]], enriched_records: list[dict[str, Any]], *, duration_seconds: float | None = None) -> StageBenchmark:
    validation = validate_spatial_enrichment(input_records, enriched_records, stage="spatial_enrichment")
    events = validation.pop("events", [])
    return _stage(
        "spatial_enrichment",
        input_records=len(input_records),
        output_records=len(enriched_records),
        duration_seconds=duration_seconds,
        warning_count=sum(1 for event in events if event.severity in {"warning", "severe"}),
        failure_count=sum(1 for event in events if event.severity == "critical"),
        extraction_coverage={"enrichment_coverage": validation.get("enrichment_coverage", 0.0)},
        validation_results=validation,
        events=events,
    )


def stage_from_dataframe(dataframe, *, stage_name: str, duration_seconds: float | None = None) -> StageBenchmark:
    quality = dataframe_record_quality(dataframe, stage=stage_name)
    geospatial = validate_geodataframe(dataframe, stage=stage_name) if hasattr(dataframe, "columns") else {}
    provenance = validate_provenance_integrity(dataframe.to_dict(orient="records"), stage=stage_name)
    events = [*quality.pop("events", []), *geospatial.pop("events", []), *provenance.pop("events", [])]
    warning_count = sum(1 for event in events if event.severity in {"warning", "severe"})
    failure_count = sum(1 for event in events if event.severity == "critical")
    return _stage(
        stage_name,
        input_records=len(dataframe),
        output_records=len(dataframe),
        duration_seconds=duration_seconds,
        warning_count=warning_count,
        failure_count=failure_count,
        validation_results={"record_quality": quality, "geospatial": geospatial, "provenance": provenance},
        confidence=quality.get("confidence_distribution", {}),
        metrics={"columns": list(dataframe.columns)},
        events=events,
    )


def stage_from_reconciliation_result(result: Any, *, duration_seconds: float | None = None) -> StageBenchmark:
    metrics = dict(getattr(result, "metrics", {}) or {})
    records = getattr(result, "records", []) or []
    events = []
    for record in records:
        for warning in getattr(record, "validation_warnings", []) or []:
            events.append(BenchmarkEvent(event_type="reconciliation_warning", severity="warning", stage="reconciliation", message=str(warning), record_id=getattr(record, "canonical_id", None)))
        for error in getattr(record, "validation_errors", []) or []:
            events.append(BenchmarkEvent(event_type="reconciliation_error", severity="critical", stage="reconciliation", message=str(error), record_id=getattr(record, "canonical_id", None)))
    return _stage(
        "reconciliation",
        input_records=sum(item.get("rows", 0) for item in getattr(result, "adapter_metrics", []) or []) or len(records),
        output_records=len(records),
        duration_seconds=duration_seconds,
        warning_count=sum(1 for event in events if event.severity == "warning"),
        failure_count=sum(1 for event in events if event.severity == "critical"),
        validation_results=metrics,
        confidence=metrics.get("confidence_distribution", {}),
        metrics={"adapter_metrics": getattr(result, "adapter_metrics", []) or []},
        events=events,
    )


def stage_from_confidence_report(report: dict[str, Any], *, stage_name: str = "confidence_scoring", duration_seconds: float | None = None) -> StageBenchmark:
    events = []
    validation = report.get("validation", {})
    if validation.get("invalid_geometry"):
        events.append(BenchmarkEvent(event_type="confidence_invalid_geometry", severity="critical", stage=stage_name, message="Confidence report contains invalid geometry records.", metric_value=validation.get("invalid_geometry")))
    dependency_failures = report.get("dependency_failures", {})
    for reason, count in dependency_failures.items():
        events.append(BenchmarkEvent(event_type="confidence_dependency_failure", severity="warning", stage=stage_name, message=str(reason), metric_value=count))
    return _stage(stage_name, output_records=int(report.get("total_records") or 0), duration_seconds=duration_seconds, warning_count=sum(1 for event in events if event.severity == "warning"), failure_count=sum(1 for event in events if event.severity == "critical"), confidence=report.get("record_confidence", {}), validation_results=report, events=events)


def stage_from_geoparquet_export(path: str, *, input_records: int, output_records: int | None = None, duration_seconds: float | None = None, warnings: list[str] | None = None) -> StageBenchmark:
    events = [BenchmarkEvent(event_type="geoparquet_export_warning", severity="warning", stage="geoparquet_export", message=warning) for warning in warnings or []]
    return _stage("geoparquet_export", input_records=input_records, output_records=output_records if output_records is not None else input_records, duration_seconds=duration_seconds, warning_count=len(events), failure_count=0, metrics={"output_path": path}, events=events)


def _stage(stage_name: str, *, input_records: int = 0, output_records: int = 0, duration_seconds: float | None = None, warning_count: int = 0, failure_count: int = 0, extraction_coverage: dict[str, Any] | None = None, validation_results: dict[str, Any] | None = None, confidence: dict[str, Any] | None = None, metrics: dict[str, Any] | None = None, events: list[BenchmarkEvent] | None = None) -> StageBenchmark:
    throughput = None
    if duration_seconds and duration_seconds > 0:
        throughput = round(output_records / duration_seconds, 4)
    return StageBenchmark(stage_name=stage_name, input_records=input_records, output_records=output_records, duration_seconds=duration_seconds, throughput_records_per_second=throughput, warning_count=warning_count, failure_count=failure_count, extraction_coverage=extraction_coverage or {}, validation_results=validation_results or {}, confidence=confidence or {}, metrics=metrics or {}, events=events or [])


def _now() -> str:
    from src.etl.provenance import utc_now

    return utc_now()
