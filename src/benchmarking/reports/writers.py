"""JSON and markdown benchmark report writers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.benchmarking.metrics import BenchmarkReport, model_to_dict
from src.benchmarking.observability import aggregate_events


def report_to_dict(report: BenchmarkReport | dict[str, Any]) -> dict[str, Any]:
    return model_to_dict(report) if isinstance(report, BenchmarkReport) else report


def write_json_report(report: BenchmarkReport | dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report_to_dict(report), indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


def markdown_summary(report: BenchmarkReport | dict[str, Any]) -> str:
    data = report_to_dict(report)
    lines = ["# Dymium Ingestion Benchmark Report", ""]
    lines.append(f"Run: **{data.get('run_name', 'adhoc')}**")
    lines.append(f"Created: `{data.get('created_at')}`")
    if data.get("dataset_name"):
        lines.append(f"Dataset: `{data.get('dataset_name')}`")
    lines.append("")
    lines.append("## Stage Summary")
    lines.append("| Stage | Inputs | Outputs | Duration s | Throughput/s | Warnings | Failures |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for stage in data.get("stages", []):
        lines.append(
            f"| `{stage.get('stage_name')}` | {stage.get('input_records', 0)} | {stage.get('output_records', 0)} | {stage.get('duration_seconds')} | {stage.get('throughput_records_per_second')} | {stage.get('warning_count', 0)} | {stage.get('failure_count', 0)} |"
        )
    lines.append("")
    lines.append("## Record Quality")
    _append_dict(lines, data.get("record_quality", {}), max_depth=1)
    lines.append("")
    lines.append("## Geospatial Validation")
    _append_dict(lines, data.get("geospatial_validation", {}), max_depth=1)
    lines.append("")
    lines.append("## Confidence Summary")
    _append_dict(lines, data.get("confidence_summary", {}), max_depth=2)
    lines.append("")
    lines.append("## Provenance Integrity")
    _append_dict(lines, data.get("provenance_integrity", {}), max_depth=1)
    lines.append("")
    lines.append("## Schema Drift")
    _append_dict(lines, data.get("schema_drift", {}), max_depth=1)
    lines.append("")
    lines.append("## Event Summary")
    events = data.get("events", [])
    event_summary = aggregate_events_from_dicts(events)
    _append_dict(lines, event_summary, max_depth=2)
    return "\n".join(lines) + "\n"


def write_markdown_report(report: BenchmarkReport | dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown_summary(report), encoding="utf-8")
    return path


def aggregate_events_from_dicts(events: list[dict[str, Any]]) -> dict[str, Any]:
    severity_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for event in events:
        severity = str(event.get("severity", "info"))
        event_type = str(event.get("event_type", "unknown"))
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        type_counts[event_type] = type_counts.get(event_type, 0) + 1
    return {"severity_counts": severity_counts, "event_type_counts": type_counts}


def _append_dict(lines: list[str], value: dict[str, Any], *, max_depth: int, depth: int = 0) -> None:
    if not value:
        lines.append("No metrics reported.")
        return
    for key, item in value.items():
        if key == "events":
            continue
        prefix = "  " * depth + "-"
        if isinstance(item, dict) and depth < max_depth:
            lines.append(f"{prefix} `{key}`:")
            _append_dict(lines, item, max_depth=max_depth, depth=depth + 1)
        else:
            lines.append(f"{prefix} `{key}`: {item}")
