# Dymium
Open-source geospatial ETL infrastructure for transforming fragmented geological data into structured, ML-ready datasets.

Dymium ingests geological PDFs, mineral databases, shapefiles, and geospatial layers, then normalizes them into a unified schema and exports GeoParquet datasets for downstream machine learning, exploration analysis, and geoscience workflows.

## Why Dymium Exists

Geological data is abundant but operationally fragmented.

Mineral deposit information is spread across:
- scanned PDF reports,
- legacy tabular databases,
- inconsistent geospatial formats,
- jurisdiction-specific schemas,
- decades of unstructured technical documents.

This fragmentation creates a major bottleneck for machine learning, spatial analysis, and downstream exploration workflows.

Dymium focuses on solving the data standardization layer first:
- extract structured entities from geological documents,
- normalize schemas across sources,
- spatially enrich deposits with geological context,
- export interoperable GeoParquet datasets.

```text
PDF Reports ─┐
MRDS CSVs ───┼──► Ingestion & Parsing ─► Entity Extraction ─► Schema Normalization ─► GeoParquet
Shapefiles ──┘            │                        │
                           │                        └──► Spatial Enrichment
                           │
                           └──► Streamlit Visualization Layer
``` 
standardized format suitable for machine learning and analysis. Geological data is abundant but fragmented across formats, schemas, and decades of inconsistent reporting. Most of it is locked in PDFs, legacy databases, or incompatible geospatial files. Dymium focuses on solving this bottleneck by automating:
- Data extraction (OCR, tables, metadata)
- Entity recognition (lithology, commodity, grade, location)
- Schema normalization
- Cross-source data integration

The result is a clean, consistent baseline dataset that can be extended and refined for downstream modeling.

## Demo UI

### Pipeline Overview
![Pipeline Overview](docs/images/overview.png)

### Deposit Map
![Deposit Map](docs/images/deposit-map.png)

### Geology Enrichment
![Geology Enrichment](docs/images/geology-tab.png)


## Example Use Cases
- Rapid integration of historical geological datasets
- Preparing training data for mineral exploration models
- Standardizing datasets across multiple jurisdictions
- Reducing manual data wrangling in mining workflows
- Enabling downstream ML/AI pipelines


## Tech Stack
- Python 3.11+
- PyMuPDF (PDF parsing)
- OpenAI structured JSON extraction
- Pandas / GeoPandas / Shapely
- Pydantic (schema validation)
- GeoParquet / PyArrow
- Streamlit / PyDeck (demo UI)

## Getting Started
```bash
git clone https://github.com/<your-username>/Dymium.git
cd Dymium
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set `OPENAI_API_KEY` before running PDF extraction or full MRDS/PDF fusion.

## MRDS CSV Ingestion
Normalize the USGS MRDS tabular export and write a GeoParquet dataset:
```bash
python -m src.etl.ingest_mrds rdbms-tab/MRDS.txt --output out/mrds.parquet
```

The same module can be called from a pipeline or Lambda handler:
```python
from src.etl.ingest_mrds import process_mrds

process_mrds("/tmp/MRDS.txt", "/tmp/mrds.parquet")
```

## PDF Report Ingestion
Extract mineral deposit records from geological PDF reports with PyMuPDF and OpenAI structured JSON output:
```bash
export OPENAI_API_KEY=...
python -m src.etl.pdf_ingest --input reports/example.pdf
```

Programmatic use:
```python
from src.etl.pdf_ingest import process_pdf

deposits = process_pdf("/tmp/report.pdf")
```
## Unified Dataset Fusion
Merge normalized MRDS records with PDF-extracted deposits and export a single GeoParquet dataset:
```bash
python -m src.etl.fusion --csv rdbms-tab/MRDS.txt --pdf reports/example.pdf --output out/unified.parquet
```

Programmatic use:
```python
from src.etl.fusion import build_unified_dataset

unified = build_unified_dataset("rdbms-tab/MRDS.txt", "reports/example.pdf")
```

## Geology Enrichment
Enrich the unified dataset with SGMC-style geologic-unit context using spatial joins:

```bash
python -m src.etl.geology --input out/unified.parquet --shapefile data/sgmc.shp --output out/enriched.parquet
```

Programmatic use:
```python
from src.etl.geology import enrich_with_geology

enriched = enrich_with_geology("out/unified.parquet", "data/sgmc.shp")
```

## Streamlit Demo
Run the local technical demo for PDF extraction, MRDS/PDF fusion, geology enrichment, map exploration, filtering, and GeoParquet downloads.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m streamlit run app.py
```

The demo reads existing outputs such as `out/unified.parquet` and `out/enriched.parquet` when present, and can also trigger the pipeline from the sidebar. Set `OPENAI_API_KEY` before running PDF extraction or dataset fusion.

## Standardized Output

Dymium exports normalized geospatial datasets with fields such as:

- `site_name`
- `commodities`
- `latitude` / `longitude`
- `lithology`
- `geologic_age`
- `source_url`
- `confidence_score`
- `geometry`

Outputs are written to GeoParquet for interoperability with:

- GeoPandas
- DuckDB
- Spark
- QGIS
- modern geospatial lakehouse workflows

## Project Status

Dymium is currently an early-stage open-source prototype.

### Current Capabilities
- MRDS CSV ingestion and normalization
- PDF mineral deposit extraction
- Multi-source dataset fusion
- Spatial geology enrichment
- GeoParquet export
- Interactive Streamlit demo UI

### In Progress
- Improved extraction confidence scoring
- Expanded lithology normalization
- GeoPackage support
- Logging and observability
- Containerized deployment

## Roadmap
- Robust PDF + OCR pipeline
- Improved LLM-based structured extraction
- Broader geoscience schema normalization
- Performance benchmarking against manual workflows
- Cloud-ready deployment patterns


## Scope

Dymium focuses on demonstrating:
- Multi-source ingestion
- Structured extraction from unstructured data
- Baseline schema alignment

It does not attempt to fully solve:
- Domain-specific geological ontologies
- High-precision geometallurgical interpretation
- Production-grade data pipelines

**Design Philosophy**
By focusing on data standardization first, Dymium aims to unlock downstream applications in exploration, processing, and decision-making.
Contributions are welcome. 

Areas of interest:

  Geoscience schema design
  NLP for technical documents
  Geospatial data processing
  Data validation and QA pipelines


Apache 2.0 License

**Disclaimer**
This project is an experimental prototype intended for research and development purposes. Accuracy of extracted geological data may vary depending on source quality.

Contact
For collaboration or questions, open an issue or reach out.
