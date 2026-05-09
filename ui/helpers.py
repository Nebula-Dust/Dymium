"""Utility helpers for the Dymium Streamlit demo."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import pandas as pd


MAP_COLUMNS = [
    "site_name",
    "latitude",
    "longitude",
    "commodities",
    "lithology",
    "geologic_age",
    "geologic_unit",
]

DISPLAY_COLUMNS = [
    "record_id",
    "site_name",
    "latitude",
    "longitude",
    "commodities",
    "source",
    "confidence_score",
    "geologic_unit",
    "lithology",
    "geologic_age",
]

COMMODITY_COLORS = {
    "gold": [218, 165, 32, 190],
    "silver": [190, 190, 200, 180],
    "copper": [184, 115, 51, 185],
    "lead": [100, 110, 130, 180],
    "zinc": [70, 130, 180, 180],
    "rare earth elements": [78, 121, 167, 190],
    "niobium": [126, 87, 194, 185],
}
DEFAULT_COLOR = [52, 152, 219, 165]


def load_geoparquet(path: str | Path) -> gpd.GeoDataFrame:
    """Load a GeoParquet file and preserve geospatial metadata."""

    return gpd.read_parquet(path)


def summarize_dataset(dataframe: pd.DataFrame) -> dict[str, Any]:
    """Return high-level metrics shown in the overview tab."""

    total = len(dataframe)
    matched = int(dataframe["geologic_unit"].notna().sum()) if "geologic_unit" in dataframe else 0
    matched_percent = round((matched / total * 100.0), 2) if total else 0.0
    return {
        "total_deposits": total,
        "matched_geology": matched,
        "matched_percent": matched_percent,
        "rows_with_coordinates": int((dataframe["latitude"].notna() & dataframe["longitude"].notna()).sum())
        if {"latitude", "longitude"}.issubset(dataframe.columns)
        else 0,
    }


def source_counts(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Return source counts for dashboard display."""

    if "source" not in dataframe:
        return pd.DataFrame(columns=["source", "count"])
    counts = dataframe["source"].value_counts(dropna=False).reset_index()
    counts.columns = ["source", "count"]
    return counts


def commodity_counts(dataframe: pd.DataFrame, limit: int = 15) -> pd.DataFrame:
    """Build a frequency table from list-like commodity values."""

    if "commodities" not in dataframe:
        return pd.DataFrame(columns=["commodity", "count"])
    counts: dict[str, int] = {}
    for value in dataframe["commodities"].dropna():
        for commodity in as_list(value):
            key = str(commodity).strip().lower()
            if key:
                counts[key] = counts.get(key, 0) + 1
    return pd.DataFrame(sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit], columns=["commodity", "count"])


def available_values(dataframe: pd.DataFrame, column: str, limit: int = 200) -> list[str]:
    """Return sorted non-empty filter values for a scalar column."""

    if column not in dataframe:
        return []
    values = sorted({str(value) for value in dataframe[column].dropna().tolist() if str(value).strip()})
    return values[:limit]


def available_commodities(dataframe: pd.DataFrame, limit: int = 200) -> list[str]:
    """Return sorted commodity filter values."""

    if "commodities" not in dataframe:
        return []
    values: set[str] = set()
    for value in dataframe["commodities"].dropna():
        values.update(str(item).strip().lower() for item in as_list(value) if str(item).strip())
    return sorted(values)[:limit]


def filter_dataframe(dataframe: pd.DataFrame, commodities: list[str], ages: list[str], lithologies: list[str]) -> pd.DataFrame:
    """Apply sidebar/tab filters to the dataset."""

    filtered = dataframe
    if commodities and "commodities" in filtered:
        wanted = {item.lower() for item in commodities}
        filtered = filtered[filtered["commodities"].map(lambda value: bool(wanted.intersection({str(item).lower() for item in as_list(value)})))]
    if ages and "geologic_age" in filtered:
        filtered = filtered[filtered["geologic_age"].isin(ages)]
    if lithologies and "lithology" in filtered:
        filtered = filtered[filtered["lithology"].isin(lithologies)]
    return filtered


def prepare_map_dataframe(dataframe: pd.DataFrame, max_points: int) -> pd.DataFrame:
    """Prepare sampled point data for PyDeck rendering."""

    if not {"latitude", "longitude"}.issubset(dataframe.columns):
        return pd.DataFrame(columns=MAP_COLUMNS + ["commodity_label", "color"])
    points = dataframe[dataframe["latitude"].notna() & dataframe["longitude"].notna()].copy()
    if len(points) > max_points:
        points = points.sample(max_points, random_state=42)
    for column in MAP_COLUMNS:
        if column not in points:
            points[column] = None
    points["commodity_label"] = points["commodities"].map(lambda value: ", ".join(map(str, as_list(value))))
    points["color"] = points["commodities"].map(_commodity_color)
    return points[MAP_COLUMNS + ["commodity_label", "color"]]


def display_dataframe(dataframe: pd.DataFrame, limit: int = 1000, *, compact: bool = True) -> pd.DataFrame:
    """Return a table preview with optional compact columns."""

    if compact:
        columns = [column for column in DISPLAY_COLUMNS if column in dataframe.columns]
    else:
        columns = list(dataframe.columns)
    return dataframe[columns].head(limit).copy()


def dataframe_to_csv_bytes(dataframe: pd.DataFrame) -> bytes:
    """Serialize a dataframe preview/download to CSV bytes."""

    return dataframe.to_csv(index=False).encode("utf-8")


def geodataframe_to_parquet_bytes(dataframe: gpd.GeoDataFrame) -> bytes:
    """Serialize a GeoDataFrame to GeoParquet bytes for Streamlit downloads."""

    with tempfile.NamedTemporaryFile(suffix=".parquet") as handle:
        dataframe.to_parquet(handle.name, index=False)
        return Path(handle.name).read_bytes()


def materialize_uploaded_file(uploaded_file: Any, suffix: str) -> Path | None:
    """Persist a Streamlit UploadedFile to a temp path."""

    if uploaded_file is None:
        return None
    path = Path(tempfile.mkdtemp(prefix="dymium_upload_")) / f"upload{suffix}"
    path.write_bytes(uploaded_file.getbuffer())
    return path


def materialize_vector_upload(uploaded_files: Iterable[Any] | None) -> Path | None:
    """Persist uploaded shapefile sidecars or a GeoPackage and return the vector path."""

    if not uploaded_files:
        return None
    upload_dir = Path(tempfile.mkdtemp(prefix="dymium_vector_"))
    vector_path: Path | None = None
    for uploaded_file in uploaded_files:
        destination = upload_dir / uploaded_file.name
        destination.write_bytes(uploaded_file.getbuffer())
        suffix = destination.suffix.lower()
        if suffix in {".gpkg", ".geojson", ".json"}:
            vector_path = destination
        elif suffix == ".shp":
            vector_path = destination
    return vector_path


def as_list(value: Any) -> list[Any]:
    """Coerce list-like Arrow/Pandas values into plain Python lists."""

    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (str, bytes, dict)):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _commodity_color(value: Any) -> list[int]:
    commodities = [str(item).lower() for item in as_list(value)]
    for commodity in commodities:
        if commodity in COMMODITY_COLORS:
            return COMMODITY_COLORS[commodity]
    return DEFAULT_COLOR
