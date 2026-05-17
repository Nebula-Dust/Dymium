"""Tests for Dymium ingestion benchmarking and validation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.benchmarking.benchmarks import create_benchmark_fixtures
from src.benchmarking.config import load_benchmark_thresholds
from src.benchmarking.drift import compare_benchmark_reports, extraction_degradation
from src.benchmarking.dashboards import streamlit_dashboard_payload
from src.benchmarking.reports import markdown_summary, report_to_dict, write_json_report, write_markdown_report
from src.benchmarking.suite import BenchmarkSuite
from src.benchmarking.validators import record_quality_metrics, validate_geospatial_records, validate_provenance_integrity, validate_spatial_enrichment
from src.etl.document_ingest import ingest_pdf_document
from src.reconciliation.adapters import MRDSAdapter
from src.reconciliation.reconciliation_engine import ReconciliationEngine


class BenchmarkingSubsystemTests(unittest.TestCase):
    def test_corrupted_pdf_stage_reports_failure_without_crashing(self) -> None:
        result = ingest_pdf_document("tests/fixtures/benchmark/corrupted_report.pdf", enable_ocr=False)
        suite = BenchmarkSuite(run_name="corrupted-pdf")
        stage = suite.add_document_ingestion(result)
        report = suite.finalize()

        self.assertIn(result.document_type, {"malformed", "empty"})
        self.assertGreaterEqual(stage.failure_count, 1)
        self.assertTrue(any(event.event_type == "document_ingestion_failure" for event in report.events))

    def test_record_quality_surfaces_missing_fields_duplicates_and_confidence(self) -> None:
        records = [
            {"record_id": "1", "site_name": "Dup", "commodities": ["gold"], "latitude": 40.0, "longitude": -105.0, "confidence_score": 0.8},
            {"record_id": "2", "site_name": "Dup", "commodities": [], "latitude": 40.0, "longitude": -105.0, "confidence_score": 0.3},
        ]
        metrics = record_quality_metrics(records)

        self.assertEqual(metrics["duplicate_records"], 2)
        self.assertEqual(metrics["missing_field_counts"]["source_url"], 2)
        self.assertEqual(metrics["confidence_distribution"]["count"], 2)
        self.assertTrue(metrics["events"])

    def test_geospatial_validator_catches_invalid_crs_geometry_and_impossible_join(self) -> None:
        records = [
            {"record_id": "bad", "latitude": 999, "longitude": -200, "crs": "EPSG:3857", "geologic_unit": "Unit A"},
            {"record_id": "dup1", "latitude": 40.0, "longitude": -105.0},
            {"record_id": "dup2", "latitude": 40.0, "longitude": -105.0},
        ]
        metrics = validate_geospatial_records(records)

        self.assertEqual(metrics["invalid_coordinate_pair_count"], 1)
        self.assertEqual(metrics["crs_failure_count"], 1)
        self.assertEqual(metrics["duplicate_geometry_records"], 2)
        self.assertEqual(metrics["impossible_spatial_join_count"], 1)

    def test_provenance_integrity_catches_missing_lineage_and_bad_confidence(self) -> None:
        records = [{"record_id": "p1", "confidence_score": 1.5}]
        metrics = validate_provenance_integrity(records)

        self.assertEqual(metrics["missing_provenance_count"], 1)
        self.assertEqual(metrics["malformed_confidence_metadata_count"], 1)
        self.assertTrue(metrics["events"])

    def test_reconciliation_benchmark_metrics_and_reports_are_exportable(self) -> None:
        df = pd.read_csv("tests/fixtures/benchmark/mrds_clean.csv")
        adapted = MRDSAdapter().adapt_dataframe(df, source_file="tests/fixtures/benchmark/mrds_clean.csv")
        reconciliation = ReconciliationEngine().reconcile_adapter_results([adapted])
        suite = BenchmarkSuite(run_name="reconciliation", dataset_name="benchmark-fixture")
        suite.add_reconciliation(reconciliation)
        report = suite.finalize()
        payload = streamlit_dashboard_payload(report)

        self.assertEqual(report.stages[0].stage_name, "reconciliation")
        self.assertEqual(payload["summary_cards"]["stages"], 1)
        self.assertIn("Stage Summary", markdown_summary(report))
        with tempfile.TemporaryDirectory() as tmp:
            json_path = write_json_report(report, Path(tmp) / "report.json")
            md_path = write_markdown_report(report, Path(tmp) / "report.md")
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())

    def test_benchmark_suite_dataframe_stage_integrates_quality_geospatial_and_confidence(self) -> None:
        df = pd.DataFrame(
            [
                {"record_id": "good", "site_name": "Good", "commodities": ["gold"], "latitude": 40.0, "longitude": -105.0, "source_url": "source", "confidence_score": 0.9},
                {"record_id": "bad", "site_name": None, "commodities": [], "latitude": 999.0, "longitude": -200.0, "confidence_score": 0.1},
            ]
        )
        suite = BenchmarkSuite(run_name="dataframe")
        stage = suite.add_dataframe_stage(df, stage_name="schema_normalization")
        report = suite.finalize()

        self.assertEqual(stage.output_records, 2)
        self.assertGreaterEqual(stage.failure_count, 1)
        self.assertIn("missing_field_rates", report.record_quality)
        self.assertIn("invalid_coordinate_pair_count", report.geospatial_validation)

    def test_drift_detection_surfaces_confidence_and_schema_degradation(self) -> None:
        baseline = {
            "stages": [
                {"stage_name": "schema_normalization", "output_records": 10, "warning_count": 1, "failure_count": 0, "confidence": {"mean": 0.9}, "metrics": {"columns": ["a", "b"]}},
            ]
        }
        current = {
            "stages": [
                {"stage_name": "schema_normalization", "output_records": 10, "warning_count": 4, "failure_count": 1, "confidence": {"mean": 0.7}, "metrics": {"columns": ["a", "c"]}},
            ]
        }
        comparison = compare_benchmark_reports(baseline, current)

        self.assertTrue(comparison["degradations"])
        self.assertEqual(comparison["schema_drift"]["new_fields"], ["c"])
        self.assertEqual(comparison["schema_drift"]["missing_fields"], ["b"])

    def test_extraction_degradation_detects_ocr_and_text_coverage_regression(self) -> None:
        degraded = extraction_degradation(
            {"extraction_coverage": {"text_coverage_percent": 95, "pages_needing_ocr": 0, "failed_pages": 0}},
            {"extraction_coverage": {"text_coverage_percent": 80, "pages_needing_ocr": 2, "failed_pages": 1}},
        )

        self.assertTrue(degraded["degraded"])
        self.assertEqual(degraded["pages_needing_ocr_delta"], 2)

    def test_spatial_enrichment_validation_flags_impossible_outputs(self) -> None:
        metrics = validate_spatial_enrichment(
            [{"record_id": "a", "latitude": None, "longitude": None}],
            [{"record_id": "a", "latitude": None, "longitude": None, "geologic_unit": "Unit"}],
        )

        self.assertEqual(metrics["impossible_spatial_join_count"], 1)
        self.assertTrue(metrics["events"])

    def test_ocr_benchmark_uses_configured_thresholds(self) -> None:
        class Page:
            def __init__(self, confidence: float | None, attempted: bool = True) -> None:
                self.ocr_confidence = confidence
                self.ocr_attempted = attempted

        class Result:
            metrics = {"page_count": 3, "pages_needing_ocr": 2}
            pages = [Page(0.2), Page(0.5), Page(None, attempted=False)]

        suite = BenchmarkSuite(run_name="ocr")
        stage = suite.add_ocr_extraction(Result())
        override_stage = suite.add_ocr_extraction(Result(), thresholds={"ocr": {"low_confidence": 0.1}})

        self.assertEqual(stage.stage_name, "ocr_extraction")
        self.assertEqual(stage.validation_results["low_ocr_confidence_threshold"], 0.35)
        self.assertEqual(stage.validation_results["low_ocr_confidence_page_count"], 1)
        self.assertEqual(override_stage.validation_results["low_ocr_confidence_threshold"], 0.1)
        self.assertEqual(override_stage.validation_results["low_ocr_confidence_page_count"], 0)

    def test_benchmark_config_malformed_file_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad_config = Path(tmp) / "thresholds.json"
            bad_config.write_text("{not-json", encoding="utf-8")
            thresholds = load_benchmark_thresholds(bad_config)

        self.assertEqual(thresholds["ocr"]["low_confidence"], 0.35)

    def test_fixture_generation_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = create_benchmark_fixtures(tmp)
            self.assertIn("mrds_clean.csv", paths)
            self.assertIn("corrupted_report.pdf", paths)
            self.assertTrue(Path(paths["mrds_clean.csv"]).read_text(encoding="utf-8").startswith("dep_id"))


if __name__ == "__main__":
    unittest.main()
