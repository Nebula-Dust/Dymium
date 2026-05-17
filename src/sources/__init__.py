"""Scalable multi-source ingestion infrastructure for Dymium."""
from .ingestion import SourceIngestionEngine
from .metrics import source_coverage_metrics, source_reliability_profile
from .registries import SourceRegistry
from .schemas import SourceDescriptor, SourceIngestionResult, SourceRecord, SourceUpdateState, SourceValidationIssue
__all__ = ["SourceIngestionEngine", "SourceRegistry", "SourceDescriptor", "SourceIngestionResult", "SourceRecord", "SourceUpdateState", "SourceValidationIssue", "source_coverage_metrics", "source_reliability_profile"]
