"""Fusion pipeline for MRDS CSV records and PDF-extracted deposits.

Example:
    python -m src.etl.fusion --csv rdbms-tab/MRDS.txt --pdf reports/example.pdf --output out/unified.parquet
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .ingest_mrds import load_mrds, normalize_mrds
from .models import MineralDeposit
from .pdf_ingest import process_pdf

LOGGER = logging.getLogger(__name__)

SOURCE_COLUMNS = [
    "record_id",
    "site_name",
    "latitude",
    "longitude",
    "commodities",
    "source",
    "confidence_score",
    "grade",
    "tonnage",
    "source_url",
]

COMMODITY_MAP = {
    "ag": "silver",
    "au": "gold",
    "cu": "copper",
    "fe": "iron",
    "hg": "mercury",
    "nb": "niobium",
    "pb": "lead",
    "ree": "rare earth elements",
    "rare earth": "rare earth elements",
    "rare earth elements": "rare earth elements",
    "zn": "zinc",
}


def load_sources(csv_path: str | Path, pdf_path: str | Path):
    """Load and normalize MRDS CSV data plus PDF-extracted deposits."""

    mrds = mrds_to_dataframe(normalize_mrds(load_mrds(csv_path)))
    pdf = pdf_to_dataframe(process_pdf(pdf_path))
    return mrds, pdf


def normalize_commodities(commodities: list[str] | tuple[str, ...] | set[str] | str | None) -> list[str]:
    """Canonicalize commodity names with lightweight dictionary mapping."""

    if commodities is None:
        return []
    values = [commodities] if isinstance(commodities, str) else list(commodities)
    normalized: list[str] = []
    seen: set[str] = set()
    for value in _expand_commodity_values(values):
        text = value.strip().lower()
        if not text:
            continue
        text = COMMODITY_MAP.get(text, text)
        if text not in seen:
            normalized.append(text)
            seen.add(text)
    return normalized


def mrds_to_dataframe(mrds_df):
    """Convert normalized MRDS records into the fusion schema."""

    pd = _require_pandas()
    frame = pd.DataFrame(index=mrds_df.index)
    frame["record_id"] = mrds_df.get("record_id")
    frame["site_name"] = mrds_df.get("site_name")
    frame["latitude"] = pd.to_numeric(mrds_df.get("latitude"), errors="coerce")
    frame["longitude"] = pd.to_numeric(mrds_df.get("longitude"), errors="coerce")
    frame["commodities"] = mrds_df.get("commodities", pd.Series([[]] * len(mrds_df))).map(normalize_commodities)
    frame["source"] = "mrds"
    frame["confidence_score"] = 1.0
    frame["grade"] = pd.to_numeric(mrds_df.get("grade"), errors="coerce")
    frame["tonnage"] = pd.to_numeric(mrds_df.get("tonnage"), errors="coerce")
    frame["source_url"] = mrds_df.get("source_url")
    return _ensure_schema(frame)


def pdf_to_dataframe(deposits: list[MineralDeposit]):
    """Convert PDF-extracted Pydantic models into the fusion schema."""

    pd = _require_pandas()
    records = [_model_to_dict(deposit) for deposit in deposits]
    if not records:
        return _ensure_schema(pd.DataFrame(columns=SOURCE_COLUMNS))
    frame = pd.DataFrame.from_records(records)
    frame["source"] = "pdf"
    frame["commodities"] = frame.get("commodities", pd.Series([[]] * len(frame))).map(normalize_commodities)
    return _ensure_schema(frame)


def match_records(mrds_df, pdf_df, *, name_threshold: float = 88.0, distance_threshold_km: float = 50.0) -> dict[str, Any]:
    """Match PDF records to MRDS records by site-name similarity and coordinates."""

    matched_pairs: list[dict[str, Any]] = []
    used_mrds: set[int] = set()
    used_pdf: set[int] = set()

    for pdf_index, pdf_row in pdf_df.iterrows():
        best: dict[str, Any] | None = None
        for mrds_index, mrds_row in mrds_df.iterrows():
            if mrds_index in used_mrds:
                continue
            name_score = _name_similarity(mrds_row.get("site_name"), pdf_row.get("site_name"))
            distance_km = _distance_km(mrds_row, pdf_row)
            matched_by_name = name_score >= name_threshold
            matched_by_distance = distance_km is not None and distance_km <= distance_threshold_km
            if not matched_by_name and not matched_by_distance:
                continue

            candidate_score = name_score + (50.0 - min(distance_km or 50.0, 50.0))
            if best is None or candidate_score > best["candidate_score"]:
                best = {
                    "mrds_index": mrds_index,
                    "pdf_index": pdf_index,
                    "name_score": round(name_score, 2),
                    "distance_km": round(distance_km, 3) if distance_km is not None else None,
                    "candidate_score": candidate_score,
                }

        if best is not None:
            used_mrds.add(best["mrds_index"])
            used_pdf.add(pdf_index)
            best.pop("candidate_score", None)
            matched_pairs.append(best)

    return {
        "matched_pairs": matched_pairs,
        "unmatched_mrds": mrds_df.drop(index=list(used_mrds)).copy(),
        "unmatched_pdf": pdf_df.drop(index=list(used_pdf)).copy(),
    }


def merge_matched_records(mrds_record: dict[str, Any], pdf_record: dict[str, Any]) -> dict[str, Any]:
    """Merge one matched MRDS/PDF pair into a unified record."""

    mrds_lat = _coerce_float(mrds_record.get("latitude"))
    mrds_lon = _coerce_float(mrds_record.get("longitude"))
    pdf_lat = _coerce_float(pdf_record.get("latitude"))
    pdf_lon = _coerce_float(pdf_record.get("longitude"))
    commodities = normalize_commodities(_as_list(mrds_record.get("commodities")) + _as_list(pdf_record.get("commodities")))

    return {
        "record_id": mrds_record.get("record_id") or pdf_record.get("record_id"),
        "site_name": mrds_record.get("site_name") or pdf_record.get("site_name"),
        "latitude": mrds_lat if mrds_lat is not None else pdf_lat,
        "longitude": mrds_lon if mrds_lon is not None else pdf_lon,
        "commodities": commodities,
        "source": "mrds+pdf",
        "confidence_score": max(_coerce_float(mrds_record.get("confidence_score")) or 0.0, _coerce_float(pdf_record.get("confidence_score")) or 0.0),
        "grade": _first_present(pdf_record.get("grade"), mrds_record.get("grade")),
        "tonnage": _first_present(pdf_record.get("tonnage"), mrds_record.get("tonnage")),
        "source_url": _join_sources(mrds_record.get("source_url"), pdf_record.get("source_url")),
    }


def build_unified_dataset(csv_path: str | Path, pdf_path: str | Path):
    """Build a deduplicated, ML-ready dataframe from MRDS and PDF sources."""

    pd = _require_pandas()
    mrds_df, pdf_df = load_sources(csv_path, pdf_path)
    match_result = match_records(mrds_df, pdf_df)

    merged_records = [
        merge_matched_records(
            mrds_df.loc[pair["mrds_index"]].to_dict(),
            pdf_df.loc[pair["pdf_index"]].to_dict(),
        )
        for pair in match_result["matched_pairs"]
    ]
    merged_df = pd.DataFrame.from_records(merged_records)
    unified = pd.concat(
        [
            _ensure_schema(merged_df),
            _ensure_schema(match_result["unmatched_mrds"]),
            _ensure_schema(match_result["unmatched_pdf"]),
        ],
        ignore_index=True,
    )
    unified = _dedupe_dataset(unified)
    unified.attrs["match_counts"] = {
        "matched": len(match_result["matched_pairs"]),
        "unmatched_mrds": len(match_result["unmatched_mrds"]),
        "unmatched_pdf": len(match_result["unmatched_pdf"]),
        "total": len(unified),
    }
    return unified


def export_geoparquet(dataframe, output_path: str | Path) -> Path:
    """Export the unified dataset to GeoParquet with nullable geometries."""

    try:
        import geopandas as gpd
        from shapely.geometry import Point
    except ImportError as exc:  # pragma: no cover - depends on deployment env
        raise RuntimeError("Unified GeoParquet export requires geopandas and shapely.") from exc

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    geometry = [
        Point(float(row.longitude), float(row.latitude)) if _has_coordinates(row.latitude, row.longitude) else None
        for row in dataframe.itertuples(index=False)
    ]
    geo_dataframe = gpd.GeoDataFrame(dataframe.copy(), geometry=geometry, crs="EPSG:4326")
    geo_dataframe.to_parquet(output, index=False)
    LOGGER.info("Wrote %s unified records to %s.", len(geo_dataframe), output)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge MRDS CSV and PDF-extracted mineral deposit records.")
    parser.add_argument("--csv", required=True, help="Path to MRDS CSV/TSV input.")
    parser.add_argument("--pdf", required=True, help="Path to geological PDF input.")
    parser.add_argument("--output", "-o", default="unified.parquet", help="Output GeoParquet path.")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s:%(name)s:%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    try:
        unified = build_unified_dataset(args.csv, args.pdf)
        export_geoparquet(unified, args.output)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    counts = unified.attrs.get("match_counts", {})
    print(json.dumps(counts, indent=2, sort_keys=True))
    print(f"Output: {args.output}")
    return 0


def _ensure_schema(dataframe):
    pd = _require_pandas()
    frame = dataframe.copy()
    for column in SOURCE_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[SOURCE_COLUMNS]
    frame["latitude"] = pd.to_numeric(frame["latitude"], errors="coerce")
    frame["longitude"] = pd.to_numeric(frame["longitude"], errors="coerce")
    frame["confidence_score"] = pd.to_numeric(frame["confidence_score"], errors="coerce").fillna(0.0)
    frame["grade"] = pd.to_numeric(frame["grade"], errors="coerce")
    frame["tonnage"] = pd.to_numeric(frame["tonnage"], errors="coerce")
    frame["commodities"] = frame["commodities"].map(lambda value: normalize_commodities(_as_list(value)))
    return frame


def _dedupe_dataset(dataframe):
    frame = dataframe.copy()
    frame["_site_key"] = frame["site_name"].map(_site_key)
    frame["_coord_key"] = frame.apply(lambda row: f"{round(row.latitude, 4)}:{round(row.longitude, 4)}" if _has_coordinates(row.latitude, row.longitude) else "", axis=1)
    frame = frame.sort_values(["source", "confidence_score"], ascending=[True, False])
    frame = frame.drop_duplicates(subset=["_site_key", "_coord_key"], keep="first")
    return frame.drop(columns=["_site_key", "_coord_key"]).reset_index(drop=True)


def _name_similarity(left: Any, right: Any) -> float:
    left_key = _site_key(left)
    right_key = _site_key(right)
    if not left_key or not right_key:
        return 0.0
    return SequenceMatcher(None, left_key, right_key).ratio() * 100.0


def _site_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _distance_km(left: Any, right: Any) -> float | None:
    left_lat = _coerce_float(left.get("latitude"))
    left_lon = _coerce_float(left.get("longitude"))
    right_lat = _coerce_float(right.get("latitude"))
    right_lon = _coerce_float(right.get("longitude"))
    if None in (left_lat, left_lon, right_lat, right_lon):
        return None

    radius_km = 6371.0088
    phi1 = math.radians(left_lat)
    phi2 = math.radians(right_lat)
    delta_phi = math.radians(right_lat - left_lat)
    delta_lambda = math.radians(right_lon - left_lon)
    haversine = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(haversine))


def _has_coordinates(latitude: Any, longitude: Any) -> bool:
    lat = _coerce_float(latitude)
    lon = _coerce_float(longitude)
    return lat is not None and lon is not None and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or value != value:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value == value and value != "":
            return value
    return None


def _join_sources(*values: Any) -> str | None:
    sources = [str(value) for value in values if value is not None and value == value and str(value).strip()]
    return ";".join(dict.fromkeys(sources)) or None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _expand_commodity_values(values: list[Any]) -> list[str]:
    expanded: list[str] = []
    for value in values:
        text = str(value).strip().lower().replace("_", " ")
        if not text:
            continue
        phrase = COMMODITY_MAP.get(text.replace("-", " "))
        if phrase is not None:
            expanded.append(phrase)
            continue
        expanded.extend(token for token in re.split(r"[\s,;/|+\-]+", text) if token)
    return expanded


def _model_to_dict(model: MineralDeposit) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _require_pandas():
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - depends on deployment env
        raise RuntimeError("Fusion requires pandas. Install requirements.txt first.") from exc
    return pd


if __name__ == "__main__":
    raise SystemExit(main())
