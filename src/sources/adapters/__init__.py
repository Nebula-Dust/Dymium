"""Source adapter registry."""
from .base import SourceAdapter
from .document import MineralsYearbookAdapter, OperatorFilingAdapter, PDFDocumentSourceAdapter
from .geospatial import NaturalEarthAdapter
from .tabular import GEOROCSourceAdapter, MRDSSourceAdapter, PetDBSourceAdapter
ADAPTERS = {
    "MRDS": MRDSSourceAdapter,
    "GEOROC": GEOROCSourceAdapter,
    "PetDB": PetDBSourceAdapter,
    "PETDB": PetDBSourceAdapter,
    "MineralsYearbook": MineralsYearbookAdapter,
    "OperatorFiling": OperatorFilingAdapter,
    "NaturalEarth": NaturalEarthAdapter,
    "PDFDocument": PDFDocumentSourceAdapter,
}
__all__ = ["ADAPTERS", "SourceAdapter", "MRDSSourceAdapter", "GEOROCSourceAdapter", "PetDBSourceAdapter", "MineralsYearbookAdapter", "OperatorFilingAdapter", "PDFDocumentSourceAdapter", "NaturalEarthAdapter"]
