"""Reliability tests for document and PDF ingestion."""

from __future__ import annotations

import unittest
from pathlib import Path

from src.etl.document_ingest import PageExtraction, chunk_pages, ingest_pdf_document
from src.etl.pdf_ingest import merge_results, validate_deposits


class DocumentIngestionReliabilityTests(unittest.TestCase):
    def test_missing_pdf_returns_structured_failure(self) -> None:
        result = ingest_pdf_document(Path("/private/tmp/dymium_missing_report.pdf"))

        self.assertEqual(result.document_type, "missing")
        self.assertEqual(result.page_count, 0)
        self.assertEqual(result.metrics["error_count"], 1)
        self.assertIn("file_not_found", result.errors[0])

    def test_chunks_preserve_page_provenance(self) -> None:
        pages = [
            PageExtraction(page_number=1, method="text", text="The Alpha mine contains copper. More text."),
            PageExtraction(page_number=2, method="text", text="The Beta prospect contains gold."),
        ]

        chunks = chunk_pages(pages, max_tokens=20)

        self.assertGreaterEqual(len(chunks), 1)
        self.assertEqual(chunks[0].page_start, 1)
        self.assertIn(1, chunks[0].page_numbers)
        self.assertTrue(chunks[0].chunk_id.startswith("chunk-0001"))

    def test_invalid_coordinates_are_retained_as_uncertainty(self) -> None:
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

        deposits = validate_deposits(merged, source_path="dummy.pdf")
        record = deposits[0].model_dump() if hasattr(deposits[0], "model_dump") else deposits[0].dict()

        self.assertIsNone(record["latitude"])
        self.assertIsNone(record["longitude"])
        self.assertEqual(record["commodities"], ["gold"])
        self.assertEqual(record["source_pages"], [2])
        self.assertIn("invalid_latitude:999.0", record["extraction_warnings"])
        self.assertIn("invalid_longitude:-200.0", record["extraction_warnings"])


if __name__ == "__main__":
    unittest.main()
