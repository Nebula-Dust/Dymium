# Dymium
AI pipeline for transforming messy geological data into structured, ML-ready geospatial datasets.

## Overview 
Dymium converts heterogeneous geological data—PDF reports, CSV datasets, and shapefiles—into a unified, standardized format suitable for machine learning and analysis. Geological data is abundant but fragmented across formats, schemas, and decades of inconsistent reporting. Most of it is locked in PDFs, legacy databases, or incompatible geospatial files. Dymium focuses on solving this bottleneck by automating:
- Data extraction (OCR, tables, metadata)
- Entity recognition (lithology, commodity, grade, location)
- Schema normalization
- Cross-source data integration

The result is a clean, consistent baseline dataset that can be extended and refined for downstream modeling.

## Architecture (Simplified)
Raw Data (PDFs / CSVs / Shapefiles)
↓
Ingestion & Parsing (Tika, pdfplumber, GDAL)
↓
Entity Extraction (LLM / NLP)
↓
Schema Mapping & Normalization
↓
Unified Dataset (GeoParquet)


## Example Use Cases
- Rapid integration of historical geological datasets
- Preparing training data for mineral exploration models
- Standardizing datasets across multiple jurisdictions
- Reducing manual data wrangling in mining workflows
- Enabling downstream ML/AI pipelines


## Tech Stack
- Python
- Apache Tika (PDF parsing)
- pdfplumber (table extraction)
- GDAL / GeoPandas (geospatial processing)
- LLM APIs or local models (entity extraction)
- Pydantic (schema validation)
- GeoParquet (output format)

## Getting Started 
1. Clone the repository **git clone https://github.com/<your-username>/Dymium.git ; cd Dymium**
2. Install dependencies
**pip install -r requirements.txt**
3. Run pipeline
**python run_pipeline.py --source data/sample_reports/**

## MRDS CSV Ingestion
Normalize the USGS MRDS tabular export and write a GeoParquet dataset:

**python -m src.etl.ingest_mrds rdbms-tab/MRDS.txt --output out/mrds.parquet**

The same module can be called from a pipeline or Lambda handler:

**from src.etl.ingest_mrds import process_mrds**

**process_mrds("/tmp/MRDS.txt", "/tmp/mrds.parquet")**

## PDF Report Ingestion
Extract mineral deposit records from geological PDF reports with PyMuPDF and OpenAI structured JSON output:

**export OPENAI_API_KEY=...**

**python -m src.etl.pdf_ingest --input reports/example.pdf**

Programmatic use:

**from src.etl.pdf_ingest import process_pdf**

**deposits = process_pdf("/tmp/report.pdf")**


**Project Status**
  Early-stage prototype

  _Current capabilities:_
    Basic PDF → structured data extraction
    CSV normalization
    Initial schema mapping

  _Planned_:
    Improved entity extraction accuracy
    Multi-source dataset joining
    Validation + confidence scoring
    Web interface / demo
    Expanded geoscience schema support


**Roadmap**
 Robust PDF + OCR pipeline
 LLM-based structured extraction
 Cross-source data integration
 Standardized geoscience schema
 Performance benchmarking (manual vs automated)
 Demo + visualization layer


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

_Areas of interest:_

  Geoscience schema design
  NLP for technical documents
  Geospatial data processing
  Data validation and QA pipelines


Apache 2.0 License

**Disclaimer**
This project is an experimental prototype intended for research and development purposes. Accuracy of extracted geological data may vary depending on source quality.

Contact
For collaboration or questions, open an issue or reach out.
