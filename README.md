# Dymium

Open-source geospatial ETL infrastructure for transforming fragmented geological data into structured, ML-ready datasets.

Dymium ingests geological PDFs, mineral databases, shapefiles, and geospatial layers, then normalizes them into unified GeoParquet datasets for downstream machine learning, exploration analysis, and geoscience workflows.

## Why Dymium Exists

Geological data is abundant but operationally fragmented. Mineral deposit information is spread across scanned PDF reports, legacy tabular databases, inconsistent geospatial formats, jurisdiction-specific schemas, and decades of unstructured technical documents.

Dymium focuses on the data standardization layer first:

- extract structured entities from geological documents
- normalize schemas across sources
- preserve provenance and uncertainty
- spatially enrich deposits with geological context
- export interoperable GeoParquet datasets

```text
PDF Reports ─┐
MRDS CSVs ───┼──► Ingestion & Parsing ─► Entity Extraction ─► Schema Normalization ─► GeoParquet
Shapefiles ──┘            │                        │
                           │                        └──► Spatial Enrichment
                           │
                           └──► Streamlit Visualization Layer
```

## Demo UI

### Pipeline Overview
![Pipeline Overview](docs/images/overview.png)

### Deposit Map
![Deposit Map](docs/images/deposit-map.png)

### Geology Enrichment
![Geology Enrichment](docs/images/geology-tab.png)

## Getting Started

```bash
git clone https://github.com/<your-username>/Dymium.git
cd Dymium
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set `OPENAI_API_KEY` before running PDF extraction or full MRDS/PDF fusion.

## Core ETL Commands

Normalize MRDS tabular data:

```bash
python -m src.etl.ingest_mrds rdbms-tab/MRDS.txt --output out/mrds.parquet
```

Extract deposits from a geological PDF:

```bash
python -m src.etl.pdf_ingest --input reports/example.pdf
```

Fuse MRDS and PDF-derived records:

```bash
python -m src.etl.fusion --csv rdbms-tab/MRDS.txt --pdf reports/example.pdf --output out/unified.parquet
```

Enrich deposits with geologic polygons:

```bash
python -m src.etl.geology --input out/unified.parquet --shapefile data/sgmc.shp --output out/enriched.parquet
```

Run the Streamlit demo:

```bash
python -m streamlit run app.py
```

## Standardized Output

Dymium exports normalized geospatial datasets with fields such as:

- `site_name`
- `commodities`
- `latitude` / `longitude`
- `lithology`
- `geologic_age`
- `source_url`
- `confidence_score`
- `record_uuid`
- `provenance`
- `geometry`

Outputs are written to GeoParquet for interoperability with GeoPandas, DuckDB, Spark, QGIS, and modern geospatial lakehouse workflows.

## Field-Level Provenance

Dymium embeds provenance directly into records rather than relying only on logs. Field-level lineage can retain source file origin, source field, extraction method, page/chunk provenance for PDFs, transformation history, normalization decisions, confidence, source priority, and resolved conflicts.

When MRDS and PDF records are fused, stable MRDS identifiers and coordinates generally supersede PDF-derived values, PDF can fill report-derived values such as grade or tonnage, and conflicts remain visible in structured provenance.

## Confidence Architecture

Dymium includes a deterministic confidence subsystem under `src/etl/confidence/`. The legacy `confidence_score` field remains for compatibility, while richer outputs can include field-level, record-level, and stage-level confidence objects.

Confidence heuristics are loaded from JSON config files in `config/confidence/`:

- `source_trust.json`
- `method_reliability.json`
- `field_weights.json`
- `modifiers.json`
- `stage_modifiers.json`
- `penalties.json`
- `dependencies.json`
- `gates.json`
- `thresholds.json`
- `temporal.json`

The confidence layer is conservative. Missing coordinates, invalid geometry, low OCR quality, unresolved conflicts, and missing provenance lower or cap trust rather than being hidden.

## Canonical Schema Reconciliation

Dymium now includes an early production-oriented schema reconciliation layer under `src/reconciliation/`. This layer is separate from the existing ETL modules: adapters convert source-native tables into canonical geological records while preserving original source semantics.

```text
Raw source dataset
    -> Source adapter layer
    -> Canonical geological schema
    -> Validation + reconciliation
    -> GeoParquet export + metrics
