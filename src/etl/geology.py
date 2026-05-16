"""Geologic context enrichment for unified mineral deposit datasets.

Example:
    python -m src.etl.geology --input out/unified.parquet --shapefile data/sgmc.shp --output out/enriched.parquet
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from .provenance import append_lineage, deterministic_uuid, ensure_provenance, field_event, set_field

LOGGER = logging.getLogger(__name__)
TARGET_CRS = "EPSG:4326"

UNIT_ALIASES = (
    "UNIT_NAME",
    "unit_name",
    "UNITNAME",
    "UNIT_NAM",
    "unitname",
    "NAME",
    "name",
    "UNIT",
    "unit",
    "MAP_UNIT",
    "map_unit",
)
DESCRIPTION_ALIASES = (
    "DESCRIPTION",
    "description",
    "DESC",
    "desc",
    "UNIT_DESC",
    "unit_desc",
)
AGE_ALIASES = (
    "AGE",
    "age",
    "PERIOD",
    "period",
    "ERA",
    "era",
    "EON",
    "eon",
    "GEO_AGE",
    "geo_age",
)
LITHOLOGY_ALIASES = (
    "LITHOLOGY",
    "lithology",
    "LITH",
    "lith",
    "ROCKTYPE",
    "rocktype",
    "ROCK_TYPE",
    "rock_type",
    "TYPE",
    "type",
)
KEEP_COLUMNS = ["geometry", "geologic_unit", "geology_description", "geologic_age_raw", "lithology_raw", "geologic_unit_source_field", "geology_description_source_field", "geologic_age_source_field", "lithology_source_field"]

AGE_KEYWORDS = [
    "Holocene",
    "Pleistocene",
    "Quaternary",
    "Pliocene",
    "Miocene",
    "Oligocene",
    "Eocene",
    "Paleocene",
    "Tertiary",
    "Cretaceous",
    "Jurassic",
    "Triassic",
    "Permian",
    "Pennsylvanian",
    "Mississippian",
    "Devonian",
    "Silurian",
    "Ordovician",
    "Cambrian",
    "Paleozoic",
    "Mesozoic",
    "Cenozoic",
    "Proterozoic",
    "Archean",
    "Precambrian",
]

LITHOLOGY_KEYWORDS = [
    "alluvium",
    "basalt",
    "carbonate",
    "conglomerate",
    "dolomite",
    "gneiss",
    "granite",
    "limestone",
    "marble",
    "quartzite",
    "rhyolite",
    "sandstone",
    "schist",
    "shale",
    "slate",
    "tuff",
    "volcanic",
]


def load_geology(shapefile_path: str | Path):
    """Load SGMC geology polygons and normalize CRS/column names."""

    gpd = _require_geopandas()
    geology = gpd.read_file(shapefile_path)
    if geology.empty:
        LOGGER.warning("Geology layer %s is empty.", shapefile_path)
    if geology.crs is None:
        LOGGER.warning("Geology layer %s has no CRS; assuming EPSG:4326.", shapefile_path)
        geology = geology.set_crs(TARGET_CRS)
    elif geology.crs.to_string() != TARGET_CRS:
        geology = geology.to_crs(TARGET_CRS)

    unit_column = _first_existing_column(geology, UNIT_ALIASES)
    description_column = _first_existing_column(geology, DESCRIPTION_ALIASES)
    age_column = _first_existing_column(geology, AGE_ALIASES)
    lithology_column = _first_existing_column(geology, LITHOLOGY_ALIASES)
    geology = geology.copy()
    geology["geologic_unit"] = geology[unit_column].map(_clean_text) if unit_column else None
    geology["geology_description"] = geology[description_column].map(_clean_text) if description_column else None
    geology["geologic_age_raw"] = geology[age_column].map(_clean_text) if age_column else None
    geology["lithology_raw"] = geology[lithology_column].map(_clean_text) if lithology_column else None
    geology["geologic_unit_source_field"] = unit_column
    geology["geology_description_source_field"] = description_column
    geology["geologic_age_source_field"] = age_column
    geology["lithology_source_field"] = lithology_column
    normalized = geology[KEEP_COLUMNS].copy()
    normalized.attrs["source_path"] = str(shapefile_path)
    return normalized


def load_deposits(parquet_path: str | Path):
    """Load unified deposit GeoParquet and ensure EPSG:4326 point geometry."""

    gpd = _require_geopandas()
    deposits = gpd.read_parquet(parquet_path)
    deposits = deposits.copy()
    if "geometry" not in deposits.columns or deposits.geometry.name is None:
        if {"longitude", "latitude"}.issubset(deposits.columns):
            deposits = gpd.GeoDataFrame(
                deposits,
                geometry=gpd.points_from_xy(deposits["longitude"], deposits["latitude"], crs=TARGET_CRS),
                crs=TARGET_CRS,
            )
        else:
            raise RuntimeError("Deposit dataset must contain geometry or latitude/longitude columns.")
    if deposits.crs is None:
        deposits = deposits.set_crs(TARGET_CRS)
    elif deposits.crs.to_string() != TARGET_CRS:
        deposits = deposits.to_crs(TARGET_CRS)
    return deposits


def spatial_join(deposits_gdf, geology_gdf):
    """Assign geologic units to deposits using a within join with intersects fallback."""

    gpd = _require_geopandas()
    deposits = deposits_gdf.copy()
    deposits["_deposit_index"] = deposits.index
    deposits_with_geometry = deposits[deposits.geometry.notna()].copy()
    deposits_without_geometry = deposits[deposits.geometry.isna()].copy()

    joined = _ensure_geology_columns(_join_with_predicate(deposits_with_geometry, geology_gdf, predicate="within"))
    unmatched = joined[joined["geologic_unit"].isna()].drop(columns=["index_right"], errors="ignore")
    matched = joined[joined["geologic_unit"].notna()]

    if not unmatched.empty:
        fallback = _ensure_geology_columns(_join_with_predicate(unmatched[deposits.columns], geology_gdf, predicate="intersects"))
        joined = gpd.GeoDataFrame(
            _concat([matched, fallback], ignore_index=True),
            geometry="geometry",
            crs=deposits.crs,
        )

    if not deposits_without_geometry.empty:
        for column in ("geologic_unit", "geology_description", "geologic_age_raw", "lithology_raw"):
            if column not in deposits_without_geometry.columns:
                deposits_without_geometry[column] = None
        joined = gpd.GeoDataFrame(
            _concat([joined, deposits_without_geometry], ignore_index=True),
            geometry="geometry",
            crs=deposits.crs,
        )

    joined = joined.sort_values("_deposit_index").drop_duplicates(subset=["_deposit_index"], keep="first")
    joined = joined.drop(columns=["index_right", "_deposit_index"], errors="ignore")
    joined["geologic_unit"] = joined.get("geologic_unit").map(_clean_text)
    joined["geology_description"] = joined.get("geology_description").map(_clean_text)
    joined["lithology"] = joined.apply(
        lambda row: _clean_text(row.get("lithology_raw")) or extract_lithology(row.get("geologic_unit"), row.get("geology_description")),
        axis=1,
    )
    joined["geologic_age"] = joined.apply(
        lambda row: _clean_text(row.get("geologic_age_raw")) or extract_geologic_age(row.get("geologic_unit"), row.get("geology_description")),
        axis=1,
    )
    source_path = geology_gdf.attrs.get("source_path")
    joined["record_uuid"] = joined.apply(_ensure_record_uuid, axis=1)
    joined["provenance"] = joined.apply(lambda row: _append_geology_provenance(row, source_path=source_path), axis=1)
    joined = joined.drop(
        columns=[
            "geologic_age_raw",
            "lithology_raw",
            "geologic_unit_source_field",
            "geology_description_source_field",
            "geologic_age_source_field",
            "lithology_source_field",
        ],
        errors="ignore",
    )
    return joined


def enrich_with_geology(parquet_path: str | Path, shapefile_path: str | Path):
    """Load deposits and geology polygons, then return geology-enriched deposits."""

    deposits = load_deposits(parquet_path)
    geology = load_geology(shapefile_path)
    return spatial_join(deposits, geology)


def export_enriched(dataframe, output_path: str | Path) -> Path:
    """Write enriched deposit data to GeoParquet."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    dataframe.to_parquet(output, index=False)
    LOGGER.info("Wrote %s geology-enriched records to %s.", len(dataframe), output)
    return output


