"""Sanity checks for a Dymium unified GeoParquet dataset.

Usage:
    python tests/sanity_check_unified.py out/unified.parquet
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import geopandas as gpd


REQUIRED_COLUMNS = {
    "record_id",
    "site_name",
    "latitude",
    "longitude",
    "commodities",
    "source",
    "confidence_score",
    "geometry",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a Dymium unified GeoParquet dataset.")
    parser.add_argument("path", nargs="?", default="out/unified.parquet", help="Path to unified GeoParquet output.")
    args = parser.parse_args()

    parquet_path = Path(args.path)
    df = gpd.read_parquet(parquet_path)

    print("path:", parquet_path)
    print("rows:", len(df))
    print("columns:", list(df.columns))
    print("head:")
    print(df.head())
    print()

    missing = sorted(REQUIRED_COLUMNS.difference(df.columns))
    print("missing_required_columns:", missing)
    print("source_counts:", df["source"].value_counts(dropna=False).to_dict() if "source" in df else {})

    has_latlon = df["latitude"].notna() & df["longitude"].notna() if {"latitude", "longitude"}.issubset(df.columns) else []
    print("rows_with_latlon:", int(has_latlon.sum()) if hasattr(has_latlon, "sum") else 0)
    print("rows_with_geometry:", int(df.geometry.notna().sum()) if "geometry" in df else 0)

    valid_lat = df["latitude"].between(-90, 90) if "latitude" in df else []
    valid_lon = df["longitude"].between(-180, 180) if "longitude" in df else []
    if hasattr(valid_lat, "sum") and hasattr(valid_lon, "sum"):
        coord_rows = df["latitude"].notna() & df["longitude"].notna()
        invalid_coords = coord_rows & ~(valid_lat & valid_lon)
        print("invalid_coordinate_rows:", int(invalid_coords.sum()))

    if "commodities" in df:
        commodity_is_list = df["commodities"].map(_is_list_like)
        print("commodity_list_like_rows:", int(commodity_is_list.sum()))
        print("commodity_non_list_like_rows:", int((~commodity_is_list).sum()))
        print("commodity_examples:", [_as_plain_list(value) for value in df.loc[df["commodities"].map(_has_items), "commodities"].head(10).tolist()])

    if {"site_name", "latitude", "longitude"}.issubset(df.columns):
        keys = df.apply(_dedupe_key, axis=1)
        duplicate_keys = keys.duplicated().sum()
        print("duplicate_site_coordinate_keys:", int(duplicate_keys))

    if "record_id" in df:
        print("duplicate_record_ids:", int(df["record_id"].duplicated().sum()))
    if "site_name" in df:
        print("missing_site_names:", int(df["site_name"].isna().sum()))

    return 0


def _is_list_like(value: Any) -> bool:
    return isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict))


def _has_items(value: Any) -> bool:
    try:
        return len(value) > 0
    except TypeError:
        return False


def _as_plain_list(value: Any) -> list[Any]:
    try:
        return list(value)
    except TypeError:
        return [value]


def _dedupe_key(row: Any) -> str:
    site_key = str(row.site_name or "").lower()
    site_key = re.sub(r"[^a-z0-9]+", "", site_key)
    if _has_coordinates(row.latitude, row.longitude):
        return f"{site_key}|{round(float(row.latitude), 4)}:{round(float(row.longitude), 4)}"
    return f"{site_key}|"


def _has_coordinates(latitude: Any, longitude: Any) -> bool:
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return False
    return lat == lat and lon == lon and -90 <= lat <= 90 and -180 <= lon <= 180


if __name__ == "__main__":
    raise SystemExit(main())
