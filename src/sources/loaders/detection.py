"""Source capability detection for geological/geospatial inputs."""
from __future__ import annotations
import csv, hashlib, io, zipfile
from pathlib import Path
from urllib.parse import urlparse
from src.sources.schemas import SourceCapability, SourceDescriptor
TABULAR_SUFFIXES = {".csv": "csv", ".tsv": "tsv", ".txt": "txt"}
GEOSPATIAL_SUFFIXES = {".shp": "shp", ".geojson": "geojson", ".gpkg": "gpkg", ".json": "geojson"}
ARCHIVE_SUFFIXES = {".zip": "zip"}
PDF_SUFFIXES = {".pdf": "pdf"}
def detect_source(source: str | Path, *, source_name: str | None = None, inspect_pdf: bool = True) -> SourceDescriptor:
    text_source = str(source)
    parsed = urlparse(text_source)
    is_remote = parsed.scheme in {"http", "https"}
    suffix = Path(parsed.path if is_remote else text_source).suffix.lower()
    descriptor = SourceDescriptor(source_name=source_name, path=None if is_remote else text_source, uri=text_source if is_remote else None, exists=is_remote, file_format=_format_from_suffix(suffix), capabilities=SourceCapability(remote_fetch=is_remote))
    if is_remote:
        descriptor.source_kind = "web_archive" if suffix in ARCHIVE_SUFFIXES else "unknown"
        descriptor.capabilities.remote_fetch = True
        return descriptor
    path = Path(text_source)
    descriptor.exists = path.exists()
    if not path.exists():
        descriptor.malformed = True
        descriptor.schema_warnings.append("source_missing")
        return descriptor
    descriptor.size_bytes = path.stat().st_size
    descriptor.checksum_sha256 = file_sha256(path)
    if suffix in TABULAR_SUFFIXES:
        _inspect_tabular(path, descriptor)
    elif suffix in GEOSPATIAL_SUFFIXES:
        _inspect_geospatial(path, descriptor)
    elif suffix in PDF_SUFFIXES:
        _inspect_pdf(path, descriptor, inspect_pdf=inspect_pdf)
    elif suffix in ARCHIVE_SUFFIXES:
        _inspect_archive(path, descriptor)
    else:
        descriptor.source_kind = "unknown"
        descriptor.schema_warnings.append(f"unsupported_extension:{suffix or 'none'}")
    return descriptor
def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
def _inspect_tabular(path: Path, descriptor: SourceDescriptor) -> None:
    descriptor.source_kind = "structured_dataset"
    descriptor.capabilities.structured_records = True
    descriptor.capabilities.canonical_mapping = True
    raw = path.read_bytes()[:65536]
    descriptor.encoding = _detect_encoding(raw)
    sample = raw.decode(descriptor.encoding or "utf-8", errors="replace")
    delimiter = _sniff_delimiter(sample, path.suffix.lower())
    descriptor.delimiter = delimiter
    try:
        reader = csv.reader(io.StringIO(sample), delimiter=delimiter)
        header = next(reader, [])
        descriptor.schema_fields = [field.strip() for field in header if field.strip()]
    except Exception as exc:
        descriptor.malformed = True
        descriptor.schema_warnings.append(f"tabular_header_parse_failed:{exc}")
    normalized = {field.lower().replace(" ", "_") for field in descriptor.schema_fields}
    descriptor.geometry_presence = bool(({"latitude", "lat", "y"} & normalized) and ({"longitude", "lon", "long", "x"} & normalized)) or "geometry" in normalized
def _inspect_geospatial(path: Path, descriptor: SourceDescriptor) -> None:
    descriptor.source_kind = "geospatial_layer"
    descriptor.geometry_presence = True
    descriptor.capabilities.geospatial_geometry = True
    try:
        import geopandas as gpd  # type: ignore
        frame = gpd.read_file(path, rows=1)
        descriptor.schema_fields = [str(column) for column in frame.columns]
        descriptor.crs = str(frame.crs) if frame.crs is not None else None
        if descriptor.crs is None:
            descriptor.schema_warnings.append("missing_crs")
    except Exception as exc:
        descriptor.schema_warnings.append(f"geospatial_light_inspection_failed:{exc}")
def _inspect_pdf(path: Path, descriptor: SourceDescriptor, *, inspect_pdf: bool) -> None:
    descriptor.source_kind = "pdf"
    descriptor.capabilities.document_text = True
    if not inspect_pdf:
        return
    try:
        from src.etl.document_ingest import ingest_pdf_document
        result = ingest_pdf_document(path, enable_ocr=False, max_tokens=200)
        descriptor.malformed = result.document_type in {"missing", "malformed"}
        descriptor.digital_pdf = result.document_type in {"text-native", "hybrid"}
        descriptor.scanned_pdf = result.document_type == "scanned" or bool(result.metrics.get("pages_needing_ocr"))
        descriptor.capabilities.ocr_required = bool(descriptor.scanned_pdf)
        descriptor.capabilities.tabular_report = bool(result.metrics.get("table_count"))
        if descriptor.scanned_pdf:
            descriptor.source_kind = "scanned_pdf"
        descriptor.schema_warnings.extend(result.errors or [])
        descriptor.schema_warnings.extend(result.warnings or [])
    except Exception as exc:
        descriptor.malformed = True
        descriptor.schema_warnings.append(f"pdf_detection_failed:{exc}")
def _inspect_archive(path: Path, descriptor: SourceDescriptor) -> None:
    descriptor.source_kind = "archive"
    descriptor.capabilities.archive_members = True
    try:
        with zipfile.ZipFile(path) as archive:
            members = []
            nested = False
            for member in archive.infolist():
                suffix = Path(member.filename).suffix.lower()
                if suffix in ARCHIVE_SUFFIXES:
                    nested = True
                members.append({"name": member.filename, "size": member.file_size, "file_format": _format_from_suffix(suffix)})
            descriptor.archive_members = members
            descriptor.nested_archive = nested
            descriptor.geometry_presence = any(item.get("file_format") in {"shp", "geojson", "gpkg"} for item in members)
    except Exception as exc:
        descriptor.malformed = True
        descriptor.schema_warnings.append(f"archive_inspection_failed:{exc}")
def _format_from_suffix(suffix: str) -> str | None:
    return TABULAR_SUFFIXES.get(suffix) or GEOSPATIAL_SUFFIXES.get(suffix) or PDF_SUFFIXES.get(suffix) or ARCHIVE_SUFFIXES.get(suffix)
def _detect_encoding(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "utf-8"
def _sniff_delimiter(sample: str, suffix: str) -> str:
    if suffix == ".tsv":
        return "	"
    lines = sample.splitlines()
    try:
        return csv.Sniffer().sniff(sample[:4096], delimiters=",	|;").delimiter
    except Exception:
        return "	" if lines and "	" in lines[0] else ","