def _append_geology_provenance(row: Any, *, source_path: str | None) -> dict[str, Any]:
    record_uuid = row.get("record_uuid") or deterministic_uuid("geology", row.get("record_id"), row.get("site_name"), row.get("latitude"), row.get("longitude"))
    provenance = ensure_provenance(row.get("provenance"), record_uuid=record_uuid)
    matched = _clean_text(row.get("geologic_unit")) is not None
    provenance = append_lineage(
        provenance,
        step="geology_enrichment",
        method="spatial_join_with_intersects_fallback",
        inputs=[source_path or "geology_layer", str(row.get("record_id"))],
        outputs=[str(row.get("record_id"))],
        confidence=1.0 if matched else 0.0,
        details={"matched_geology": matched, "target_crs": TARGET_CRS},
    )
    if not matched:
        return provenance

    field_specs = {
        "geologic_unit": (row.get("geologic_unit"), row.get("geologic_unit_source_field"), "spatial_join_attribute_transfer", ["point_in_polygon_join"]),
        "geology_description": (row.get("geology_description"), row.get("geology_description_source_field"), "spatial_join_attribute_transfer", ["point_in_polygon_join", "text_cleanup"]),
        "lithology": (row.get("lithology"), row.get("lithology_source_field"), "lithology_normalization", ["attribute_mapping", "fallback_keyword_extraction"]),
        "geologic_age": (row.get("geologic_age"), row.get("geologic_age_source_field"), "geologic_age_normalization", ["attribute_mapping", "fallback_keyword_extraction"]),
    }
    for field, (value, source_field, method, transformations) in field_specs.items():
        if _clean_text(value) is None:
            continue
        provenance = set_field(
            provenance,
            field,
            field_event(
                field,
                _json_ready(value),
                source="GEOLOGY",
                source_file=source_path,
                source_record_id=_clean_text(row.get("geologic_unit")),
                source_field=_clean_text(source_field),
                method=method,
                confidence=1.0,
                transformations=transformations,
                normalization_decisions=["geologic context assigned from containing polygon"],
            ),
        )
    return provenance


