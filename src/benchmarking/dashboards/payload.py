"""Dashboard-ready benchmark payloads."""

from __future__ import annotations

from typing import Any

from src.benchmarking.reports import report_to_dict


def streamlit_dashboard_payload(report: dict[str, Any] | Any) -> dict[str, Any]:
    data = report_to_dict(report)
    stages = data.get("stages", [])
    return {
        "summary_cards": {
            "stages": len(stages),
            "warnings": sum(stage.get("warning_count", 0) for stage in stages),
            "failures": sum(stage.get("failure_count", 0) for stage in stages),
            "events": len(data.get("events", [])),
        },
        "stage_table": [
            {
                "stage": stage.get("stage_name"),
                "duration_seconds": stage.get("duration_seconds"),
                "throughput": stage.get("throughput_records_per_second"),
                "warnings": stage.get("warning_count"),
                "failures": stage.get("failure_count"),
            }
            for stage in stages
        ],
        "confidence": data.get("confidence_summary", {}),
        "geospatial": data.get("geospatial_validation", {}),
        "schema_drift": data.get("schema_drift", {}),
    }
