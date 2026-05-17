"""Document source adapters for semi-structured geological reports and filings."""
from __future__ import annotations
from pathlib import Path
from src.etl.document_ingest import ingest_pdf_document
from src.etl.provenance import deterministic_uuid
from src.sources.adapters.base import SourceAdapter
from src.sources.provenance import build_source_provenance, make_ingestion_id
from src.sources.schemas import SourceIngestionResult, SourceRecord, SourceUpdateState, SourceValidationIssue
class PDFDocumentSourceAdapter(SourceAdapter):
    source_name = "PDFDocument"
    adapter_version = "pdf-document-source-adapter-v1"
    supported_formats = ("pdf",)
    extraction_method = "pdf_document_ingestion"
    source_kind = "semi_structured_report"
    def ingest(self, source: str | Path, *, prior_state: SourceUpdateState | None = None, source_version: str | None = None) -> SourceIngestionResult:
        descriptor = self.inspect(source, inspect_pdf=True)
        ingestion_id = make_ingestion_id(self.source_name, descriptor, source_version=source_version)
        if prior_state and prior_state.checksum_sha256 == descriptor.checksum_sha256:
            return SourceIngestionResult(ingestion_id=ingestion_id, source_dataset=self.source_name, descriptor=descriptor, metrics={"unchanged": True}, state={"checksum_sha256": descriptor.checksum_sha256}, warnings=["source_unchanged_skipped_incremental_ingestion"])
        document = ingest_pdf_document(source, enable_ocr=True)
        issues = [SourceValidationIssue(severity="critical", code="document_error", message=error) for error in document.errors]
        issues.extend(SourceValidationIssue(severity="warning", code="document_warning", message=warning) for warning in document.warnings)
        provenance = build_source_provenance(source_dataset=self.source_name, descriptor=descriptor, ingestion_id=ingestion_id, extraction_method=self.extraction_method, adapter_name=self.__class__.__name__, adapter_version=self.adapter_version, source_schema_version=self.registry_metadata.get("schema_version"), registry_metadata=self.registry_metadata)
        raw_fields = {"document_type": document.document_type, "page_count": document.page_count, "metrics": document.metrics, "warnings": document.warnings, "errors": document.errors, "chunk_ids": [chunk.chunk_id for chunk in document.chunks], "table_count": len(document.tables)}
        record = SourceRecord(record_id=deterministic_uuid("source-document", self.source_name, descriptor.checksum_sha256), source_dataset=self.source_name, source_kind=self.source_kind, raw_fields=raw_fields, source_terms={"document_type": document.document_type, "pages_needing_ocr": document.metrics.get("pages_needing_ocr", 0)}, provenance=provenance, validation_issues=issues, confidence_hints={"source_trust": self.registry.trust_level(self.source_name), "text_coverage_percent": document.metrics.get("text_coverage_percent"), "ocr_dependency": document.metrics.get("pages_needing_ocr", 0)})
        metrics = {"source_dataset": self.source_name, "document_type": document.document_type, "page_count": document.page_count, "chunk_count": len(document.chunks), "table_count": len(document.tables), "pages_needing_ocr": document.metrics.get("pages_needing_ocr", 0), "text_coverage_percent": document.metrics.get("text_coverage_percent", 0.0), "validation_issue_count": len(issues), "extraction_reliability": _document_reliability(document.metrics, len(document.errors))}
        return SourceIngestionResult(ingestion_id=ingestion_id, source_dataset=self.source_name, descriptor=descriptor, records=[record], validation_issues=issues, metrics=metrics, state={"source_dataset": self.source_name, "source_file": descriptor.path, "checksum_sha256": descriptor.checksum_sha256, "source_version": source_version, "ingested_record_ids": [record.record_id]}, warnings=[*document.warnings, *descriptor.schema_warnings], errors=document.errors)
class MineralsYearbookAdapter(PDFDocumentSourceAdapter):
    source_name = "MineralsYearbook"
    adapter_version = "minerals-yearbook-source-adapter-v1"
    extraction_method = "minerals_yearbook_pdf_ingestion"
class OperatorFilingAdapter(PDFDocumentSourceAdapter):
    source_name = "OperatorFiling"
    adapter_version = "operator-filing-source-adapter-v1"
    extraction_method = "operator_filing_pdf_ocr_aware_ingestion"
    source_kind = "operator_filing"
def _document_reliability(metrics: dict, error_count: int) -> float:
    score = 0.85
    score -= min(float(metrics.get("pages_needing_ocr", 0)) * 0.05, 0.3)
    score -= min(error_count * 0.15, 0.45)
    coverage = metrics.get("text_coverage_percent")
    if coverage is not None:
        score += (float(coverage) - 80.0) / 500.0
    return round(max(0.0, min(score, 1.0)), 4)
