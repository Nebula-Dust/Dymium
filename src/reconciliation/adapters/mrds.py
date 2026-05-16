"""MRDS source adapter."""

from __future__ import annotations

from .base import SourceAdapter


class MRDSAdapter(SourceAdapter):
    dataset_name = "MRDS"
    adapter_version = "mrds-adapter-v1"
    source_schema_version = "mrds-rdbms-tab"
    field_aliases = {
        "source_record_id": ("dep_id", "record_id", "site_id", "id"),
        "site_name": ("name", "site_name", "deposit_name", "mine_name"),
        "commodities": ("code_list", "commodities", "commodity_codes", "commod", "commod1"),
        "latitude": ("latitude", "lat", "y"),
        "longitude": ("longitude", "lon", "long", "x"),
        "crs": ("crs", "srs", "coordinate_system"),
        "lithology": ("lithology", "rock_type", "host_rock", "ore_body"),
        "geologic_age": ("age", "geologic_age", "era", "period"),
        "units": ("unit", "units", "value_unit", "analytical_unit", "concentration_unit"),
        "source_url": ("url", "source_url", "mrds_url"),
        "source_timestamp": ("updated_at", "modified", "record_date"),
    }
