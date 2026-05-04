"""CSV/TSV ingestion and normalization for USGS MRDS deposits.

Example:
    python -m src.etl.ingest_mrds rdbms-tab/MRDS.txt --output out/mrds.parquet

The module is also safe to call from AWS Lambda handlers:

    from src.etl.ingest_mrds import process_mrds
    output_path = process_mrds("/tmp/MRDS.txt", "/tmp/mrds.parquet")
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
from pathlib import Path
from typing import Iterable, Mapping

from .models import MineralDeposit

LOGGER = logging.getLogger(__name__)

NULL_STRINGS = {"", " ", "na", "n/a", "null", "none", "nan", "-999", "-9999"}

COLUMN_ALIASES: Mapping[str, tuple[str, ...]] = {
    "record_id": ("dep_id", "record_id", "site_id", "id"),
    "site_name": ("name", "site_name", "deposit_name", "mine_name"),
    "development_status": ("dev_stat", "development_status", "status"),
    "source_url": ("url", "source_url", "mrds_url"),
    "latitude": ("latitude", "lat", "y"),
    "longitude": ("longitude", "lon", "long", "x"),
    "commodity_text": ("code_list", "commodities", "commodity_codes", "commod", "commod1"),
    "commod1": ("commod1", "commodity_1", "primary_commodity"),
    "commod2": ("commod2", "commodity_2", "secondary_commodity"),
    "tonnage": ("tonnage", "tons", "ore_tonnage"),
    "grade": ("grade", "ore_grade"),
}

COMMODITY_SYNONYMS: Mapping[str, str] = {
    "ag": "silver",
    "al": "aluminum",
    "as": "arsenic",
    "au": "gold",
    "ba": "barite",
    "cu": "copper",
    "fe": "iron",
    "hg": "mercury",
    "mn": "manganese",
    "mo": "molybdenum",
    "pb": "lead",
    "ree": "rare earth elements",
    "rare earth": "rare earth elements",
    "rare earth element": "rare earth elements",
    "rare earth elements": "rare earth elements",
    "sb": "antimony",
    "sn": "tin",
    "u": "uranium",
    "w": "tungsten",
    "zn": "zinc",
}


def process_mrds(input_path: str | Path, output_path: str | Path) -> Path:
    """Normalize an MRDS CSV/TSV file and export it as GeoParquet.

    Args:
        input_path: Local MRDS delimited file path. S3 download can be added by
            swapping this boundary without changing normalization internals.
        output_path: Destination GeoParquet path.

    Returns:
        The written output path.
    """

    dataframe = load_mrds(input_path)
    normalized = normalize_mrds(dataframe)
    return export_geoparquet(normalized, output_path)


def load_mrds(input_path: str | Path):
    """Load an MRDS delimited text file with delimiter inference."""

    pd = _require_pandas()
    path = Path(input_path)
    delimiter = sniff_delimiter(path)
    return pd.read_csv(
        path,
        sep=delimiter,
        dtype="string",
        keep_default_na=False,
        na_values=[],
        quoting=csv.QUOTE_MINIMAL,
        low_memory=False,
    )


def normalize_mrds(dataframe):
    """Return a normalized dataframe with stable Mercury ETL columns."""

    pd = _require_pandas()
    df = dataframe.copy()
    df.columns = [_clean_column_name(column) for column in df.columns]

    normalized = pd.DataFrame(index=df.index)
    normalized["record_id"] = _first_available(df, COLUMN_ALIASES["record_id"]).map(_clean_scalar)
    normalized["site_name"] = _first_available(df, COLUMN_ALIASES["site_name"]).map(_clean_scalar)
    normalized["development_status"] = _first_available(df, COLUMN_ALIASES["development_status"]).map(_clean_scalar)
    normalized["source_url"] = _first_available(df, COLUMN_ALIASES["source_url"]).map(_clean_scalar)

    normalized["latitude"] = pd.to_numeric(_first_available(df, COLUMN_ALIASES["latitude"]), errors="coerce")
    normalized["longitude"] = pd.to_numeric(_first_available(df, COLUMN_ALIASES["longitude"]), errors="coerce")
    normalized["tonnage"] = pd.to_numeric(_first_available(df, COLUMN_ALIASES["tonnage"]), errors="coerce")
    normalized["grade"] = pd.to_numeric(_first_available(df, COLUMN_ALIASES["grade"]), errors="coerce")

    commodity_source = _first_available(df, COLUMN_ALIASES["commodity_text"])
    if "commod1" in df.columns or "commodity_1" in df.columns or "primary_commodity" in df.columns:
        normalized["commod1"] = _first_available(df, COLUMN_ALIASES["commod1"]).map(_clean_scalar)
    else:
        normalized["commod1"] = commodity_source.map(lambda value: _commodity_at(value, 0))
    if "commod2" in df.columns or "commodity_2" in df.columns or "secondary_commodity" in df.columns:
        normalized["commod2"] = _first_available(df, COLUMN_ALIASES["commod2"]).map(_clean_scalar)
    else:
        normalized["commod2"] = commodity_source.map(lambda value: _commodity_at(value, 1))
    normalized["commodity_codes"] = commodity_source.map(normalize_commodity_codes)
    normalized["commodities"] = commodity_source.map(normalize_commodities)

    valid_mask = (
        normalized["record_id"].notna()
        & normalized["latitude"].between(-90.0, 90.0, inclusive="both")
        & normalized["longitude"].between(-180.0, 180.0, inclusive="both")
    )
    invalid_count = int((~valid_mask).sum())
    if invalid_count:
        LOGGER.warning("Dropping %s invalid MRDS records with missing IDs or invalid coordinates.", invalid_count)

    normalized = normalized.loc[valid_mask].reset_index(drop=True)
    normalized.attrs["dropped_invalid_records"] = invalid_count
    return normalized


def iter_deposits(dataframe) -> Iterable[MineralDeposit]:
    """Yield Pydantic-validated deposit models from a normalized dataframe."""

    records = dataframe.to_dict(orient="records")
    for record in records:
        yield MineralDeposit(**record)


def export_geoparquet(dataframe, output_path: str | Path) -> Path:
    """Convert normalized records to GeoDataFrame and write GeoParquet."""

    try:
        import geopandas as gpd
    except ImportError as exc:  # pragma: no cover - depends on deployment env
        raise RuntimeError("GeoParquet export requires geopandas. Install requirements.txt first.") from exc

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    geometry = gpd.points_from_xy(dataframe["longitude"], dataframe["latitude"], crs="EPSG:4326")
    geo_dataframe = gpd.GeoDataFrame(dataframe.copy(), geometry=geometry, crs="EPSG:4326")
    geo_dataframe.to_parquet(output, index=False)
    LOGGER.info("Wrote %s normalized MRDS records to %s.", len(geo_dataframe), output)
    return output


def normalize_commodities(value: object) -> list[str]:
    """Normalize commodity text to deduplicated, lowercase commodity names."""

    names: list[str] = []
    seen: set[str] = set()
    for token in _split_commodities(value):
        key = token.lower()
        name = COMMODITY_SYNONYMS.get(key, key)
        if name not in seen:
            names.append(name)
            seen.add(name)
    return names


def normalize_commodity_codes(value: object) -> list[str]:
    """Normalize commodity text to deduplicated uppercase MRDS-style codes."""

    codes: list[str] = []
    seen: set[str] = set()
    for token in _split_commodities(value):
        code = token.upper()
        if code not in seen:
            codes.append(code)
            seen.add(code)
    return codes


def sniff_delimiter(path: Path) -> str:
    """Infer comma or tab delimiter from the first line of a source file."""

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t|;").delimiter
    except csv.Error:
        first_line = sample.splitlines()[0] if sample else ""
        return "\t" if first_line.count("\t") >= first_line.count(",") else ","


def _split_commodities(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        tokens: list[str] = []
        for item in value:
            tokens.extend(_split_commodities(item))
        return tokens
    cleaned = _clean_scalar(value)
    if cleaned is None:
        return []
    phrase = COMMODITY_SYNONYMS.get(cleaned.lower())
    if phrase is not None:
        return [phrase]
    return [token for token in re.split(r"[,\s;/|]+", cleaned) if token]


def _commodity_at(value: object, index: int) -> str | None:
    codes = normalize_commodity_codes(value)
    return codes[index] if len(codes) > index else None


def _clean_column_name(column: object) -> str:
    return re.sub(r"[^0-9a-zA-Z]+", "_", str(column).strip().lower()).strip("_")


def _clean_scalar(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().strip('"')
    if text.lower() in NULL_STRINGS:
        return None
    return text


def _first_available(dataframe, aliases: Iterable[str]):
    pd = _require_pandas()
    for alias in aliases:
        if alias in dataframe.columns:
            return dataframe[alias]
    return pd.Series([None] * len(dataframe), index=dataframe.index, dtype="object")


def _require_pandas():
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - depends on deployment env
        raise RuntimeError("MRDS ingestion requires pandas. Install requirements.txt first.") from exc
    return pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize USGS MRDS CSV/TSV data and write GeoParquet.")
    parser.add_argument("input", help="Path to MRDS CSV/TSV input.")
    parser.add_argument("--output", "-o", required=True, help="Output GeoParquet path.")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s:%(name)s:%(message)s")
    process_mrds(args.input, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
