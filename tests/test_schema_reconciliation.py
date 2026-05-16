"""Tests for canonical geological schema reconciliation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.reconciliation.adapters import GEOROCAdapter, MRDSAdapter, PetDBAdapter
from src.reconciliation.metrics import generate_reconciliation_metrics, metrics_to_markdown
from src.reconciliation.ontology import OntologyMapper
from src.reconciliation.reconciliation_engine import ReconciliationEngine


class SchemaReconciliationTests(unittest.TestCase):
    def test_mrds_adapter_preserves_raw_and_normalizes_schema(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "dep_id": "100",
                    "name": "Moonshine Prospect",
                    "code_list": "AU CU",
                    "latitude": "55.14445",
                    "longitude": "-132.05371",
                    "url": "https://example.test/mrds/100",
                    "extra_native_field": "source-only",
                }
            ]
        )
        result = MRDSAdapter().adapt_dataframe(df, source_file="MRDS.txt")
        record = result.records[0]

        self.assertEqual(record.source_dataset, "MRDS")
        self.assertEqual(record.normalized_commodities, ["gold", "copper"])
        self.assertEqual(record.raw_fields["code_list"], "AU CU")
        self.assertIn("extra_native_field", record.unmapped_fields)
        self.assertEqual(record.reconciled_fields["commodities"].raw_field, "code_list")
        self.assertEqual(record.reconciled_fields["commodities"].provenance.source_dataset, "MRDS")
        self.assertGreater(record.confidence_score, 0.65)

    def test_ontology_mapping_handles_alias_and_fuzzy_values(self) -> None:
        mapper = OntologyMapper()
        commodities = mapper.map_commodities("REE AU")
        fuzzy = mapper.map_commodities("silvr")
        lithology = mapper.map_lithology("NdPr-bearing carbonatite")
        units = mapper.map_units("wt%; ppm")

        self.assertIn("rare earth elements", commodities.normalized_values)
        self.assertIn("gold", commodities.normalized_values)
        self.assertEqual(fuzzy.normalized_values, ["silver"])
        self.assertIn("fuzzy", fuzzy.method)
        self.assertEqual(lithology.normalized_value, "carbonatite")
        self.assertEqual(lithology.metadata["deposit_model"], "carbonatite-related REE system")
        self.assertEqual(units.normalized_values, ["percent", "ppm"])

    def test_invalid_coordinates_are_flagged_not_dropped(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "sample_id": "G1",
                    "location": "Bad Coordinate Locality",
                    "elements": "REE",
                    "latitude": "999",
                    "longitude": "-200",
                    "rock_type": "carbonatite",
                }
            ]
        )
        result = GEOROCAdapter().adapt_dataframe(df)
        record = result.records[0]

        self.assertEqual(len(result.records), 1)
        self.assertEqual(record.validation_status, "warning")
        self.assertFalse(record.geometry.valid)
        self.assertIsNone(record.latitude)
        self.assertIn("invalid_latitude:999.0", record.validation_warnings)
        self.assertLessEqual(record.confidence_score, 0.62)

    def test_partial_schema_tolerates_missing_fields_and_reports_drift(self) -> None:
        df = pd.DataFrame([{"sample_id": "P1", "material": "basalt", "extra": "kept"}])
        result = PetDBAdapter().adapt_dataframe(df)
        record = result.records[0]

        self.assertIn("missing_field_group:site_name", record.schema_drift_warnings)
        self.assertIn("extra", record.unmapped_fields)
        self.assertIn("missing_field_group:site_name", record.validation_warnings)
        self.assertEqual(record.validation_status, "warning")
        self.assertEqual(record.raw_fields["extra"], "kept")

    def test_reconciliation_detects_duplicates_and_conflicts_without_merging(self) -> None:
        mrds = MRDSAdapter().adapt_dataframe(
            pd.DataFrame(
                [
                    {
                        "dep_id": "M1",
                        "name": "Bayan Obo",
                        "code_list": "REE NB",
                        "latitude": "41.8",
                        "longitude": "109.9",
                    }
                ]
            )
        )
        georoc = GEOROCAdapter().adapt_dataframe(
            pd.DataFrame(
                [
                    {
                        "sample_id": "G1",
                        "location": "Bayan Obo",
                        "elements": "Fe",
                        "latitude": "41.801",
                        "longitude": "109.901",
                        "rock_type": "carbonatite",
                    }
                ]
            )
        )
        result = ReconciliationEngine().reconcile_adapter_results([mrds, georoc])

        self.assertEqual(len(result.records), 2)
        self.assertTrue(all(record.duplicate_group_id for record in result.records))
        self.assertTrue(any(conflict["field"] == "commodities" for record in result.records for conflict in record.conflicts))
        self.assertEqual(result.metrics["duplicate_group_count"], 1)
        self.assertGreater(result.metrics["ontology_conflicts"], 0)

    def test_metrics_and_markdown_summary_are_generated(self) -> None:
        result = MRDSAdapter().adapt_dataframe(
            pd.DataFrame(
                [
                    {
                        "dep_id": "100",
                        "name": "Moonshine Prospect",
                        "code_list": "AU CU",
                        "latitude": "55.14445",
                        "longitude": "-132.05371",
                    }
                ]
            )
        )
        metrics = generate_reconciliation_metrics(result.records)
        markdown = metrics_to_markdown(metrics)

        self.assertEqual(metrics["source_counts"], {"MRDS": 1})
        self.assertIn("confidence_distribution", metrics)
        self.assertIn("Schema Reconciliation Metrics", markdown)

    def test_geoparquet_export_preserves_canonical_rows(self) -> None:
        engine = ReconciliationEngine()
        result = MRDSAdapter().adapt_dataframe(
            pd.DataFrame(
                [
                    {
                        "dep_id": "100",
                        "name": "Moonshine Prospect",
                        "code_list": "AU CU",
                        "latitude": "55.14445",
                        "longitude": "-132.05371",
                    }
                ]
            )
        )
        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            engine.export_geoparquet(result.records, tmp.name)
            import geopandas as gpd

            reloaded = gpd.read_parquet(tmp.name)

        self.assertEqual(reloaded.iloc[0]["source_dataset"], "MRDS")
        self.assertIn("raw_fields_json", reloaded.columns)
        self.assertEqual(len(reloaded), 1)

    def test_real_mrds_head_can_adapt(self) -> None:
        path = Path("rdbms-tab/MRDS.txt")
        if not path.exists():
            self.skipTest("MRDS source file is not present in this checkout")
        adapter = MRDSAdapter()
        result = adapter.adapt_dataframe(adapter.read_source(path).head(2), source_file=str(path))

        self.assertEqual(len(result.records), 2)
        self.assertTrue(all(record.raw_fields for record in result.records))
        self.assertTrue(any(record.normalized_commodities for record in result.records))


if __name__ == "__main__":
    unittest.main()
