"""Confidence infrastructure tests for deterministic trust scoring."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.etl.confidence import (
    attach_record_confidence,
    calibration_diagnostics,
    confidence_drift_report,
    dependency_failure_summary,
    load_confidence_config,
    normalization_event,
    validation_report,
)
from src.etl.provenance import empty_provenance, field_event, set_field


class ConfidenceFrameworkTests(unittest.TestCase):
    def test_config_override_and_malformed_fallback(self) -> None:
        overridden = load_confidence_config(overrides={"source_trust": {"PDF": 0.2, "UNKNOWN": 0.1}})
        self.assertEqual(overridden.source_score("PDF"), 0.2)

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not valid json", encoding="utf-8")
            config = load_confidence_config(overrides=bad)
        self.assertTrue(config.errors)
        self.assertGreater(config.source_score("MRDS"), 0.0)

    def test_invalid_coordinates_cascade_to_geometry_and_record(self) -> None:
        record = attach_record_confidence(
            {
                "record_id": "bad-coords",
                "site_name": "Bad Coordinates",
                "latitude": None,
                "longitude": None,
                "commodities": ["gold"],
                "source": "pdf",
                "extraction_warnings": ["invalid_latitude:999", "invalid_longitude:-200"],
                "provenance": empty_provenance(record_uuid="bad-coords"),
            },
            stage="pdf_extraction",
        )

        self.assertLessEqual(record["coordinates_confidence"]["score"], 0.10)
        self.assertLessEqual(record["geometry_confidence"]["score"], 0.20)
        self.assertLessEqual(record["record_confidence"]["score"], 0.70)
        self.assertTrue(record["geometry_confidence"].get("inherited_penalties"))

    def test_ocr_degradation_is_structured_and_stage_capped(self) -> None:
        record = attach_record_confidence(
            {
                "record_id": "ocr-low",
                "site_name": "OCR Low",
                "latitude": 37.8,
                "longitude": -106.9,
                "commodities": ["silver"],
                "source": "pdf",
                "extraction_warnings": ["low_ocr_confidence:0.12"],
                "provenance": empty_provenance(record_uuid="ocr-low"),
            },
            stage="pdf_extraction",
        )

        self.assertTrue(record["stage_confidence"]["penalty_lineage"])
        severities = {event["severity"] for event in record["stage_confidence"]["penalty_lineage"]}
        self.assertIn("severe", severities)

    def test_structured_normalization_event_improves_explainability(self) -> None:
        provenance = empty_provenance(record_uuid="commodity-test")
        provenance = set_field(
            provenance,
            "commodities",
            field_event(
                "commodities",
                ["gold"],
                source="MRDS",
                method="commodity_normalization",
                confidence=1.0,
                normalization_events=[
                    normalization_event(
                        "commodity_alias_expansion",
                        source_value="AU",
                        normalized_value="gold",
                        ontology_version="dymium-commodity-v1",
                        confidence_delta=0.03,
                    )
                ],
            ),
        )
        record = attach_record_confidence(
            {
                "record_id": "commodity-test",
                "site_name": "Commodity Test",
                "latitude": 40.0,
                "longitude": -105.0,
                "commodities": ["gold"],
                "source": "mrds",
                "provenance": provenance,
            },
            stage="mrds_normalization",
        )

        self.assertIn("structured ontology normalization event applied", record["commodity_confidence"]["factors"])
        self.assertGreater(record["commodity_confidence"]["score"], 0.8)

    def test_reports_include_drift_dependency_and_calibration_diagnostics(self) -> None:
        high = attach_record_confidence(
            {
                "record_id": "high",
                "site_name": "High",
                "latitude": 40.0,
                "longitude": -105.0,
                "commodities": ["gold"],
                "source": "mrds",
                "provenance": empty_provenance(record_uuid="high"),
            },
            stage="mrds_normalization",
        )
        low = attach_record_confidence(
            {
                "record_id": "low",
                "site_name": "Low",
                "latitude": None,
                "longitude": None,
                "commodities": ["gold"],
                "source": "pdf",
                "provenance": empty_provenance(record_uuid="low"),
            },
            stage="pdf_extraction",
        )
        baseline = pd.DataFrame.from_records([high])
        current = pd.DataFrame.from_records([low])

        self.assertIn("record_confidence", validation_report(current, stage="test"))
        self.assertLess(confidence_drift_report(baseline, current)["mean_delta"], 0)
        self.assertIsInstance(dependency_failure_summary(current), dict)
        self.assertEqual(calibration_diagnostics(current)["calibration_status"], "identity_calibrator_only")

    def test_confidence_objects_are_geoparquet_safe(self) -> None:
        import tempfile

        import geopandas as gpd

        from src.etl.ingest_mrds import export_geoparquet, load_mrds, normalize_mrds

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            export_geoparquet(normalize_mrds(load_mrds("rdbms-tab/MRDS.txt").head(1)), tmp.name)
            reloaded = gpd.read_parquet(tmp.name)

        record_confidence = reloaded.iloc[0]["record_confidence"]
        self.assertIsInstance(record_confidence, dict)
        self.assertIn("score", record_confidence)

    def test_dependency_graph_references_known_confidence_fields(self) -> None:
        config = load_confidence_config()
        known = set(config.field_weights) | {"record_confidence", "stage_confidence"}
        for child, rules in config.dependencies.items():
            self.assertIn(child, known)
            for rule in rules:
                self.assertIn(rule["parent"], known)


if __name__ == "__main__":
    unittest.main()
