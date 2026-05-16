"""Small ontology-aware mapper for early-stage geological reconciliation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from src.etl.provenance import deterministic_uuid, utc_now

DEFAULT_ONTOLOGY_PATH = Path(__file__).resolve().parents[3] / "config" / "reconciliation" / "ontology.json"


@dataclass
class MappingResult:
    raw_value: Any
    normalized_value: Any = None
    normalized_values: list[Any] = field(default_factory=list)
    method: str = "unmapped"
    confidence: float = 0.0
    matched_term: str | None = None
    warnings: list[str] = field(default_factory=list)
    normalization_events: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CoordinateResult:
    latitude: float | None
    longitude: float | None
    crs: str
    valid: bool
    confidence: float
    method: str
    warnings: list[str] = field(default_factory=list)


def load_ontology(path: str | Path | None = None) -> dict[str, Any]:
    ontology_path = Path(path) if path else DEFAULT_ONTOLOGY_PATH
    return json.loads(ontology_path.read_text(encoding="utf-8"))


class OntologyMapper:
    """Deterministic mapper with alias, synonym, and fuzzy fallback support."""

    def __init__(self, ontology: dict[str, Any] | None = None) -> None:
        self.ontology = ontology or load_ontology()
        self.version = str(self.ontology.get("version", "unknown"))
        self._commodity_aliases = self._build_alias_index(self.ontology.get("commodities", {}))
        self._lithology_aliases = self._build_alias_index(self.ontology.get("lithologies", {}))
        self._age_aliases = self._build_age_alias_index(self.ontology.get("geologic_ages", {}))
        self._unit_aliases = self._build_alias_index(self.ontology.get("units", {}))

    def source_trust(self, source_dataset: str | None) -> float:
        values = self.ontology.get("source_trust", {})
        return _clamp(values.get(str(source_dataset or "UNKNOWN"), values.get("UNKNOWN", 0.45)))

    def map_commodities(self, value: Any) -> MappingResult:
        tokens = _split_commodity_tokens(value)
        normalized: list[str] = []
        events: list[dict[str, Any]] = []
        warnings: list[str] = []
        confidences: list[float] = []
        methods: set[str] = set()
        for token in tokens:
            result = self._map_single(token, self._commodity_aliases, kind="commodity")
            if result.normalized_value and result.normalized_value not in normalized:
                normalized.append(result.normalized_value)
            warnings.extend(result.warnings)
            events.extend(result.normalization_events)
            confidences.append(result.confidence)
            methods.add(result.method)
        if not tokens:
            warnings.append("missing_commodity_value")
        return MappingResult(
            raw_value=value,
            normalized_values=normalized,
            normalized_value=normalized,
            method=_combined_method(methods, default="commodity_unmapped"),
            confidence=round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
            warnings=list(dict.fromkeys(warnings)),
            normalization_events=events,
            metadata={"ontology_version": self.version, "token_count": len(tokens)},
        )

    def map_lithology(self, value: Any) -> MappingResult:
        result = self._map_single(value, self._lithology_aliases, kind="lithology")
        if result.normalized_value:
            metadata = self.ontology.get("lithologies", {}).get(result.normalized_value, {})
            result.metadata["deposit_model"] = metadata.get("deposit_model")
        return result

    def map_geologic_age(self, value: Any) -> MappingResult:
        return self._map_single(value, self._age_aliases, kind="geologic_age")

    def map_units(self, value: Any) -> MappingResult:
        if value is None or str(value).strip() == "":
            return MappingResult(raw_value=value, method="unit_missing", warnings=["missing_unit"])
        if isinstance(value, (list, tuple, set)):
            raw_values = list(value)
        else:
            raw_values = [token for token in re.split(r"[,;/|]+", str(value)) if token.strip()]
        normalized = []
        events = []
        warnings = []
        confidences = []
        methods = set()
        for raw in raw_values:
            result = self._map_single(raw, self._unit_aliases, kind="unit")
            if result.normalized_value and result.normalized_value not in normalized:
                normalized.append(result.normalized_value)
            events.extend(result.normalization_events)
            warnings.extend(result.warnings)
            confidences.append(result.confidence)
            methods.add(result.method)
        return MappingResult(
            raw_value=value,
            normalized_value=normalized,
            normalized_values=normalized,
            method=_combined_method(methods, default="unit_unmapped"),
            confidence=round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
            warnings=list(dict.fromkeys(warnings)),
            normalization_events=events,
            metadata={"ontology_version": self.version},
        )

    def normalize_coordinates(self, latitude: Any, longitude: Any, crs: Any = None) -> CoordinateResult:
        lat = _to_float(latitude)
        lon = _to_float(longitude)
        normalized_crs = str(crs or "EPSG:4326").strip() or "EPSG:4326"
        warnings: list[str] = []
        confidence = 0.92
        method = "coordinate_numeric_parse_v1"
        if crs in (None, ""):
            warnings.append("missing_crs_assumed_epsg_4326")
            confidence -= 0.07
        elif normalized_crs.upper() not in {"EPSG:4326", "WGS84", "WGS 84"}:
            warnings.append(f"unsupported_crs:{normalized_crs}")
            confidence -= 0.25
        if lat is None or lon is None:
            warnings.append("missing_coordinates")
            return CoordinateResult(None, None, normalized_crs, False, 0.0, method, warnings)
        if not -90 <= lat <= 90:
            warnings.append(f"invalid_latitude:{lat}")
        if not -180 <= lon <= 180:
            warnings.append(f"invalid_longitude:{lon}")
        valid = not any(item.startswith("invalid_") for item in warnings)
        if not valid:
            confidence = 0.0
        return CoordinateResult(lat if valid else None, lon if valid else None, normalized_crs, valid, _clamp(confidence), method, warnings)

    def _map_single(self, value: Any, alias_index: dict[str, dict[str, Any]], *, kind: str) -> MappingResult:
        raw = _clean_text(value)
        if raw is None:
            return MappingResult(raw_value=value, method=f"{kind}_missing", warnings=[f"missing_{kind}"])
        key = _norm(raw)
        if key in alias_index:
            entry = alias_index[key]
            canonical = entry["canonical"]
            confidence = _clamp(entry.get("confidence", 0.9))
            return MappingResult(
                raw_value=value,
                normalized_value=canonical,
                normalized_values=[canonical],
                method=f"{kind}_ontology_alias_mapping_v1",
                confidence=confidence,
                matched_term=key,
                normalization_events=[_normalization_event(f"{kind}_alias_mapping", raw, canonical, self.version, confidence_delta=0.03)],
                metadata={"ontology_version": self.version},
            )
        best_key, best_entry, ratio = self._best_fuzzy(key, alias_index)
        if best_key and ratio >= 0.86:
            canonical = best_entry["canonical"]
            confidence = round(min(_clamp(best_entry.get("confidence", 0.8)), ratio) - 0.12, 4)
            return MappingResult(
                raw_value=value,
                normalized_value=canonical,
                normalized_values=[canonical],
                method=f"{kind}_fuzzy_ontology_mapping_v1",
                confidence=max(confidence, 0.0),
                matched_term=best_key,
                warnings=[f"fuzzy_{kind}_mapping:{raw}->{canonical}"],
                normalization_events=[_normalization_event(f"{kind}_fuzzy_mapping", raw, canonical, self.version, confidence_delta=-0.06)],
                metadata={"ontology_version": self.version, "similarity": round(ratio, 4)},
            )
        return MappingResult(
            raw_value=value,
            normalized_value=raw.lower(),
            normalized_values=[raw.lower()],
            method=f"{kind}_unmapped_preserved_raw",
            confidence=0.35,
            warnings=[f"unmapped_{kind}:{raw}"],
            normalization_events=[_normalization_event(f"{kind}_raw_preservation", raw, raw.lower(), self.version, confidence_delta=-0.15)],
            metadata={"ontology_version": self.version},
        )

    def _best_fuzzy(self, key: str, alias_index: dict[str, dict[str, Any]]) -> tuple[str | None, dict[str, Any] | None, float]:
        best_key = None
        best_entry = None
        best_ratio = 0.0
        for alias, entry in alias_index.items():
            ratio = SequenceMatcher(None, key, alias).ratio()
            if ratio > best_ratio:
                best_key = alias
                best_entry = entry
                best_ratio = ratio
        return best_key, best_entry, best_ratio

    @staticmethod
    def _build_alias_index(values: dict[str, Any]) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for canonical, metadata in values.items():
            aliases = metadata.get("aliases", []) if isinstance(metadata, dict) else []
            for alias in [canonical, *aliases]:
                index[_norm(alias)] = {"canonical": canonical, **(metadata if isinstance(metadata, dict) else {})}
        return index

    @staticmethod
    def _build_age_alias_index(values: dict[str, Any]) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for canonical, aliases in values.items():
            for alias in [canonical, *(aliases or [])]:
                index[_norm(alias)] = {"canonical": canonical, "confidence": 0.86}
        return index


def _normalization_event(event_type: str, raw: Any, normalized: Any, ontology_version: str, *, confidence_delta: float) -> dict[str, Any]:
    return {
        "event_id": deterministic_uuid("schema-reconciliation-normalization", event_type, raw, normalized, ontology_version),
        "type": event_type,
        "source_value": raw,
        "normalized_value": normalized,
        "ontology_version": ontology_version,
        "confidence_delta": confidence_delta,
        "timestamp": utc_now(),
    }


def _split_commodity_tokens(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        tokens: list[str] = []
        for item in value:
            tokens.extend(_split_commodity_tokens(item))
        return tokens
    text = _clean_text(value)
    if not text:
        return []
    lowered = text.lower()
    phrase_tokens = []
    for phrase in ("rare earth elements", "rare earth element", "rare earth"):
        if phrase in lowered:
            phrase_tokens.append(phrase)
            lowered = lowered.replace(phrase, " ")
    tokens = [token for token in re.split(r"[\s,;/|+]+", lowered) if token and token not in {"and", "with"}]
    return [*phrase_tokens, *tokens]


def _combined_method(methods: set[str], *, default: str) -> str:
    if not methods:
        return default
    if len(methods) == 1:
        return next(iter(methods))
    if any("fuzzy" in method for method in methods):
        return "mixed_with_fuzzy_ontology_mapping_v1"
    if any("unmapped" in method for method in methods):
        return "mixed_with_unmapped_raw_preservation_v1"
    return "ontology_alias_mapping_v1"


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if value != value:
            return None
    except (TypeError, ValueError):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip().strip('"')
    return text or None


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value != value or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return round(max(0.0, min(number, 1.0)), 4)
