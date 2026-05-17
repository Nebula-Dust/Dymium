"""Structured logging and export hooks for benchmark events."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.benchmarking.metrics.models import BenchmarkEvent, model_to_dict

LOGGER = logging.getLogger("dymium.benchmarking")


def emit_event(event: BenchmarkEvent) -> None:
    """Emit a benchmark event as structured JSON through Python logging."""

    level = {
        "info": logging.INFO,
        "warning": logging.WARNING,
        "severe": logging.ERROR,
        "critical": logging.CRITICAL,
    }.get(event.severity, logging.INFO)
    LOGGER.log(level, json.dumps(model_to_dict(event), sort_keys=True, default=str))


def aggregate_events(events: list[BenchmarkEvent]) -> dict[str, Any]:
    severity_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    for event in events:
        severity_counts[event.severity] = severity_counts.get(event.severity, 0) + 1
        type_counts[event.event_type] = type_counts.get(event.event_type, 0) + 1
        if event.stage:
            stage_counts[event.stage] = stage_counts.get(event.stage, 0) + 1
    return {"severity_counts": severity_counts, "event_type_counts": type_counts, "stage_counts": stage_counts}


def export_events_jsonl(events: list[BenchmarkEvent], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(model_to_dict(event), sort_keys=True, default=str) + "\n")
    return path
