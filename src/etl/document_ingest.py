"""Fault-tolerant geological document ingestion primitives.

This module keeps PDF parsing separate from LLM entity extraction so the ETL
pipeline can inspect document quality, provenance, extraction coverage, and OCR
routing decisions before any normalized mineral deposit records are produced.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

LOGGER = logging.getLogger(__name__)

TARGET_CHARS_PER_TOKEN = 4
MIN_TEXT_CHARS_PER_PAGE = 40
GIBBERISH_QUALITY_THRESHOLD = 0.45
TABLE_ROW_LIMIT = 50


@dataclass
class TableExtraction:
    """Structured table content extracted from a single page."""

    page_number: int
    table_index: int
    row_count: int
    column_count: int
    rows: list[list[str | None]] = field(default_factory=list)
    bbox: list[float] | None = None
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class PageExtraction:
    """Page-level extraction output with provenance and quality signals."""

    page_number: int
    method: str
    text: str = ""
    text_chars: int = 0
    text_sha1: str | None = None
    text_quality_score: float = 0.0
    width: float | None = None
    height: float | None = None
    rotation: int | None = None
    image_count: int = 0
    table_count: int = 0
    needs_ocr: bool = False
    ocr_attempted: bool = False
    ocr_confidence: float | None = None
    tables: list[TableExtraction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class DocumentChunk:
    """Deterministic narrative-text chunk with page provenance."""

    chunk_id: str
    chunk_index: int
    text: str
    text_sha1: str
    char_count: int
    page_numbers: list[int]
    page_start: int
    page_end: int


@dataclass
class DocumentIngestionResult:
    """Complete PDF ingestion result used by downstream LLM extraction."""

    source_path: str
    file_sha256: str | None
    document_type: str
    page_count: int
    pages: list[PageExtraction] = field(default_factory=list)
    chunks: list[DocumentChunk] = field(default_factory=list)
    tables: list[TableExtraction] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def raw_text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text.strip())

    def to_dict(self, *, include_text: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if not include_text:
            for page in payload["pages"]:
                page.pop("text", None)
            for chunk in payload["chunks"]:
                chunk.pop("text", None)
        return payload


def ingest_pdf_document(
    pdf_path: str | Path,
    *,
    enable_ocr: bool = True,
    ocr_language: str = "eng",
    max_tokens: int = 1500,
) -> DocumentIngestionResult:
    """Extract page text, table content, chunks, and observability metadata.

    The function is intentionally fault-tolerant: malformed files and failed
    pages are represented in the returned report instead of being silently
    discarded. OCR is attempted only for pages that look scanned or empty.
    """

    path = Path(pdf_path)
    file_sha256 = _file_sha256(path) if path.exists() else None
    if not path.exists():
        return DocumentIngestionResult(
            source_path=str(path),
            file_sha256=file_sha256,
            document_type="missing",
            page_count=0,
            errors=[f"file_not_found: {path}"],
            metrics={"page_count": 0, "error_count": 1, "warning_count": 0},
        )

    try:
        fitz = _require_fitz()
        document = fitz.open(path)
    except Exception as exc:
        LOGGER.warning("Unable to open PDF %s: %s", path, exc)
        return DocumentIngestionResult(
            source_path=str(path),
            file_sha256=file_sha256,
            document_type="malformed",
            page_count=0,
            errors=[f"pdf_open_failed: {exc}"],
            metrics={"page_count": 0, "error_count": 1, "warning_count": 0},
        )

    pages: list[PageExtraction] = []
    try:
        for index in range(int(document.page_count)):
            try:
                page = document.load_page(index)
                page_result = _extract_page(page, index + 1, enable_ocr=enable_ocr, ocr_language=ocr_language)
            except Exception as exc:
                LOGGER.warning("Failed to extract page %s from %s: %s", index + 1, path, exc)
                page_result = PageExtraction(
                    page_number=index + 1,
                    method="failed",
                    errors=[f"page_extract_failed: {exc}"],
                )
            pages.append(page_result)
    finally:
        document.close()

    tables = [table for page in pages for table in page.tables]
    chunks = chunk_pages(pages, max_tokens=max_tokens)
    document_type = classify_document(pages)
    warnings = _document_warnings(pages, document_type)
    errors = [error for page in pages for error in page.errors]
    metrics = build_metrics(pages, chunks, tables, document_type=document_type)
    metrics["file_sha256"] = file_sha256

    return DocumentIngestionResult(
        source_path=str(path),
        file_sha256=file_sha256,
        document_type=document_type,
        page_count=len(pages),
        pages=pages,
        chunks=chunks,
        tables=tables,
        metrics=metrics,
        warnings=warnings,
        errors=errors,
    )


def chunk_pages(pages: list[PageExtraction], *, max_tokens: int = 1500) -> list[DocumentChunk]:
    """Build deterministic chunks while preserving page-number provenance."""

    max_chars = max(500, max_tokens * TARGET_CHARS_PER_TOKEN)
    chunks: list[DocumentChunk] = []
    current_sentences: list[str] = []
    current_pages: list[int] = []

    def flush() -> None:
        if not current_sentences:
            return
        text = _clean_text(" ".join(current_sentences))
        if not text:
            current_sentences.clear()
            current_pages.clear()
            return
        page_numbers = sorted(set(current_pages))
        text_sha1 = _text_sha1(text)
        chunks.append(
            DocumentChunk(
                chunk_id=f"chunk-{len(chunks) + 1:04d}-p{page_numbers[0]:04d}-p{page_numbers[-1]:04d}-{text_sha1[:12]}",
                chunk_index=len(chunks),
                text=text,
                text_sha1=text_sha1,
                char_count=len(text),
                page_numbers=page_numbers,
                page_start=page_numbers[0],
                page_end=page_numbers[-1],
            )
        )
        current_sentences.clear()
        current_pages.clear()

    for page in pages:
        if not page.text.strip():
            continue
        for sentence in _split_sentences(page.text):
            if len(sentence) > max_chars:
                flush()
                for wrapped in _hard_wrap(sentence, max_chars):
                    current_sentences.append(wrapped)
                    current_pages.append(page.page_number)
                    flush()
                continue
            candidate = _clean_text(" ".join(current_sentences + [sentence]))
            if current_sentences and len(candidate) > max_chars:
                flush()
            current_sentences.append(sentence)
            current_pages.append(page.page_number)
    flush()
    return chunks


def classify_document(pages: list[PageExtraction]) -> str:
    if not pages:
        return "empty"
    failed = sum(1 for page in pages if page.method == "failed")
    text_native = sum(1 for page in pages if page.method == "text")
    ocr = sum(1 for page in pages if page.method == "ocr")
    needs_ocr = sum(1 for page in pages if page.needs_ocr)
    empty = sum(1 for page in pages if page.method in {"empty", "ocr_unavailable", "ocr_failed", "ocr_disabled"})
    if failed == len(pages):
        return "malformed"
    if text_native == len(pages):
        return "text-native"
    if text_native == 0 and (ocr or needs_ocr or empty):
        return "scanned"
    return "hybrid"


def build_metrics(
    pages: list[PageExtraction],
    chunks: list[DocumentChunk],
    tables: list[TableExtraction],
    *,
    document_type: str,
) -> dict[str, Any]:
    page_count = len(pages)
    text_pages = sum(1 for page in pages if page.text_chars > 0)
    failed_pages = sum(1 for page in pages if page.errors)
    pages_needing_ocr = sum(1 for page in pages if page.needs_ocr)
    ocr_attempted = sum(1 for page in pages if page.ocr_attempted)
    ocr_succeeded = sum(1 for page in pages if page.method == "ocr" and page.text_chars > 0)
    warning_count = sum(len(page.warnings) for page in pages)
    error_count = sum(len(page.errors) for page in pages)
    quality_scores = [page.text_quality_score for page in pages if page.text_chars > 0]
    return {
        "document_type": document_type,
        "page_count": page_count,
        "text_pages": text_pages,
        "text_coverage_percent": round((text_pages / page_count * 100.0), 2) if page_count else 0.0,
        "raw_text_characters": sum(page.text_chars for page in pages),
        "failed_pages": failed_pages,
        "pages_needing_ocr": pages_needing_ocr,
        "ocr_attempted_pages": ocr_attempted,
        "ocr_succeeded_pages": ocr_succeeded,
        "table_count": len(tables),
        "chunk_count": len(chunks),
        "average_text_quality": round(mean(quality_scores), 3) if quality_scores else 0.0,
        "warning_count": warning_count,
        "error_count": error_count,
    }


def write_ingestion_artifacts(result: DocumentIngestionResult, output_dir: str | Path) -> dict[str, str]:
    """Persist raw extraction artifacts next to a machine-readable report."""

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "document_ingestion_report.json"
    raw_text_path = output / "raw_text.txt"
    chunks_path = output / "chunks.jsonl"
    tables_path = output / "tables.jsonl"

    report_path.write_text(json.dumps(result.to_dict(include_text=True), indent=2, sort_keys=True), encoding="utf-8")
    raw_text_path.write_text(result.raw_text, encoding="utf-8")
    chunks_path.write_text("\n".join(json.dumps(asdict(chunk), sort_keys=True) for chunk in result.chunks), encoding="utf-8")
    tables_path.write_text("\n".join(json.dumps(asdict(table), sort_keys=True) for table in result.tables), encoding="utf-8")
    return {
        "report": str(report_path),
        "raw_text": str(raw_text_path),
        "chunks": str(chunks_path),
        "tables": str(tables_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect a PDF and produce reliable document-ingestion artifacts.")
    parser.add_argument("--input", "-i", required=True, help="Path to a geological PDF report.")
    parser.add_argument("--artifacts-dir", help="Directory for raw text, chunk, table, and report artifacts.")
    parser.add_argument("--report", help="Optional JSON report path. Use --artifacts-dir for the full artifact bundle.")
    parser.add_argument("--max-tokens", type=int, default=1500, help="Approximate max chunk size for downstream LLM extraction.")
    parser.add_argument("--ocr-language", default="eng", help="Tesseract language code when OCR fallback is available.")
    parser.add_argument("--no-ocr", action="store_true", help="Disable OCR fallback routing.")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s:%(name)s:%(message)s")
    result = ingest_pdf_document(
        args.input,
        enable_ocr=not args.no_ocr,
        ocr_language=args.ocr_language,
        max_tokens=args.max_tokens,
    )
    if args.artifacts_dir:
        paths = write_ingestion_artifacts(result, args.artifacts_dir)
        LOGGER.info("Wrote document-ingestion artifacts: %s", paths)
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result.to_dict(include_text=True), indent=2, sort_keys=True), encoding="utf-8")
        LOGGER.info("Wrote document-ingestion report to %s", report_path)
    print(json.dumps(result.metrics, indent=2, sort_keys=True))
    return 1 if result.document_type in {"missing", "malformed"} else 0


def _extract_page(page: Any, page_number: int, *, enable_ocr: bool, ocr_language: str) -> PageExtraction:
    warnings: list[str] = []
    errors: list[str] = []
    rotation = int(getattr(page, "rotation", 0) or 0)
    if rotation % 360:
        warnings.append(f"rotated_page:{rotation}")

    rect = getattr(page, "rect", None)
    width = round(float(rect.width), 2) if rect is not None else None
    height = round(float(rect.height), 2) if rect is not None else None

    try:
        raw_text = page.get_text("text") or ""
    except UnicodeError as exc:
        raw_text = ""
        warnings.append(f"text_encoding_error:{exc}")
    except Exception as exc:
        raw_text = ""
        errors.append(f"text_extract_failed:{exc}")

    text = _clean_text(raw_text)
    image_count = _image_count(page, warnings)
    tables, table_warnings = _extract_tables(page, page_number)
    warnings.extend(table_warnings)
    warnings.extend(_layout_warnings(page, tables, width, height))

    needs_ocr = len(text) < MIN_TEXT_CHARS_PER_PAGE and image_count > 0
    method = "text" if text else "empty"
    ocr_attempted = False
    ocr_confidence: float | None = None

    if needs_ocr:
        warnings.append("page_has_low_text_and_images:ocr_recommended")
        if enable_ocr:
            ocr_attempted = True
            ocr_text, ocr_confidence, ocr_warnings = _try_ocr_page(page, language=ocr_language)
            warnings.extend(ocr_warnings)
            if ocr_text.strip():
                text = _clean_text(ocr_text)
                method = "ocr"
            elif any(warning.startswith("ocr_backend_unavailable") for warning in ocr_warnings):
                method = "ocr_unavailable"
            else:
                method = "ocr_failed"
        else:
            method = "ocr_disabled"

    quality = text_quality_score(text)
    if text and quality < GIBBERISH_QUALITY_THRESHOLD:
        warnings.append(f"low_text_quality:{quality}")

    return PageExtraction(
        page_number=page_number,
        method=method,
        text=text,
        text_chars=len(text),
        text_sha1=_text_sha1(text) if text else None,
        text_quality_score=quality,
        width=width,
        height=height,
        rotation=rotation,
        image_count=image_count,
        table_count=len(tables),
        needs_ocr=needs_ocr,
        ocr_attempted=ocr_attempted,
        ocr_confidence=ocr_confidence,
        tables=tables,
        warnings=warnings,
        errors=errors,
    )


def _extract_tables(page: Any, page_number: int) -> tuple[list[TableExtraction], list[str]]:
    warnings: list[str] = []
    finder = getattr(page, "find_tables", None)
    if not callable(finder):
        return [], ["table_extraction_unavailable:pymupdf_find_tables_missing"]
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = finder()
        raw_tables = getattr(result, "tables", []) or []
    except Exception as exc:
        return [], [f"table_extract_failed:{exc}"]

    tables: list[TableExtraction] = []
    for index, table in enumerate(raw_tables):
        try:
            rows = table.extract() or []
            normalized_rows = [[_clean_cell(cell) for cell in row] for row in rows]
            column_count = max((len(row) for row in normalized_rows), default=0)
            bbox = [round(float(value), 2) for value in getattr(table, "bbox", [])] or None
            truncated = len(normalized_rows) > TABLE_ROW_LIMIT
            tables.append(
                TableExtraction(
                    page_number=page_number,
                    table_index=index,
                    row_count=len(normalized_rows),
                    column_count=column_count,
                    rows=normalized_rows[:TABLE_ROW_LIMIT],
                    bbox=bbox,
                    truncated=truncated,
                    warnings=["table_rows_truncated"] if truncated else [],
                )
            )
        except Exception as exc:
            warnings.append(f"table_{index}_malformed:{exc}")
    return tables, warnings


def _layout_warnings(page: Any, tables: list[TableExtraction], width: float | None, height: float | None) -> list[str]:
    warnings: list[str] = []
    if width:
        try:
            blocks = page.get_text("blocks") or []
        except Exception as exc:
            return [f"layout_inspection_failed:{exc}"]
        text_blocks = [block for block in blocks if len(block) >= 5 and str(block[4]).strip()]
        left_blocks = 0
        right_blocks = 0
        for block in text_blocks:
            x0, _, x1, *_ = block
            center = (float(x0) + float(x1)) / 2.0
            if center < width * 0.45:
                left_blocks += 1
            elif center > width * 0.55:
                right_blocks += 1
        if left_blocks >= 3 and right_blocks >= 3:
            warnings.append("possible_multi_column_layout")
    if height:
        for table in tables:
            if table.bbox and len(table.bbox) >= 4 and float(table.bbox[3]) > height * 0.88:
                warnings.append(f"table_{table.table_index}_near_page_bottom:possible_split_table")
    return warnings


def _try_ocr_page(page: Any, *, language: str) -> tuple[str, float | None, list[str]]:
    warnings: list[str] = []
    try:
        import pytesseract  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        return "", None, ["ocr_backend_unavailable:install pytesseract, pillow, and the tesseract binary"]

    try:
        fitz = _require_fitz()
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = Image.open(io.BytesIO(pixmap.tobytes("png")))
        data = pytesseract.image_to_data(image, lang=language, output_type=pytesseract.Output.DICT)
        words = [word.strip() for word in data.get("text", []) if str(word).strip()]
        confidences: list[float] = []
        for value in data.get("conf", []):
            try:
                confidence = float(value)
            except (TypeError, ValueError):
                continue
            if confidence >= 0:
                confidences.append(confidence / 100.0)
        text = " ".join(words).strip()
        if not text:
            text = pytesseract.image_to_string(image, lang=language).strip()
        avg_confidence = round(mean(confidences), 3) if confidences else None
        if avg_confidence is not None and avg_confidence < 0.35:
            warnings.append(f"low_ocr_confidence:{avg_confidence}")
        if text and text_quality_score(text) < GIBBERISH_QUALITY_THRESHOLD:
            warnings.append("ocr_text_quality_low")
        return text, avg_confidence, warnings
    except Exception as exc:
        return "", None, [f"ocr_failed:{exc}"]


def text_quality_score(text: str) -> float:
    """Return a lightweight 0-1 quality score for extracted or OCR text."""

    if not text:
        return 0.0
    printable = sum(1 for char in text if char.isprintable() or char.isspace()) / len(text)
    alpha_numeric = sum(1 for char in text if char.isalnum() or char.isspace() or char in ".,;:%/()[]+-") / len(text)
    words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text)
    word_density = min(len(words) / max(len(text) / 12.0, 1.0), 1.0)
    replacement_penalty = min(text.count("\ufffd") / max(len(text), 1), 0.5)
    score = 0.4 * printable + 0.35 * alpha_numeric + 0.25 * word_density - replacement_penalty
    return round(max(0.0, min(score, 1.0)), 3)


def _document_warnings(pages: list[PageExtraction], document_type: str) -> list[str]:
    warnings: list[str] = []
    if document_type in {"scanned", "hybrid"} and any(page.needs_ocr and not page.ocr_attempted for page in pages):
        warnings.append("ocr_needed_but_not_attempted")
    if any(page.method == "ocr_unavailable" for page in pages):
        warnings.append("ocr_backend_unavailable")
    if any(page.method == "failed" for page in pages):
        warnings.append("one_or_more_pages_failed")
    if any(page.table_count for page in pages):
        warnings.append("tables_detected:review_table_artifacts_for_split_or_malformed_rows")
    return warnings


def _image_count(page: Any, warnings: list[str]) -> int:
    try:
        return len(page.get_images(full=True) or [])
    except Exception as exc:
        warnings.append(f"image_inspection_failed:{exc}")
        return 0


def _split_sentences(text: str) -> list[str]:
    cleaned = _clean_text(text)
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", cleaned) if sentence.strip()]


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


def _clean_text(text: str) -> str:
    text = text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_cell(value: Any) -> str | None:
    if value is None:
        return None
    text = _clean_text(str(value))
    return text or None


def _text_sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_fitz():
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - depends on deployment env
        raise RuntimeError("PDF ingestion requires pymupdf. Install requirements.txt first.") from exc
    return fitz


if __name__ == "__main__":
    raise SystemExit(main())
