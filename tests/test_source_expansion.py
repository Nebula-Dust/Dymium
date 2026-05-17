"""Tests for scalable source expansion infrastructure."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from src.sources import SourceIngestionEngine, SourceRegistry
from src.sources.loaders import detect_source
from src.sources.schemas import SourceUpdateState

FIXTURE_DIR = Path("tests/fixtures/sources")


class SourceExpansionTests(unittest.TestCase):
    def test_detects_tabular_schema_geometry_and_encoding(self) -> None:
        descriptor = detect_source(FIXTURE_DIR / "mrds_source.csv")

        self.assertEqual(descriptor.source_kind, "structured_dataset")
        self.assertEqual(descriptor.file_format, "csv")
        self.assertTrue(descriptor.geometry_presence)
        self.assertIn("dep_id", descriptor.schema_fields)
        self.assertEqual(descriptor.encoding, "utf-8")

    def test_detects_nested_archive_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "nested.zip"
            nested.write_bytes(b"not a real inner archive, just a named member")
            archive_path = Path(tmp) / "sources.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.write(FIXTURE_DIR / "mrds_source.csv", "mrds_source.csv")
                archive.write(FIXTURE_DIR / "natural_earth_context.geojson", "context.geojson")
                archive.write(nested, "nested.zip")

            descriptor = detect_source(archive_path)

        self.assertEqual(descriptor.source_kind, "archive")
        self.assertTrue(descriptor.nested_archive)
        self.assertTrue(descriptor.geometry_presence)
        self.assertEqual(len(descriptor.archive_members), 3)

    def test_mrds_source_adapter_preserves_raw_fields_and_canonical_mapping(self) -> None:
        result = SourceIngestionEngine().ingest(FIXTURE_DIR / "mrds_source.csv", source_name="MRDS")

        self.assertEqual(result.source_dataset, "MRDS")
        self.assertEqual(len(result.records), 2)
        self.assertEqual(len(result.canonical_records), 2)
        first = result.records[0]
        self.assertEqual(first.raw_fields["code_list"], "AU CU")
        self.assertEqual(first.provenance.source_dataset, "MRDS")
        self.assertIn("source_coverage", result.metrics)
        self.assertGreater(result.metrics["source_coverage"]["geographic_coverage_rate"], 0)

    def test_georoc_source_adapter_tracks_schema_semantics_without_mrds_assumptions(self) -> None:
        result = SourceIngestionEngine().ingest(FIXTURE_DIR / "georoc_source.csv", source_name="GEOROC")

        self.assertEqual(result.source_dataset, "GEOROC")
        self.assertEqual(result.records[0].raw_fields["rock_type"], "NdPr-bearing carbonatite")
        self.assertEqual(result.records[0].provenance.registry_metadata["source_name"], "GEOROC")
        self.assertEqual(result.metrics["source_coverage"]["canonical_mapping_rate"], 1.0)

    def test_document_adapter_surfaces_malformed_pdf_without_crashing(self) -> None:
        result = SourceIngestionEngine().ingest("tests/fixtures/benchmark/corrupted_report.pdf", source_name="MineralsYearbook")

        self.assertEqual(result.source_dataset, "MineralsYearbook")
        self.assertTrue(result.errors)
        self.assertGreaterEqual(result.metrics["validation_issue_count"], 1)
        self.assertEqual(result.records[0].raw_fields["document_type"], "malformed")

    def test_geospatial_adapter_preserves_context_layer_metadata(self) -> None:
        result = SourceIngestionEngine().ingest(FIXTURE_DIR / "natural_earth_context.geojson", source_name="NaturalEarth")

        self.assertEqual(result.source_dataset, "NaturalEarth")
        self.assertEqual(len(result.records), 1)
        self.assertTrue(result.records[0].geometry_metadata["valid"])
        self.assertTrue(result.metrics["enrichment_context"])

    def test_incremental_ingestion_skips_unchanged_source(self) -> None:
        engine = SourceIngestionEngine()
        first = engine.ingest(FIXTURE_DIR / "mrds_source.csv", source_name="MRDS")
        state = SourceUpdateState(**first.state)
        second = engine.ingest(FIXTURE_DIR / "mrds_source.csv", source_name="MRDS", prior_state=state)

        self.assertTrue(second.metrics["unchanged"])
        self.assertEqual(second.records, [])
        self.assertIn("source_unchanged_skipped_incremental_ingestion", second.warnings)

    def test_registry_exposes_source_specific_rules(self) -> None:
        registry = SourceRegistry()

        self.assertIn("MRDS", registry.source_names())
        self.assertEqual(registry.adapter_name("OperatorFiling"), "OperatorFilingAdapter")
        self.assertEqual(registry.reconciliation_rules("NaturalEarth")["geometry_role"], "context_enrichment_not_deposit")


if __name__ == "__main__":
    unittest.main()
