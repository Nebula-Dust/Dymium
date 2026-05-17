"""Standard source adapter interface for Dymium source expansion."""
from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from src.sources.loaders import detect_source
from src.sources.registries import SourceRegistry
from src.sources.schemas import SourceDescriptor, SourceIngestionResult, SourceUpdateState
class SourceAdapter(ABC):
    source_name = "UNKNOWN"
    adapter_version = "source-adapter-v1"
    supported_formats: tuple[str, ...] = ()
    canonical_mapping = False
    def __init__(self, registry: SourceRegistry | None = None) -> None:
        self.registry = registry or SourceRegistry()
    def inspect(self, source: str | Path, *, inspect_pdf: bool = True) -> SourceDescriptor:
        descriptor = detect_source(source, source_name=self.source_name, inspect_pdf=inspect_pdf)
        return descriptor
    @abstractmethod
    def ingest(self, source: str | Path, *, prior_state: SourceUpdateState | None = None, source_version: str | None = None) -> SourceIngestionResult:
        """Ingest a source while preserving source-native semantics."""
    def can_handle(self, descriptor: SourceDescriptor) -> bool:
        return not self.supported_formats or descriptor.file_format in self.supported_formats
    @property
    def registry_metadata(self) -> dict[str, Any]:
        return self.registry.get(self.source_name)
