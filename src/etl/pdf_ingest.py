"""PDF ingestion and LLM entity extraction for mineral deposit reports.

Example:
    python -m src.etl.pdf_ingest --input report.pdf
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .confidence import attach_record_confidence, load_confidence_config, normalization_event, validation_report
from .document_ingest import DocumentIngestionResult, ingest_pdf_document, write_ingestion_artifacts
from .ingest_mrds import normalize_commodities
from .llm_extract import extract_deposits_from_chunk
from .models import MineralDeposit
from .provenance import append_lineage, deterministic_uuid, empty_provenance, field_event, set_field

LOGGER = logging.getLogger(__name__)


@dataclass
class PdfExtractionResult:
    """Domain-level extraction result with raw and normalized records."""

    deposits: list[MineralDeposit]
    raw_records: list[dict[str, Any]]
    merged_records: list[dict[str, Any]]
    document: DocumentIngestionResult
    metrics: dict[str, Any]
    warnings: list[str]
    errors: list[str]

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        return {
            "deposits": [_model_to_dict(deposit) for deposit in self.deposits],
            "raw_records": self.raw_records,
            "merged_records": self.merged_records,
            "document": self.document.to_dict(include_text=include_text),
            "metrics": self.metrics,
            "warnings": self.warnings,
            "errors": self.errors,
        }


def extract_text_from_pdf(pdf_path: str | Path) -> str:
    """Extract cleaned text from a PDF using the reliability ingestion layer."""

    result = ingest_pdf_document(pdf_path, enable_ocr=False)
    if result.document_type in {"missing", "malformed"}:
        raise RuntimeError("; ".join(result.errors) or f"Could not read PDF: {pdf_path}")
    return result.raw_text


def chunk_text(text: str, max_tokens: int = 1500) -> list[str]:
    """Split text into chunks with a simple token approximation.

    This compatibility wrapper preserves the previous public helper for callers
    that pass plain text instead of using document-level chunk provenance.
    """

    cleaned = _clean_text(text)
    if not cleaned:
        return []

    max_chars = max(500, max_tokens * 4)
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_hard_wrap(sentence, max_chars))
            continue
        candidate = f"{current} {sentence}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current.strip())
            current = sentence

    if current:
        chunks.append(current.strip())
    return chunks


def merge_results(results: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Deduplicate chunk-level extraction results by site-name similarity."""

    merged: list[dict[str, Any]] = []
    appearances: dict[int, int] = {}

    for deposit in [item for group in results for item in group]:
        site_name = _clean_optional_string(deposit.get("site_name"))
        if not site_name:
            continue

        enriched = _with_provenance(dict(deposit), site_name=site_name)
        match_index = _find_similar_site(merged, site_name)
        if match_index is None:
            merged.append(enriched)
            appearances[len(merged) - 1] = 1
            continue

        merged[match_index] = _merge_record(merged[match_index], enriched)
        appearances[match_index] = appearances.get(match_index, 1) + 1

    for index, record in enumerate(merged):
        record["confidence_score"] = score_confidence(record, appearances.get(index, 1))
        if appearances.get(index, 1) > 1:
            record.setdefault("extraction_warnings", []).append("duplicate_site_name_merged")
    return merged


def score_confidence(record: dict[str, Any], appearances: int = 1) -> float:
    """Assign preliminary deterministic confidence before field scoring."""

    config = load_confidence_config()
    score = (config.source_score("PDF") + config.method_score("llm_structured_json")) / 2
    if _is_valid_latitude(record.get("latitude")) and _is_valid_longitude(record.get("longitude")):
        score += config.modifier("pdf_preliminary_valid_coordinates")
    if record.get("commodities"):
        score += config.modifier("pdf_preliminary_commodity_present")
    if _clean_optional_string(record.get("site_name")):
        score += config.modifier("pdf_preliminary_site_name_present")
    if appearances > 1:
        score += config.modifier("duplicate_site_agreement")
    warnings = [str(value) for value in _as_list(record.get("extraction_warnings"))]
    if any(value.startswith(("invalid_", "low_ocr_confidence", "ocr_text_quality_low")) for value in warnings):
        score -= config.penalty("preliminary_pdf_warning")
    return min(round(max(score, 0.0), 2), 1.0)


def process_pdf(pdf_path: str | Path, **kwargs: Any) -> list[MineralDeposit]:
    """Run the PDF -> reliable chunks -> LLM -> merge -> validation pipeline."""

    return process_pdf_with_report(pdf_path, **kwargs).deposits


