"""GEOROC-style source adapter for geochemical sample/locality tables."""

from __future__ import annotations

from .base import SourceAdapter


class GEOROCAdapter(SourceAdapter):
    dataset_name = "GEOROC"
    adapter_version = "georoc-adapter-v1"
    source_schema_version = "georoc-tabular-sample"
    field_aliases = {
        "source_record_id": ("sample_id", "sample name", "sample_name", "georoc_id", "id"),
        "site_name": ("location", "locality", "volcano", "site_name", "sample_name"),
        "commodities": ("commodities", "elements", "element", "analytes"),
        "latitude": ("latitude", "lat", "latitude_decimal", "y"),
        "longitude": ("longitude", "lon", "long", "longitude_decimal", "x"),
        "crs": ("crs", "coordinate_system", "datum"),
        "lithology": ("rock_type", "rock name", "rock_name", "material", "lithology"),
        "geologic_age": ("age", "geologic_age", "eruption_age", "stratigraphic_age"),
        "units": ("unit", "units", "value_unit", "analytical_unit", "concentration_unit"),
        "source_url": ("url", "source_url", "doi", "reference"),
        "source_timestamp": ("publication_date", "published", "updated_at"),
    }
