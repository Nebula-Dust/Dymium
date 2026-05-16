"""Field-level provenance tests for Dymium ETL records."""

from __future__ import annotations

import unittest

from src.etl.fusion import merge_matched_records
from src.etl.ingest_mrds import load_mrds, normalize_mrds
from src.etl.pdf_ingest import merge_results, validate_deposits
from src.etl.provenance import deterministic_uuid


class FieldProvenanceTests(unittest.TestCase):
    def test_mrds_normalization_embeds_field_lineage(self) -> None:
        normalized = normalize_mrds(load_mrds("rdbms-tab/MRDS.txt").head(1))
        row = normalized.iloc[0]
        provenance = row.provenance

        self.assertEqual(row.record_uuid, deterministic_uuid("mrds", row.record_id))
        self.assertEqual(provenance["fields"]["commodities"]["source"], "MRDS")
        self.assertEqual(provenance["fields"]["commodities"]["method"], "commodity_normalization")
        self.assertIn("commodity_abbreviation_expansion", provenance["fields"]["commodities"]["transformations"])
        self.assertEqual(provenance["record_lineage"][-1]["step"], "mrds_normalization")

    def test_pdf_invalid_coordinate_provenance_records_decision(self) -> None:
        merged = merge_results(
            [
                [
                    {
                        "site_name": "Bad Coord Mine",
                        "latitude": 999,
                        "longitude": -200,
                        "commodities": ["Au"],
                        "_chunk_id": "chunk-0001",
                        "_page_numbers": [2],
                        "_source_text_sha1": "abc",
                    }
                ]
            ]
        )
        deposit = validate_deposits(merged, source_path="dummy.pdf")[0]
        record = deposit.model_dump() if hasattr(deposit, "model_dump") else deposit.dict()
        provenance = record["provenance"]

        self.assertIsNone(record["latitude"])
        self.assertEqual(provenance["fields"]["latitude"]["source"], "PDF")
        self.assertIn("invalid_latitude_nulled:999", provenance["fields"]["latitude"]["normalization_decisions"])
        self.assertEqual(provenance["fields"]["commodities"]["page"], 2)
        self.assertEqual(provenance["fields"]["commodities"]["chunk_ids"], ["chunk-0001"])

    def test_fusion_preserves_history_and_records_conflicts(self) -> None:
        mrds = normalize_mrds(load_mrds("rdbms-tab/MRDS.txt").head(1)).iloc[0].to_dict()
        pdf_deposit = validate_deposits(
            merge_results(
                [
                    [
                        {
                            "site_name": "Moonshine Prospect",
                            "latitude": 55.14445,
                            "longitude": -132.05371,
                            "commodities": ["Au"],
                            "_chunk_id": "chunk-0001",
                            "_page_numbers": [1],
                            "_source_text_sha1": "abc",
                        }
                    ]
                ]
            ),
            source_path="reports/example.pdf",
        )[0]
        pdf = pdf_deposit.model_dump() if hasattr(pdf_deposit, "model_dump") else pdf_deposit.dict()

        merged = merge_matched_records(mrds, pdf, match_metadata={"name_score": 100, "distance_km": 0})
        provenance = merged["provenance"]

        self.assertEqual(provenance["fields"]["commodities"]["source"], "FUSION")
        self.assertGreaterEqual(len(provenance["fields"]["commodities"]["history"]), 2)
        self.assertTrue(any(conflict["field"] == "commodities" for conflict in provenance["conflicts"]))
        self.assertEqual(provenance["record_lineage"][-1]["step"], "dataset_fusion")


if __name__ == "__main__":
    unittest.main()
