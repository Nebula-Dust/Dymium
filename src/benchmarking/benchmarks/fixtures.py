"""Reproducible benchmark fixture generation."""

from __future__ import annotations

from pathlib import Path


CSV_FIXTURES = {
    "mrds_clean.csv": "dep_id,name,code_list,latitude,longitude,url\n100,Moonshine Prospect,AU CU,55.14445,-132.05371,https://example.test/mrds/100\n",
    "malformed_coordinates.csv": "dep_id,name,code_list,latitude,longitude\nBAD1,Bad Coordinate Mine,AU,999,-200\nPARTIAL,Partial Coordinate Mine,CU,40.0,\n",
    "conflicting_commodities.csv": "dep_id,name,code_list,latitude,longitude\nM1,Bayan Obo,REE NB,41.8,109.9\nG1,Bayan Obo,FE,41.801,109.901\n",
    "duplicate_deposits.csv": "dep_id,name,code_list,latitude,longitude\nD1,Duplicate Mine,AU,40.0,-105.0\nD2,Duplicate Mine,CU,40.0,-105.0\n",
    "incomplete_metadata.csv": "sample_id,rock_type,extra_field\nP1,basalt,kept\n",
    "schema_drift_georoc.csv": "sample name,volcano,analytes,latitude_decimal,longitude_decimal,rock name,new_unseen_field\nS1,Test Volcano,REE,10.0,20.0,carbonatite,drift\n",
}


def create_benchmark_fixtures(output_dir: str | Path) -> dict[str, str]:
    """Create small deterministic benchmark fixtures and return their paths."""

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for name, content in CSV_FIXTURES.items():
        path = output / name
        path.write_text(content, encoding="utf-8")
        paths[name] = str(path)
    corrupted_pdf = output / "corrupted_report.pdf"
    corrupted_pdf.write_bytes(b"%PDF-1.4\nthis is intentionally malformed for benchmark regression\n")
    paths["corrupted_report.pdf"] = str(corrupted_pdf)
    clean_pdf = output / "clean_report.pdf"
    if _write_clean_pdf(clean_pdf):
        paths["clean_report.pdf"] = str(clean_pdf)
    else:
        clean_pdf.write_text("Clean benchmark report placeholder: Alpha Mine contains gold at 40.0 -105.0", encoding="utf-8")
        paths["clean_report.pdf"] = str(clean_pdf)
    ocr_heavy = output / "ocr_heavy_placeholder.pdf"
    ocr_heavy.write_bytes(b"%PDF-1.4\n% OCR-heavy placeholder: intentionally lacks extractable page objects\n")
    paths["ocr_heavy_placeholder.pdf"] = str(ocr_heavy)
    return paths


def _write_clean_pdf(path: Path) -> bool:
    try:
        import fitz
    except Exception:
        return False
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Clean benchmark report. Alpha Mine contains gold and copper. Location 40.0, -105.0.")
    doc.save(path)
    doc.close()
    return True