def process_pdf_with_report(
    pdf_path: str | Path,
    *,
    artifacts_dir: str | Path | None = None,
    enable_ocr: bool = True,
    ocr_language: str = "eng",
    max_tokens: int = 1500,
) -> PdfExtractionResult:
    """Extract deposits and return traceability, raw records, and metrics."""

    document = ingest_pdf_document(pdf_path, enable_ocr=enable_ocr, ocr_language=ocr_language, max_tokens=max_tokens)
    if document.document_type in {"missing", "malformed"}:
        raise RuntimeError("; ".join(document.errors) or f"Could not read PDF: {pdf_path}")

    chunk_results: list[list[dict[str, Any]]] = []
    raw_records: list[dict[str, Any]] = []
    llm_chunk_failures = 0

    for chunk in document.chunks:
        try:
            extracted = extract_deposits_from_chunk(chunk.text)
        except RuntimeError:
            raise
        except Exception as exc:  # Keep one bad chunk from killing the whole document.
            llm_chunk_failures += 1
            LOGGER.warning("LLM extraction failed for %s in %s: %s", chunk.chunk_id, pdf_path, exc)
            continue

        annotated_records: list[dict[str, Any]] = []
        for record in extracted:
            if not isinstance(record, dict):
                continue
            annotated = dict(record)
            annotated.update(
                {
                    "_chunk_id": chunk.chunk_id,
                    "_chunk_index": chunk.chunk_index,
                    "_page_numbers": chunk.page_numbers,
                    "_page_start": chunk.page_start,
                    "_page_end": chunk.page_end,
                    "_source_text_sha1": chunk.text_sha1,
                }
            )
            raw_records.append(annotated)
            annotated_records.append(annotated)
        chunk_results.append(annotated_records)

    merged = merge_results(chunk_results)
    deposits = validate_deposits(merged, source_path=pdf_path)
    metrics = _build_extraction_metrics(document, raw_records, merged, deposits, llm_chunk_failures)
    result = PdfExtractionResult(
        deposits=deposits,
        raw_records=raw_records,
        merged_records=merged,
        document=document,
        metrics=metrics,
        warnings=document.warnings,
        errors=document.errors,
    )

    if artifacts_dir:
        _write_pdf_extraction_artifacts(result, artifacts_dir)
    return result


