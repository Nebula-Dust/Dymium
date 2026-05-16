"""ETL modules for Dymium."""

from .models import MineralDeposit

__all__ = ["MineralDeposit", "build_unified_dataset", "enrich_with_geology", "ingest_pdf_document", "process_mrds", "process_pdf", "process_pdf_with_report"]


def __getattr__(name: str):
    if name == "process_mrds":
        from .ingest_mrds import process_mrds

        return process_mrds
    if name == "ingest_pdf_document":
        from .document_ingest import ingest_pdf_document

        return ingest_pdf_document
    if name == "process_pdf":
        from .pdf_ingest import process_pdf

        return process_pdf
    if name == "process_pdf_with_report":
        from .pdf_ingest import process_pdf_with_report

        return process_pdf_with_report
    if name == "build_unified_dataset":
        from .fusion import build_unified_dataset

        return build_unified_dataset
    if name == "enrich_with_geology":
        from .geology import enrich_with_geology

        return enrich_with_geology
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