```

### Supported Initial Sources

The first adapter set is intentionally narrow:

- `MRDSAdapter` for USGS MRDS-style tabular records
- `GEOROCAdapter` for GEOROC-style geochemical sample/locality tables
- `PetDBAdapter` for PetDB-style petrological sample tables

GEOROC and PetDB are treated as geochemical or petrological context sources, not as perfect mineral deposit datasets. Missing deposit-specific fields are preserved as uncertainty rather than filled.

### Canonical Record Shape

Canonical records use strongly typed Pydantic models in `src/reconciliation/canonical_schema.py`. Each record includes:

- site or sample identity
- normalized commodities
- coordinates and CRS/geometry validation
- lithology and geologic age
- measurement units when present
- source references and timestamps
- dataset origin
- raw source fields
- field-level reconciliation metadata
- confidence and validation status
- duplicate/conflict annotations

Source-native values are not overwritten. A reconciled commodity field keeps both raw and normalized semantics:

```json
{
  "raw_field": "code_list",
  "raw_value": "AU CU",
  "normalized_values": ["gold", "copper"],
  "mapping_method": "commodity_ontology_alias_mapping_v1",
  "mapping_confidence": 0.98,
  "provenance": {
    "source_dataset": "MRDS",
    "source_field": "code_list",
    "transformation_method": "commodity_ontology_alias_mapping_v1",
    "reconciliation_version": "dymium-schema-reconciliation-v1"
  }
}
```

### Ontology Mapping Examples

The initial ontology config lives in `config/reconciliation/ontology.json`. It supports exact alias mapping, conservative fuzzy fallback, and raw-value preservation when no mapping is available.

| Raw value | Canonical value | Method |
| --- | --- | --- |
| `AU` | `gold` | commodity alias mapping |
| `REE` | `rare earth elements` | commodity alias mapping |
| `silvr` | `silver` | fuzzy ontology mapping with lower confidence |
| `NdPr-bearing carbonatite` | `carbonatite` + `carbonatite-related REE system` | lithology/deposit-model mapping |
| `wt%; ppm` | `percent`, `ppm` | unit alias mapping |

Unmapped lithologies, units, or commodity labels are preserved and flagged instead of being discarded.

### Schema Drift Handling

Adapters tolerate partial, renamed, and extra source schemas. They report missing expected field groups, unmapped source fields, compatibility warnings, invalid or missing coordinates, missing CRS assumptions, and low-confidence fuzzy mappings.

Invalid rows are not silently dropped. They remain in the canonical output with `validation_status`, `validation_warnings`, and `validation_errors`.

### Entity Reconciliation

`ReconciliationEngine` detects likely duplicate entities using deposit/locality name similarity and nearby coordinates. It does not aggressively merge records. Candidate duplicates receive:

- `duplicate_group_id`
- `duplicate_candidates`
- conflict records for differing commodities, lithology, or geologic age
- confidence penalties when reconciliation conflicts are present

This keeps cross-source uncertainty visible for later adjudication.

### Reconciliation Metrics

`src/reconciliation/metrics.py` generates JSON-ready metrics and markdown summaries for matched fields, low-confidence fields, unmapped fields, schema coverage, invalid geometry counts, duplicate groups, ontology conflicts, validation warnings, and confidence distributions.

Example usage:

```python
from src.reconciliation.adapters import MRDSAdapter
from src.reconciliation.metrics import generate_reconciliation_metrics, metrics_to_markdown
from src.reconciliation.reconciliation_engine import ReconciliationEngine

adapter = MRDSAdapter()
source = adapter.read_source("rdbms-tab/MRDS.txt").head(100)
adapted = adapter.adapt_dataframe(source, source_file="rdbms-tab/MRDS.txt")
result = ReconciliationEngine().reconcile_adapter_results([adapted])

metrics = generate_reconciliation_metrics(result.records)
print(metrics_to_markdown(metrics))
ReconciliationEngine().export_geoparquet(result.records, "out/canonical_reconciled.parquet")
```

### Current Limitations

This is a baseline reconciliation layer, not a complete geoscience ontology. Current assumptions:

- coordinates are normalized only when already decimal-degree compatible
- unsupported CRS values are flagged, not reprojected
- GEOROC/PetDB adapters are generic tabular adapters and may need source-specific column tuning
- unit normalization is limited to common assay, age, and resource units
- duplicate detection is conservative and does not perform authoritative entity resolution
- conflicting records are annotated, not adjudicated
- ontology mappings are intentionally small and should be expanded with domain review

## Project Status

Dymium is currently an early-stage open-source prototype.

### Current Capabilities

- MRDS CSV ingestion and normalization
- PDF mineral deposit extraction
- Multi-source dataset fusion
- Spatial geology enrichment
- GeoParquet export
- Interactive Streamlit demo UI
- Field-level provenance metadata
- Configurable confidence scoring and validation reports
- Canonical schema reconciliation for MRDS/GEOROC/PetDB-style tables

### In Progress

- Confidence calibration benchmarks
- Expanded reconciliation ontology coverage
- Expanded lithology normalization
- GeoPackage support
- Logging and observability
- Containerized deployment

## Scope

Dymium focuses on demonstrating multi-source ingestion, structured extraction from unstructured data, baseline schema alignment, provenance preservation, and geospatial interoperability.

It does not attempt to fully solve global geological ontologies, high-precision geometallurgical interpretation, authoritative entity resolution, or production-grade orchestration.

## License

Apache 2.0 License

## Disclaimer

This project is an experimental prototype intended for research and development purposes. Accuracy of extracted and reconciled geological data may vary depending on source quality.
