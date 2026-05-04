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
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .ingest_mrds import normalize_commodities
from .llm_extract import extract_deposits_from_chunk
from .models import MineralDeposit

LOGGER = logging.getLogger(__name__)


def extract_text_from_pdf(pdf_path: str | Path) -> str:
    """Extract cleaned text from all pages in a PDF using PyMuPDF."""

    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - depends on deployment env
        raise RuntimeError("PDF ingestion requires pymupdf. Install requirements.txt first.") from exc

    path = Path(pdf_path)
    parts: list[str] = []
    with fitz.open(path) as document:
        for page_index, page in enumerate(document, start=1):
            try:
                text = page.get_text("text") or ""
            except UnicodeError as exc:
                LOGGER.warning("Skipping page %s in %s due to encoding error: %s", page_index, path, exc)
                continue
            if not text.strip():
                LOGGER.debug("Page %s in %s contains no extractable text.", page_index, path)
                continue
            parts.append(text)
    return _clean_text("\n".join(parts))


def chunk_text(text: str, max_tokens: int = 1500) -> list[str]:
    """Split text into chunks with a simple token approximation.

    This intentionally avoids tokenizer dependencies. It approximates one token
    as four characters and prefers sentence boundaries where possible.
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

        match_index = _find_similar_site(merged, site_name)
        if match_index is None:
            record = dict(deposit)
            record["site_name"] = site_name
            merged.append(record)
            appearances[len(merged) - 1] = 1
            continue

        merged[match_index] = _merge_record(merged[match_index], deposit)
        appearances[match_index] = appearances.get(match_index, 1) + 1

    for index, record in enumerate(merged):
        record["confidence_score"] = score_confidence(record, appearances.get(index, 1))
    return merged


def score_confidence(record: dict[str, Any], appearances: int = 1) -> float:
    """Assign deterministic confidence based on available evidence."""

    score = 0.0
    if _is_valid_latitude(record.get("latitude")) and _is_valid_longitude(record.get("longitude")):
        score += 0.4
    if record.get("commodities"):
        score += 0.3
    if _clean_optional_string(record.get("site_name")):
        score += 0.2
    if appearances > 1:
        score += 0.1
    return min(round(score, 2), 1.0)


def process_pdf(pdf_path: str | Path) -> list[MineralDeposit]:
    """Run the PDF -> text -> chunks -> LLM -> merge -> validation pipeline."""

    text = extract_text_from_pdf(pdf_path)
    chunks = chunk_text(text)
    chunk_results = [extract_deposits_from_chunk(chunk) for chunk in chunks]
    merged = merge_results(chunk_results)
    return validate_deposits(merged, source_path=pdf_path)


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
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s:%(name)s:%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    try:
        deposits = process_pdf(args.input)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Deposits found: {len(deposits)}")
    if deposits:
        print(json.dumps(_model_to_dict(deposits[0]), indent=2, sort_keys=True))
    return 0


def _prepare_record(record: dict[str, Any], *, source_path: str | Path | None) -> dict[str, Any]:
    site_name = _clean_optional_string(record.get("site_name"))
    commodities = normalize_commodities(record.get("commodities") or [])
    prepared = {
        "record_id": _record_id(site_name, source_path),
        "site_name": site_name,
        "latitude": _coerce_float(record.get("latitude")),
        "longitude": _coerce_float(record.get("longitude")),
        "commodities": commodities,
        "commod1": commodities[0] if commodities else None,
        "commod2": commodities[1] if len(commodities) > 1 else None,
        "grade": _coerce_float(record.get("grade")),
        "tonnage": _coerce_float(record.get("tonnage")),
        "source_url": str(source_path) if source_path else None,
        "confidence_score": _coerce_float(record.get("confidence_score")),
    }
    return prepared


def _merge_record(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key in ("latitude", "longitude", "grade", "tonnage"):
        if merged.get(key) in (None, "") and incoming.get(key) not in (None, ""):
            merged[key] = incoming[key]

    commodities = normalize_commodities(_as_list(merged.get("commodities")) + _as_list(incoming.get("commodities")))
    merged["commodities"] = commodities
    if len(str(incoming.get("site_name", ""))) > len(str(merged.get("site_name", ""))):
        merged["site_name"] = incoming["site_name"]
    return merged


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


def _is_valid_latitude(value: Any) -> bool:
    number = _coerce_float(value)
    return number is not None and -90.0 <= number <= 90.0


def _is_valid_longitude(value: Any) -> bool:
    number = _coerce_float(value)
    return number is not None and -180.0 <= number <= 180.0


def _model_to_dict(model: MineralDeposit) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


if __name__ == "__main__":
    raise SystemExit(main())