def validate_deposits(records: list[dict[str, Any]], *, source_path: str | Path | None = None) -> list[MineralDeposit]:
    """Convert extracted dictionaries into validated MineralDeposit models."""

    deposits: list[MineralDeposit] = []
    for record in records:
        prepared = _prepare_record(record, source_path=source_path)
        try:
            deposits.append(MineralDeposit(**prepared))
        except Exception as exc:  # Pydantic exposes different exception classes across versions.
            LOGGER.warning("Dropping invalid extracted deposit %r: %s", record.get("site_name"), exc)
    return deposits


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract mineral deposit records from a geological PDF report.")
    parser.add_argument("--input", "-i", required=True, help="Path to a geological PDF report.")
    parser.add_argument("--artifacts-dir", help="Write raw text, chunks, tables, raw LLM records, normalized records, and metrics.")
    parser.add_argument("--metrics", action="store_true", help="Print extraction metrics after the sample record.")
    parser.add_argument("--max-tokens", type=int, default=1500, help="Approximate max chunk size for downstream LLM extraction.")
    parser.add_argument("--ocr-language", default="eng", help="Tesseract language code when OCR fallback is available.")
    parser.add_argument("--no-ocr", action="store_true", help="Disable OCR fallback routing.")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s:%(name)s:%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    try:
        result = process_pdf_with_report(
            args.input,
            artifacts_dir=args.artifacts_dir,
            enable_ocr=not args.no_ocr,
            ocr_language=args.ocr_language,
            max_tokens=args.max_tokens,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Deposits found: {len(result.deposits)}")
    if result.deposits:
        print(json.dumps(_model_to_dict(result.deposits[0]), indent=2, sort_keys=True))
    if args.metrics:
        print(json.dumps(result.metrics, indent=2, sort_keys=True))
    return 0


def _build_extraction_metrics(
    document: DocumentIngestionResult,
    raw_records: list[dict[str, Any]],
    merged_records: list[dict[str, Any]],
    deposits: list[MineralDeposit],
    llm_chunk_failures: int,
) -> dict[str, Any]:
    confidences = [_model_to_dict(deposit).get("confidence_score") for deposit in deposits]
    numeric_confidences = [float(value) for value in confidences if value is not None]
    report = {}
    if deposits:
        try:
            pd = __import__("pandas")
            records = [_model_to_dict(deposit) for deposit in deposits]
            report = validation_report(pd.DataFrame.from_records(records), stage="pdf_extraction")
        except Exception:  # pragma: no cover - metrics should never fail extraction.
            report = {}
    return {
        **document.metrics,
        "raw_entity_candidates": len(raw_records),
        "merged_entity_candidates": len(merged_records),
        "validated_deposits": len(deposits),
        "invalid_deposits": max(len(merged_records) - len(deposits), 0),
        "llm_chunk_failures": llm_chunk_failures,
        "confidence_distribution": _confidence_distribution(numeric_confidences),
        "confidence_validation_report": report,
    }


def _write_pdf_extraction_artifacts(result: PdfExtractionResult, output_dir: str | Path) -> dict[str, str]:
    paths = write_ingestion_artifacts(result.document, output_dir)
    output = Path(output_dir)
    raw_records_path = output / "raw_entity_candidates.json"
    merged_records_path = output / "merged_entity_candidates.json"
    normalized_path = output / "validated_deposits.json"
    metrics_path = output / "extraction_metrics.json"
    raw_records_path.write_text(json.dumps(result.raw_records, indent=2, sort_keys=True), encoding="utf-8")
    merged_records_path.write_text(json.dumps(result.merged_records, indent=2, sort_keys=True), encoding="utf-8")
    normalized_path.write_text(json.dumps([_model_to_dict(deposit) for deposit in result.deposits], indent=2, sort_keys=True), encoding="utf-8")
    metrics_path.write_text(json.dumps(result.metrics, indent=2, sort_keys=True), encoding="utf-8")
    paths.update(
        {
            "raw_entity_candidates": str(raw_records_path),
            "merged_entity_candidates": str(merged_records_path),
            "validated_deposits": str(normalized_path),
            "extraction_metrics": str(metrics_path),
        }
    )
    return paths


def _prepare_record(record: dict[str, Any], *, source_path: str | Path | None) -> dict[str, Any]:
    site_name = _clean_optional_string(record.get("site_name"))
    commodities = normalize_commodities(record.get("commodities") or [])
    warnings = list(dict.fromkeys(str(value) for value in _as_list(record.get("extraction_warnings")) if str(value).strip()))
    latitude = _coerce_float(record.get("latitude"))
    longitude = _coerce_float(record.get("longitude"))
    if latitude is not None and not -90.0 <= latitude <= 90.0:
        warnings.append(f"invalid_latitude:{latitude}")
        latitude = None
    if longitude is not None and not -180.0 <= longitude <= 180.0:
        warnings.append(f"invalid_longitude:{longitude}")
        longitude = None
    if (latitude is None) != (longitude is None):
        warnings.append("partial_coordinates")
        latitude = None
        longitude = None

    record_id = _record_id(site_name, source_path)
    source_pages = _int_list(record.get("source_pages"))
    source_chunks = _string_list(record.get("source_chunks"))
    record_uuid = deterministic_uuid("pdf", source_path, site_name, source_pages)
    prepared = {
        "record_id": record_id,
        "record_uuid": record_uuid,
        "site_name": site_name,
        "latitude": latitude,
        "longitude": longitude,
        "commodities": commodities,
        "commod1": commodities[0] if commodities else None,
        "commod2": commodities[1] if len(commodities) > 1 else None,
        "grade": _coerce_float(record.get("grade")),
        "tonnage": _coerce_float(record.get("tonnage")),
        "source_url": str(source_path) if source_path else None,
        "confidence_score": _coerce_float(record.get("confidence_score")),
        "source_pages": source_pages,
        "source_chunks": source_chunks,
        "source_text_sha1": _clean_optional_string(record.get("source_text_sha1")),
        "extraction_warnings": list(dict.fromkeys(warnings)),
        "raw_extraction": _raw_snapshot(record),
    }
    prepared["provenance"] = _build_pdf_provenance(record, prepared, source_path=source_path)
    return attach_record_confidence(prepared, stage="pdf_extraction", config=load_confidence_config())


def _build_pdf_provenance(record: dict[str, Any], prepared: dict[str, Any], *, source_path: str | Path | None) -> dict[str, Any]:
    source_file = str(source_path) if source_path else None
    record_uuid = prepared.get("record_uuid")
    confidence = _coerce_float(prepared.get("confidence_score")) or 0.0
    pages = _int_list(prepared.get("source_pages"))
    chunks = _string_list(prepared.get("source_chunks"))
    source_text_sha1 = _clean_optional_string(prepared.get("source_text_sha1"))
    provenance = empty_provenance(record_uuid=record_uuid)
    provenance = append_lineage(
        provenance,
        step="document_ingestion",
        method="pdf_text_or_ocr_routing",
        inputs=[source_file or "PDF"],
        outputs=chunks,
        confidence=confidence,
        details={"pages": pages, "source_text_sha1": source_text_sha1},
    )
    provenance = append_lineage(
        provenance,
        step="llm_entity_extraction",
        method="openai_structured_json",
        inputs=chunks,
        outputs=[str(prepared.get("record_id"))],
        confidence=confidence,
        details={"source_priority": 60},
    )

    field_specs = {
        "record_id": (prepared.get("record_id"), "deterministic_record_id", ["stable_hash_generation"], []),
        "record_uuid": (prepared.get("record_uuid"), "deterministic_uuid", ["stable_uuid_generation"], []),
        "site_name": (prepared.get("site_name"), "llm_structured_json", ["text_cleanup"], []),
        "latitude": (prepared.get("latitude"), "coordinate_validation", ["numeric_coercion", "coordinate_range_validation"], _coordinate_decisions(record, "latitude", prepared.get("latitude"))),
        "longitude": (prepared.get("longitude"), "coordinate_validation", ["numeric_coercion", "coordinate_range_validation"], _coordinate_decisions(record, "longitude", prepared.get("longitude"))),
        "commodities": (prepared.get("commodities"), "commodity_normalization", ["commodity_abbreviation_expansion", "lowercase_deduplication"], _normalization_decisions(record.get("commodities"), prepared.get("commodities"))),
        "commod1": (prepared.get("commod1"), "commodity_normalization", ["primary_commodity_selection"], []),
        "commod2": (prepared.get("commod2"), "commodity_normalization", ["secondary_commodity_selection"], []),
        "grade": (prepared.get("grade"), "llm_structured_json", ["numeric_coercion"], _normalization_decisions(record.get("grade"), prepared.get("grade"))),
        "tonnage": (prepared.get("tonnage"), "llm_structured_json", ["numeric_coercion"], _normalization_decisions(record.get("tonnage"), prepared.get("tonnage"))),
        "confidence_score": (prepared.get("confidence_score"), "confidence_scoring", ["evidence_weighted_scoring"], []),
    }
    for field, (value, method, transformations, decisions) in field_specs.items():
        provenance = set_field(
            provenance,
            field,
            field_event(
                field,
                _json_ready(value),
                source="PDF",
                source_file=source_file,
                source_record_id=prepared.get("record_id"),
                source_field=field,
                pages=pages,
                chunk_ids=chunks,
                source_text_sha1=source_text_sha1,
                method=method,
                confidence=confidence if value is not None else 0.0,
                transformations=transformations,
                normalization_decisions=decisions,
                normalization_events=_pdf_normalization_events(field, record, value),
                warnings=prepared.get("extraction_warnings"),
            ),
        )
    provenance = append_lineage(
        provenance,
        step="pdf_validation",
        method="pydantic_schema_validation",
        inputs=[str(prepared.get("record_id"))],
        outputs=[str(prepared.get("record_id"))],
        confidence=confidence,
        details={"warnings": prepared.get("extraction_warnings", [])},
    )
    return provenance


def _merge_record(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key in ("latitude", "longitude", "grade", "tonnage"):
        if merged.get(key) in (None, "") and incoming.get(key) not in (None, ""):
            merged[key] = incoming[key]

    commodities = normalize_commodities(_as_list(merged.get("commodities")) + _as_list(incoming.get("commodities")))
    merged["commodities"] = commodities
    if len(str(incoming.get("site_name", ""))) > len(str(merged.get("site_name", ""))):
        merged["site_name"] = incoming["site_name"]
    merged["source_pages"] = _join_int_lists(merged.get("source_pages"), incoming.get("source_pages"))
    merged["source_chunks"] = _join_string_lists(merged.get("source_chunks"), incoming.get("source_chunks"))
    merged["extraction_warnings"] = _join_string_lists(merged.get("extraction_warnings"), incoming.get("extraction_warnings"))
    merged["source_text_sha1"] = _join_strings(merged.get("source_text_sha1"), incoming.get("source_text_sha1"))
    return merged


def _with_provenance(record: dict[str, Any], *, site_name: str) -> dict[str, Any]:
    record["site_name"] = site_name
    record["source_pages"] = _int_list(record.get("_page_numbers"))
    record["source_chunks"] = _string_list(record.get("_chunk_id"))
    record["source_text_sha1"] = _clean_optional_string(record.get("_source_text_sha1"))
    record.setdefault("extraction_warnings", [])
    if not record["source_pages"]:
        record["extraction_warnings"].append("missing_page_provenance")
    return record


def _coordinate_decisions(raw_record: dict[str, Any], field: str, normalized_value: Any) -> list[str]:
    raw_value = raw_record.get(field)
    decisions = _normalization_decisions(raw_value, normalized_value)
    if raw_value not in (None, "") and normalized_value is None:
        decisions.append(f"invalid_{field}_nulled:{raw_value}")
    return decisions


def _pdf_normalization_events(field: str, raw_record: dict[str, Any], normalized_value: Any) -> list[dict[str, Any]]:
    raw_value = raw_record.get(field)
    if field == "commodities":
        return [
            normalization_event(
                "commodity_alias_expansion",
                source_value=_json_ready(raw_value),
                normalized_value=_json_ready(normalized_value),
                ontology_version="dymium-commodity-v1",
                confidence_delta=0.01,
                notes="LLM commodity string normalized through Dymium commodity aliases",
            )
        ]
    if field in {"latitude", "longitude"}:
        delta = -0.20 if raw_value not in (None, "") and normalized_value is None else 0.01
        event_type = "invalid_coordinate_rejection" if delta < 0 else "coordinate_numeric_parse"
        return [
            normalization_event(
                event_type,
                source_value=_json_ready(raw_value),
                normalized_value=_json_ready(normalized_value),
                ontology_version="epsg-4326-range",
                confidence_delta=delta,
            )
        ]
    if _json_ready(raw_value) != _json_ready(normalized_value):
        return [
            normalization_event(
                "llm_value_coercion",
                source_value=_json_ready(raw_value),
                normalized_value=_json_ready(normalized_value),
                ontology_version="dymium-schema-v1",
                confidence_delta=-0.01,
            )
        ]
    return []


def _normalization_decisions(raw_value: Any, normalized_value: Any) -> list[str]:
    if raw_value in (None, "") and normalized_value in (None, ""):
        return ["source_value_missing"]
    if _json_ready(raw_value) != _json_ready(normalized_value):
        return [f"normalized_from:{raw_value}"]
    return []


def _find_similar_site(records: list[dict[str, Any]], site_name: str) -> int | None:
    normalized = _site_key(site_name)
    for index, record in enumerate(records):
        candidate = _site_key(str(record.get("site_name", "")))
        if normalized == candidate or SequenceMatcher(None, normalized, candidate).ratio() >= 0.88:
            return index
    return None


def _record_id(site_name: str | None, source_path: str | Path | None) -> str:
    seed = f"{source_path or ''}:{site_name or ''}".encode("utf-8", errors="ignore")
    return f"pdf-{hashlib.sha1(seed).hexdigest()[:16]}"


def _site_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_text(text: str) -> str:
    text = text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _hard_wrap(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            space = text.rfind(" ", start, end)
            if space > start:
                end = space
        chunks.append(text[start:end].strip())
        start = end
    return [chunk for chunk in chunks if chunk]


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _int_list(value: Any) -> list[int]:
    values: list[int] = []
    for item in _as_list(value):
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number not in values:
            values.append(number)
    return values


def _string_list(value: Any) -> list[str]:
    values: list[str] = []
    for item in _as_list(value):
        text = str(item).strip()
        if text and text not in values:
            values.append(text)
    return values


def _join_int_lists(*values: Any) -> list[int]:
    joined: list[int] = []
    for value in values:
        for item in _int_list(value):
            if item not in joined:
                joined.append(item)
    return sorted(joined)


def _join_string_lists(*values: Any) -> list[str]:
    joined: list[str] = []
    for value in values:
        for item in _string_list(value):
            if item not in joined:
                joined.append(item)
    return joined


def _join_strings(*values: Any) -> str | None:
    joined = _join_string_lists(*values)
    return ";".join(joined) if joined else None


def _raw_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def _json_ready(value: Any) -> Any:
    try:
        if value != value:
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return value


def _is_valid_latitude(value: Any) -> bool:
    number = _coerce_float(value)
    return number is not None and -90.0 <= number <= 90.0


def _is_valid_longitude(value: Any) -> bool:
    number = _coerce_float(value)
    return number is not None and -180.0 <= number <= 180.0


def _confidence_distribution(values: list[float]) -> dict[str, int]:
    buckets = {"0.00-0.24": 0, "0.25-0.49": 0, "0.50-0.74": 0, "0.75-1.00": 0}
    for value in values:
        if value < 0.25:
            buckets["0.00-0.24"] += 1
        elif value < 0.5:
            buckets["0.25-0.49"] += 1
        elif value < 0.75:
            buckets["0.50-0.74"] += 1
        else:
            buckets["0.75-1.00"] += 1
    return buckets


def _model_to_dict(model: MineralDeposit) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


if __name__ == "__main__":
    raise SystemExit(main())
