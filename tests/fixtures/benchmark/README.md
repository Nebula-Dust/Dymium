# Dymium Benchmark Fixtures

Small deterministic fixtures for ingestion benchmarking tests:

- clean MRDS-style rows
- malformed coordinates
- duplicate deposits
- conflicting commodity labels
- incomplete metadata
- schema drift examples
- corrupted and OCR-heavy placeholder PDFs

The PDF placeholders are intentionally tiny regression fixtures. Tests may generate a valid clean PDF at runtime when PyMuPDF is available.
