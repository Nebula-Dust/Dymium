"""Dataset-specific reconciliation adapters."""

from .base import AdapterResult, SourceAdapter
from .georoc import GEOROCAdapter
from .mrds import MRDSAdapter
from .petdb import PetDBAdapter

ADAPTERS = {
    "MRDS": MRDSAdapter,
    "GEOROC": GEOROCAdapter,
    "PetDB": PetDBAdapter,
    "PETDB": PetDBAdapter,
}

__all__ = ["ADAPTERS", "AdapterResult", "GEOROCAdapter", "MRDSAdapter", "PetDBAdapter", "SourceAdapter"]
