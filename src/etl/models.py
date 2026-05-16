"""Pydantic models for normalized MRDS mineral deposit records."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

try:  # Pydantic v2
    from pydantic import ConfigDict, field_validator
except ImportError:  # pragma: no cover - exercised only on Pydantic v1
    ConfigDict = None  # type: ignore[assignment]
    from pydantic import validator as field_validator  # type: ignore[assignment]


class MineralDeposit(BaseModel):
    """Normalized, ML-ready mineral deposit record.

    The schema intentionally keeps source identifiers and commodity fields
    alongside normalized geospatial fields so downstream PDF, CSV, and
    shapefile joins can use the same stable keys.
    """

    if ConfigDict is not None:
        model_config = ConfigDict(extra="allow", populate_by_name=True)

    record_id: str = Field(..., min_length=1)
    record_uuid: str | None = None
    site_name: str | None = None
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    commodities: list[str] = Field(default_factory=list)
    commodity_codes: list[str] = Field(default_factory=list)
    commod1: str | None = None
    commod2: str | None = None
    development_status: str | None = None
    source_url: str | None = None
    source_pages: list[int] = Field(default_factory=list)
    source_chunks: list[str] = Field(default_factory=list)
    source_text_sha1: str | None = None
    extraction_warnings: list[str] = Field(default_factory=list)
    raw_extraction: dict[str, Any] | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    tonnage: float | None = Field(default=None, ge=0.0)
    grade: float | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("commodities", "commodity_codes", "source_chunks", "extraction_warnings", mode="before")
    @classmethod
    def _coerce_string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return list(value)

    @field_validator("provenance", "raw_extraction", mode="before")
    @classmethod
    def _coerce_dict(cls, value: Any) -> dict[str, Any] | None:
        if value is None:
            return {} if value is None else value
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            import json

            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @field_validator("source_pages", mode="before")
    @classmethod
    def _coerce_int_list(cls, value: Any) -> list[int]:
        if value is None:
            return []
        values = value if isinstance(value, (list, tuple, set)) else [value]
        cleaned: list[int] = []
        for item in values:
            try:
                cleaned.append(int(item))
            except (TypeError, ValueError):
                continue
        return cleaned

    @field_validator("commodities")
    @classmethod
    def _normalize_commodity_names(cls, values: list[str]) -> list[str]:
        return _dedupe_clean(values, upper=False)

    @field_validator("commodity_codes")
    @classmethod
    def _normalize_commodity_codes(cls, values: list[str]) -> list[str]:
        return _dedupe_clean(values, upper=True)

    @field_validator("record_uuid", "site_name", "commod1", "commod2", "development_status", "source_url", "source_text_sha1", mode="before")
    @classmethod
    def _empty_string_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value


def _dedupe_clean(values: list[str], *, upper: bool) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        if not item:
            continue
        item = item.upper() if upper else item.lower()
        if item not in seen:
            cleaned.append(item)
            seen.add(item)
    return cleaned
