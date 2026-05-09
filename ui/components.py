"""Reusable Streamlit rendering components for the Dymium demo."""

from __future__ import annotations

import altair as alt
import pandas as pd
import pydeck as pdk
import streamlit as st

from .helpers import commodity_counts, prepare_map_dataframe, summarize_dataset


WORKFLOW_STEPS = [
    ("PDFs", "LLM entity extraction"),
    ("MRDS CSV", "Structured deposit records"),
    ("Shapefiles", "Geologic context"),
    ("Unified GeoParquet", "ML-ready spatial dataset"),
]


def render_metrics(dataframe: pd.DataFrame, *, compact: bool = False, vertical: bool = False) -> None:
    """Render top-line dataset metrics."""

    metrics = summarize_dataset(dataframe)
    metric_items = [
        ("Deposits", f"{metrics['total_deposits']:,}"),
        ("Located", f"{metrics['rows_with_coordinates']:,}"),
        ("Geology matches", f"{metrics['matched_geology']:,}"),
        ("Coverage", f"{metrics['matched_percent']}%"),
    ]
    if vertical:
        for label, value in metric_items:
            st.metric(label, value)
    else:
        columns = st.columns(4)
        for column, (label, value) in zip(columns, metric_items):
            column.metric(label, value)
    if not compact:
        st.caption(
            "Geology coverage reflects the selected shapefile extent. A Colorado-only layer will intentionally match only a small share of the global MRDS dataset."
        )


def render_workflow() -> None:
    """Render a compact workflow strip for the demo narrative."""

    st.markdown(
        """
        <div class="workflow-strip">
          <div class="workflow-card"><strong>PDFs</strong><span>LLM extraction</span></div>
          <div class="workflow-arrow">+</div>
          <div class="workflow-card"><strong>MRDS CSV</strong><span>structured records</span></div>
          <div class="workflow-arrow">+</div>
          <div class="workflow-card"><strong>Shapefiles</strong><span>geologic context</span></div>
          <div class="workflow-arrow">→</div>
          <div class="workflow-card emphasis"><strong>GeoParquet</strong><span>ML-ready output</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_commodity_chart(dataframe: pd.DataFrame, *, height: int = 280) -> None:
    """Render commodity frequency as a readable horizontal chart."""

    counts = commodity_counts(dataframe, limit=12)
    if counts.empty:
        st.info("No commodity values available.")
        return
    chart = (
        alt.Chart(counts)
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            x=alt.X("count:Q", title="records"),
            y=alt.Y("commodity:N", sort="-x", title=None),
            tooltip=["commodity", "count"],
        )
        .properties(height=height)
    )
    st.altair_chart(chart, use_container_width=True)


def render_distribution_chart(dataframe: pd.DataFrame, column: str, title: str, *, height: int = 260) -> None:
    """Render a compact distribution chart for scalar geology fields."""

    if column not in dataframe or dataframe[column].dropna().empty:
        st.info(f"No {title.lower()} values in the current dataset. Rerun geology enrichment if the source layer has this attribute.")
        return
    counts = dataframe[column].value_counts().head(15).reset_index()
    counts.columns = [column, "count"]
    chart = (
        alt.Chart(counts)
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            x=alt.X("count:Q", title="records"),
            y=alt.Y(f"{column}:N", sort="-x", title=None),
            tooltip=[column, "count"],
        )
        .properties(height=height)
    )
    st.altair_chart(chart, use_container_width=True)


def render_map(dataframe: pd.DataFrame, max_points: int, *, height: int = 640) -> None:
    """Render an interactive PyDeck scatter map."""

    map_df = prepare_map_dataframe(dataframe, max_points=max_points)
    if map_df.empty:
        st.info("No deposit coordinates available for the current filter.")
        return
    center_lat = float(map_df["latitude"].mean())
    center_lon = float(map_df["longitude"].mean())
    layer = pdk.Layer(
        "ScatterplotLayer",
        data=map_df,
        get_position="[longitude, latitude]",
        get_fill_color="color",
        get_radius=3200,
        radius_min_pixels=1.5,
        radius_max_pixels=9,
        pickable=True,
        opacity=0.78,
    )
    deck = pdk.Deck(
        map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        layers=[layer],
        initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=2.65, pitch=0),
        tooltip={
            "html": "<b>{site_name}</b><br/>Commodities: {commodity_label}<br/>Lithology: {lithology}<br/>Age: {geologic_age}",
            "style": {"backgroundColor": "#17202a", "color": "white"},
        },
    )
    st.pydeck_chart(deck, use_container_width=True, height=height)
    st.caption(f"Rendering {len(map_df):,} sampled deposits. Use filters or raise the point limit for a denser view.")
