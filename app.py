"""Streamlit demo for the Dymium geospatial ETL pipeline."""

from __future__ import annotations

import contextlib
import io
import logging
from pathlib import Path
from typing import Callable

import geopandas as gpd
import streamlit as st

from src.etl.fusion import build_unified_dataset, export_geoparquet
from src.etl.geology import enrich_with_geology, export_enriched
from src.etl.pdf_ingest import extract_text_from_pdf, process_pdf
from ui.components import render_commodity_chart, render_distribution_chart, render_map, render_metrics, render_workflow
from ui.helpers import (
    available_commodities,
    available_values,
    dataframe_to_csv_bytes,
    display_dataframe,
    filter_dataframe,
    geodataframe_to_parquet_bytes,
    load_geoparquet,
    materialize_uploaded_file,
    materialize_vector_upload,
    source_counts,
    summarize_dataset,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_UNIFIED_PATH = Path("out/unified.parquet")
DEFAULT_ENRICHED_PATH = Path("out/enriched.parquet")
DEFAULT_MRDS_PATH = Path("rdbms-tab/MRDS.txt")
DEFAULT_PDF_PATH = Path("test_docs/minerals-10-00965-v3.pdf")
DEFAULT_GEOLOGY_PATH = Path("geological_data/colorado_geology.shp")

SAMPLE_RAW_TEXT = (
    "The Creede District in Colorado is a silver-lead mining district near latitude 37.8 and longitude -106.9. "
    "Historical veins contain silver and lead mineralization. The Silver Bell mine contains about 2.4 million tons "
    "grading 0.8 percent Cu at latitude 32.38 and longitude -111.50."
)
SAMPLE_JSON = [
    {"site_name": "Creede District", "latitude": 37.8, "longitude": -106.9, "commodities": ["silver", "lead"], "confidence_score": 0.9},
    {"site_name": "Silver Bell mine", "latitude": 32.38, "longitude": -111.50, "commodities": ["copper"], "grade": 0.8},
]

st.set_page_config(page_title="Dymium ETL Demo", page_icon="D", layout="wide")


def main() -> None:
    """Render the Dymium technical demo."""

    _init_state()
    _inject_css()
    st.title("Dymium geospatial ETL demo")
    st.caption("Open-source ETL prototype for mineral deposit PDFs, MRDS records, geology layers, and ML-ready GeoParquet outputs.")

    sidebar = _render_sidebar()
    dataset = _load_active_dataset(sidebar["active_dataset_path"])
    filtered = _render_global_filters(dataset) if dataset is not None else None

    tabs = st.tabs(["Overview", "Map Explorer", "Dataset", "Geology", "Logs"])
    with tabs[0]:
        _overview_tab(dataset, filtered)
    with tabs[1]:
        _map_tab(filtered)
    with tabs[2]:
        _dataset_tab(filtered)
    with tabs[3]:
        _geology_tab(filtered)
    with tabs[4]:
        _logs_tab(dataset)


def _render_sidebar() -> dict[str, Path | None]:
    st.sidebar.header("Dymium pipeline")
    st.sidebar.caption("Run ETL steps or inspect existing GeoParquet outputs.")

    enriched_default = DEFAULT_ENRICHED_PATH.exists()
    view_choice = st.sidebar.radio(
        "Dataset view",
        ["Enriched output", "Unified output"],
        index=0 if enriched_default else 1,
    )

    with st.sidebar.expander("Output paths", expanded=False):
        unified_path = Path(st.text_input("Unified GeoParquet", value=str(DEFAULT_UNIFIED_PATH)))
        enriched_path = Path(st.text_input("Enriched GeoParquet", value=str(DEFAULT_ENRICHED_PATH)))

    with st.sidebar.expander("Pipeline inputs", expanded=False):
        mrds_path = Path(st.text_input("MRDS CSV/TSV", value=str(DEFAULT_MRDS_PATH)))
        pdf_path_text = st.text_input("PDF path", value=str(DEFAULT_PDF_PATH) if DEFAULT_PDF_PATH.exists() else "")
        geology_path_text = st.text_input("Shapefile / GeoPackage path", value=str(DEFAULT_GEOLOGY_PATH) if DEFAULT_GEOLOGY_PATH.exists() else "")

    with st.sidebar.expander("Uploads", expanded=False):
        uploaded_pdf = st.file_uploader("PDF report", type=["pdf"])
        uploaded_vector = st.file_uploader(
            "Shapefile sidecars or GeoPackage",
            type=["shp", "shx", "dbf", "prj", "cpg", "gpkg", "geojson", "json"],
            accept_multiple_files=True,
        )

    with st.sidebar.expander("Run pipeline", expanded=True):
        if st.button("Extract PDF entities", use_container_width=True):
            pdf_path = materialize_uploaded_file(uploaded_pdf, ".pdf") or _optional_path(pdf_path_text)
            _run_pipeline_step("PDF extraction", lambda: _run_pdf_extraction(pdf_path))

        if st.button("Fuse MRDS + PDF", use_container_width=True):
            pdf_path = materialize_uploaded_file(uploaded_pdf, ".pdf") or _optional_path(pdf_path_text)
            _run_pipeline_step("Dataset fusion", lambda: _run_dataset_fusion(mrds_path, pdf_path, unified_path))

        if st.button("Join geology", use_container_width=True):
            vector_path = materialize_vector_upload(uploaded_vector) or _optional_path(geology_path_text)
            _run_pipeline_step("Geology enrichment", lambda: _run_geology_enrichment(unified_path, vector_path, enriched_path))

    active_dataset_path = enriched_path if view_choice == "Enriched output" else unified_path
    st.sidebar.divider()
    st.sidebar.caption("Active dataset")
    st.sidebar.code(str(active_dataset_path), language=None)
    if view_choice == "Enriched output" and "colorado" in str(DEFAULT_GEOLOGY_PATH).lower():
        st.sidebar.caption("Current geology context uses a regional Colorado subset.")
    return {"active_dataset_path": active_dataset_path}


@st.cache_data(show_spinner=False)
def _load_dataset_cached(path: str) -> gpd.GeoDataFrame:
    return load_geoparquet(path)


def _load_active_dataset(path: Path | None) -> gpd.GeoDataFrame | None:
    if path is None or not path.exists():
        st.info("Run the pipeline or point the sidebar at an existing GeoParquet output to begin.")
        return None
    try:
        return _load_dataset_cached(str(path))
    except Exception as exc:
        st.error(f"Could not load `{path}`: {exc}")
        _append_log(f"Dataset load failed for {path}: {exc}")
        return None


def _render_global_filters(dataset: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    with st.expander("Filters", expanded=False):
        col1, col2, col3 = st.columns(3)
        commodities = col1.multiselect("Commodity", available_commodities(dataset), default=[])
        ages = col2.multiselect("Geologic age", available_values(dataset, "geologic_age"), default=[])
        lithologies = col3.multiselect("Lithology", available_values(dataset, "lithology"), default=[])
    return filter_dataframe(dataset, commodities, ages, lithologies)


def _overview_tab(dataset: gpd.GeoDataFrame | None, filtered: gpd.GeoDataFrame | None) -> None:
    if dataset is None or filtered is None:
        return
    st.subheader("System overview")
    render_metrics(dataset)
    render_workflow()

    st.markdown("**Deposit map**")
    render_map(filtered, max_points=9000, height=640)

    left, right = st.columns([0.95, 1.05])
    with left:
        st.markdown("**Commodity distribution**")
        render_commodity_chart(filtered, height=310)
    with right:
        _sample_extraction_panel()


def _sample_extraction_panel() -> None:
    st.markdown("**Sample PDF extraction**")
    raw_text = st.session_state.get("last_pdf_text") or SAMPLE_RAW_TEXT
    records = st.session_state.get("last_pdf_records") or SAMPLE_JSON
    col1, col2 = st.columns(2)
    with col1:
        st.caption("Raw geological text")
        st.text_area("Raw geological text", value=raw_text, height=210, label_visibility="collapsed")
    with col2:
        st.caption("Structured extraction")
        st.json(records[:3])


def _map_tab(dataset: gpd.GeoDataFrame | None) -> None:
    st.subheader("Map explorer")
    if dataset is None:
        return
    col1, col2 = st.columns([0.28, 0.72])
    with col1:
        max_points = st.slider("Rendered points", min_value=1000, max_value=50000, value=12000, step=1000)
        st.caption("Sampling keeps the browser responsive while preserving geographic spread.")
        render_metrics(dataset, compact=True, vertical=True)
    with col2:
        render_map(dataset, max_points=max_points, height=720)


def _dataset_tab(dataset: gpd.GeoDataFrame | None) -> None:
    st.subheader("Unified dataset")
    if dataset is None:
        return
    st.caption(f"Showing {len(dataset):,} filtered records.")
    compact_columns = st.toggle("Compact columns", value=True, help="Hide geometry and long source URL fields in the preview only. Downloads still include the full dataset.")
    preview = display_dataframe(dataset, compact=compact_columns)
    st.dataframe(preview, use_container_width=True, hide_index=True, height=460)
    with st.expander("Column details", expanded=False):
        st.write(list(dataset.columns))
        if compact_columns:
            hidden = [column for column in dataset.columns if column not in preview.columns]
            st.caption(f"Hidden from preview: {', '.join(hidden) if hidden else 'none'}")
    col1, col2 = st.columns(2)
    col1.download_button(
        "Download CSV",
        data=dataframe_to_csv_bytes(dataset.drop(columns=["geometry"], errors="ignore")),
        file_name="dymium_dataset.csv",
        mime="text/csv",
        use_container_width=True,
    )
    col2.download_button(
        "Download GeoParquet",
        data=geodataframe_to_parquet_bytes(dataset),
        file_name="dymium_dataset.parquet",
        mime="application/octet-stream",
        use_container_width=True,
    )


def _geology_tab(dataset: gpd.GeoDataFrame | None) -> None:
    st.subheader("Geology coverage")
    if dataset is None:
        return
    metrics = summarize_dataset(dataset)
    render_metrics(dataset)
    if metrics["matched_percent"] < 5:
        st.info("Low match percentage is expected when the selected geology layer covers only a regional subset, such as Colorado, while the deposit dataset is global.")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Lithology distribution**")
        render_distribution_chart(dataset, "lithology", "Lithology", height=320)
    with col2:
        st.markdown("**Geologic age distribution**")
        render_distribution_chart(dataset, "geologic_age", "Geologic age", height=320)


def _logs_tab(dataset: gpd.GeoDataFrame | None) -> None:
    st.subheader("Pipeline logs and validation notes")
    if dataset is not None:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Source counts**")
            st.dataframe(source_counts(dataset), use_container_width=True, hide_index=True, height=170)
        with col2:
            metrics = summarize_dataset(dataset)
            st.markdown("**Current dataset checks**")
            st.write(
                {
                    "rows": metrics["total_deposits"],
                    "coordinates": metrics["rows_with_coordinates"],
                    "geology_matches": metrics["matched_geology"],
                }
            )
    logs = st.session_state.get("logs", [])
    if not logs:
        st.caption("No pipeline steps have been run in this browser session yet. Existing GeoParquet outputs are loaded directly from disk.")
    st.text_area("Pipeline events", value="\n".join(logs), height=260)


def _run_pdf_extraction(pdf_path: Path | None) -> None:
    if pdf_path is None or not pdf_path.exists():
        raise FileNotFoundError("Provide an uploaded PDF or a valid PDF path.")
    st.session_state["last_pdf_text"] = extract_text_from_pdf(pdf_path)[:1400]
    records = process_pdf(pdf_path)
    st.session_state["last_pdf_records"] = [_model_to_dict(record) for record in records]
    _append_log(f"PDF extraction produced {len(records)} deposit records from {pdf_path}.")


def _run_dataset_fusion(mrds_path: Path, pdf_path: Path | None, output_path: Path) -> None:
    if pdf_path is None or not pdf_path.exists():
        raise FileNotFoundError("Provide an uploaded PDF or a valid PDF path.")
    unified = build_unified_dataset(mrds_path, pdf_path)
    export_geoparquet(unified, output_path)
    _load_dataset_cached.clear()
    _append_log(f"Fusion wrote {len(unified)} records to {output_path}. Counts: {unified.attrs.get('match_counts', {})}")


def _run_geology_enrichment(input_path: Path, vector_path: Path | None, output_path: Path) -> None:
    if not input_path.exists():
        raise FileNotFoundError(f"Unified dataset not found: {input_path}")
    if vector_path is None or not vector_path.exists():
        raise FileNotFoundError("Provide uploaded shapefile/GeoPackage files or a valid vector path.")
    enriched = enrich_with_geology(input_path, vector_path)
    export_enriched(enriched, output_path)
    _load_dataset_cached.clear()
    matched = int(enriched["geologic_unit"].notna().sum()) if "geologic_unit" in enriched else 0
    _append_log(f"Geology enrichment wrote {len(enriched)} records to {output_path}; matched {matched}.")


def _run_pipeline_step(label: str, callback: Callable[[], None]) -> None:
    buffer = io.StringIO()
    try:
        with st.spinner(f"Running {label}..."), contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            callback()
    except Exception as exc:
        _append_log(f"{label} failed: {exc}")
        if buffer.getvalue():
            _append_log(buffer.getvalue().strip())
        st.sidebar.error(f"{label} failed: {exc}")
        return
    if buffer.getvalue():
        _append_log(buffer.getvalue().strip())
    st.sidebar.success(f"{label} complete")


def _optional_path(value: str) -> Path | None:
    text = value.strip()
    return Path(text) if text else None


def _append_log(message: str) -> None:
    st.session_state.setdefault("logs", []).append(message)


def _init_state() -> None:
    st.session_state.setdefault("logs", [])
    st.session_state.setdefault("last_pdf_records", [])
    st.session_state.setdefault("last_pdf_text", "")


def _model_to_dict(model: object) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    return dict(model)


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 3.1rem; padding-bottom: 1.2rem; max-width: 1500px; }
        [data-testid="stSidebar"] { background: #eef2f6; }
        [data-testid="stMetric"] { background: #ffffff; border: 1px solid #e7eaf0; border-radius: 8px; padding: 0.65rem 0.85rem; }
        [data-testid="stMetricLabel"] { color: #5f6877; }
        .stTabs [data-baseweb="tab-list"] { gap: 0.2rem; border-bottom: 1px solid #e7eaf0; }
        .stTabs [data-baseweb="tab"] { padding: 0.4rem 0.75rem; height: 2.2rem; }
        .workflow-strip { display: flex; align-items: stretch; gap: 0.5rem; margin: 1rem 0 1.05rem; }
        .workflow-card { flex: 1; border: 1px solid #e3e7ed; background: #fbfcfe; border-radius: 8px; padding: 0.75rem 0.85rem; }
        .workflow-card strong { display: block; color: #2f3542; font-size: 0.95rem; }
        .workflow-card span { color: #697386; font-size: 0.78rem; }
        .workflow-card.emphasis { border-color: #f05a5a; background: #fff7f7; }
        .workflow-arrow { display: flex; align-items: center; color: #8a94a6; font-weight: 700; }
        div[data-testid="stExpander"] { border-color: #e7eaf0; }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
