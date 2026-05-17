"""High-level source ingestion orchestration."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from src.sources.adapters import ADAPTERS, PDFDocumentSourceAdapter, SourceAdapter
from src.sources.loaders import detect_source
from src.sources.metrics import source_coverage_metrics, source_reliability_profile
from src.sources.registries import SourceRegistry
from src.sources.schemas import SourceIngestionResult, SourceUpdateState
from src.sources.validators import validate_source_result
class SourceIngestionEngine:
    """Resolve source adapters and produce provenance-rich ingestion results."""
    def __init__(self, registry: SourceRegistry | None = None, adapters: dict[str, type[SourceAdapter]] | None = None) -> None:
        self.registry = registry or SourceRegistry()
        self.adapters = adapters or ADAPTERS
    def adapter_for(self, source: str | Path, *, source_name: str | None = None):
        if source_name:
            adapter_name = self.registry.adapter_name(source_name)
            adapter_cls = self.adapters.get(source_name) or self.adapters.get(adapter_name or "")
            if adapter_cls:
                return adapter_cls(self.registry)
        descriptor = detect_source(source, source_name=source_name, inspect_pdf=False)
        if descriptor.file_format == "pdf":
            return PDFDocumentSourceAdapter(self.registry)
        if descriptor.source_kind == "geospatial_layer":
            return self.adapters["NaturalEarth"](self.registry)
        if descriptor.source_kind == "structured_dataset":
            return self.adapters["MRDS"](self.registry)
        raise ValueError(f"No source adapter registered for {source_name or descriptor.file_format or descriptor.source_kind}")
    def ingest(self, source: str | Path, *, source_name: str | None = None, prior_state: SourceUpdateState | None = None, source_version: str | None = None) -> SourceIngestionResult:
        adapter = self.adapter_for(source, source_name=source_name)
        result = adapter.ingest(source, prior_state=prior_state, source_version=source_version)
        result.validation_issues.extend(validate_source_result(result))
        coverage = source_coverage_metrics(result)
        reliability = source_reliability_profile(result)
        result.metrics = {**result.metrics, "source_coverage": coverage, "source_reliability": reliability}
        return result
    def supported_sources(self) -> list[dict[str, Any]]:
        return [self.registry.get(name) for name in self.registry.source_names()]