def _ensure_record_uuid(row: Any) -> str:
    existing = row.get("record_uuid")
    if existing is not None and existing == existing and str(existing).strip():
        return str(existing)
    return deterministic_uuid("deposit", row.get("record_id"), row.get("site_name"), row.get("latitude"), row.get("longitude"))


def _json_ready(value: Any) -> Any:
    try:
        if value != value:
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return value


def extract_lithology(unit_name: Any, description: Any = None) -> str | None:
    """Extract a simple lithology label from SGMC unit text."""

    text = _combined_text(unit_name, description)
    if not text:
        return None
    lowered = text.lower()
    for keyword in LITHOLOGY_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", lowered):
            return keyword
    cleaned = _clean_text(unit_name) or _clean_text(description)
    return cleaned.lower() if cleaned else None


def extract_geologic_age(unit_name: Any, description: Any = None) -> str | None:
    """Extract a rough geologic age from unit text using simple keyword matching."""

    text = _combined_text(unit_name, description)
    if not text:
        return None
    for keyword in AGE_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", text, flags=re.IGNORECASE):
            return keyword
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enrich unified mineral deposits with SGMC geologic context.")
    parser.add_argument("--input", "-i", required=True, help="Path to unified deposit GeoParquet input.")
    parser.add_argument("--shapefile", "-s", required=True, help="Path to SGMC shapefile or vector layer.")
    parser.add_argument("--output", "-o", default="enriched.parquet", help="Output enriched GeoParquet path.")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s:%(name)s:%(message)s")
    try:
        enriched = enrich_with_geology(args.input, args.shapefile)
        export_enriched(enriched, args.output)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    matched = int(enriched["geologic_unit"].notna().sum()) if "geologic_unit" in enriched.columns else 0
    total = len(enriched)
    percent = (matched / total * 100.0) if total else 0.0
    print(json.dumps({"total_deposits": total, "matched_geology": matched, "matched_percent": round(percent, 2)}, indent=2, sort_keys=True))
    print(f"Output: {args.output}")
    return 0


def _join_with_predicate(deposits, geology, *, predicate: str):
    gpd = _require_geopandas()
    if deposits.empty:
        return deposits.copy()
    return gpd.sjoin(deposits, geology, how="left", predicate=predicate)


def _ensure_geology_columns(dataframe):
    frame = dataframe.copy()
    for column in ("geologic_unit", "geology_description", "geologic_age_raw", "lithology_raw"):
        if column not in frame.columns:
            frame[column] = None
    return frame


def _first_existing_column(dataframe, aliases: tuple[str, ...]) -> str | None:
    columns_by_lower = {column.lower(): column for column in dataframe.columns}
    for alias in aliases:
        if alias in dataframe.columns:
            return alias
        if alias.lower() in columns_by_lower:
            return columns_by_lower[alias.lower()]
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
    except (TypeError, ValueError):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _combined_text(*values: Any) -> str:
    return " ".join(text for text in (_clean_text(value) for value in values) if text)


def _concat(frames, *, ignore_index: bool):
    pd = _require_pandas()
    return pd.concat(frames, ignore_index=ignore_index)


def _require_geopandas():
    try:
        import geopandas as gpd
    except ImportError as exc:  # pragma: no cover - depends on deployment env
        raise RuntimeError("Geology enrichment requires geopandas. Install requirements.txt first.") from exc
    return gpd


def _require_pandas():
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - depends on deployment env
        raise RuntimeError("Geology enrichment requires pandas. Install requirements.txt first.") from exc
    return pd


if __name__ == "__main__":
    raise SystemExit(main())
