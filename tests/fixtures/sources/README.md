# Source Expansion Fixtures

Small deterministic fixtures for the `src.sources` ingestion architecture:

- `mrds_source.csv`: clean MRDS-style deposit rows.
- `georoc_source.csv`: GEOROC-style sample/locality row with source-specific terminology.
- `operator_conflict.csv`: future operator-filing conflict scaffold.
- `natural_earth_context.geojson`: geospatial context layer with CRS metadata.

Malformed/OCR PDF behavior is covered by `tests/fixtures/benchmark/corrupted_report.pdf` and `ocr_heavy_placeholder.pdf`.
