"""PetDB-style source adapter for petrological sample tables."""

from __future__ import annotations

from .base import SourceAdapter


class PetDBAdapter(SourceAdapter):
    dataset_name = "PetDB"
    adapter_version = "petdb-adapter-v1"
    source_schema_version = "petdb-tabular-sample"
    field_aliases = {
        "source_record_id": ("sample_id", "station_id", "specimen_id", "petdb_id", "id"),
        "site_name": ("location", "station_name", "site", "site_name", "sample_name"),
        "commodities": ("commodities", "elements", "element", "analytes"),
        "latitude": ("latitude", "lat", "decimal_latitude", "y"),
        "longitude": ("longitude", "lon", "long", "decimal_longitude", "x"),
        "crs": ("crs", "coordinate_system", "datum"),
        "lithology": ("material", "rock_type", "rock_name", "lithology"),
        "geologic_age": ("age", "geologic_age", "stratigraphic_age"),
        "units": ("unit", "units", "value_unit", "analytical_unit", "concentration_unit"),
        "source_url": ("url", "source_url", "doi", "reference"),
        "source_timestamp": ("publication_date", "published", "updated_at"),
    }
