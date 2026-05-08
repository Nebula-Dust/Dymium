"""ETL modules for Dymium."""

from .models import MineralDeposit

__all__ = ["MineralDeposit", "build_unified_dataset", "process_mrds", "process_pdf"]


def __getattr__(name: str):
    if name == "process_mrds":
        from .ingest_mrds import process_mrds

        return process_mrds
    if name == "process_pdf":
        from .pdf_ingest import process_pdf

        return process_pdf
    if name == "build_unified_dataset":
        from .fusion import build_unified_dataset

        return build_unified_dataset
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
