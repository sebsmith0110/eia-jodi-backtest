"""Shared pieces for the dashboard pages: labels, colours, data loading, chart helpers."""

import datetime as dt

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from app import prep, schemes
from etl import metrics, transform

SOURCES = ["eia_monthly", "eia_weekly"]
LABELS = {
    "eia_monthly": "EIA monthly (PSM)",
    "eia_weekly": "EIA weekly (WPSR)",
    "jodi": "JODI",
    "imports": "Imports",
    "exports": "Exports",
}
SHORT = {"eia_monthly": "PSM", "eia_weekly": "WPSR"}  # for dense sentences
# Colours come from the active scheme (app/schemes.py). Identity colours stay fixed
# whatever is filtered; JODI is the benchmark, so it gets a neutral grey.
SCHEME = schemes.active()
COLORS = {"eia_monthly": SCHEME.monthly, "eia_weekly": SCHEME.weekly, "jodi": SCHEME.jodi}
ANOMALY_COLOR, INK, AXIS, SURFACE = SCHEME.outlier, SCHEME.text, SCHEME.axis, SCHEME.background
GRID, SHADE = SCHEME.grid, SCHEME.shade

# Periods where the comparison means something different; shaded on the charts.
PERIODS = [
    ("imports", dt.date(2002, 1, 1), dt.date(2005, 12, 1), "JODI mirrors PSM"),
    ("exports", dt.date(2002, 1, 1), dt.date(2015, 12, 1), "Export ban: low volumes"),
]


@st.cache_data
def load() -> tuple[pl.DataFrame, pl.DataFrame, str]:
    """Clean table, comparison with outliers (scored on full history), download date."""
    clean = transform.run()
    flagged = metrics.flag_anomalies(metrics.compare(clean))
    return clean, flagged, transform.get_latest_raw_dir("jodi").name


def fmt_month(month: dt.date) -> str:
    """e.g. Aug 2026."""
    return month.strftime("%b %Y")


def n_months(n: int) -> str:
    """e.g. '1 month', '140 months'."""
    return f"{n} month{'s' * (n != 1)}"


def style(fig: go.Figure, y_title: str, height: int, dates: bool = True) -> go.Figure:
    """Shared chart chrome: hairline grid, dark labels, unified hover, legend on top.

    With `dates`, x ticks show years, or month and year when the range is short
    enough for monthly ticks.
    """
    fig.update_layout(
        height=height,
        margin=dict(l=56, r=8, t=4, b=32),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=INK, size=12),
        hovermode="x unified",
        # Plotly hides a one-line legend; keep it so the chart still names its release.
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, title=None),
    )
    fig.update_xaxes(showgrid=False, linecolor=AXIS, tickfont_color=INK)
    if dates:
        fig.update_xaxes(
            hoverformat="%b %Y",
            tickformatstops=[
                dict(dtickrange=[None, "M11"], value="%b %Y"),
                dict(dtickrange=["M12", None], value="%Y"),
            ],
        )
    fig.update_yaxes(
        title=dict(text=y_title, font=dict(color=INK, size=11)),
        gridcolor=GRID,
        zeroline=False,
        linecolor=AXIS,
        tickfont_color=INK,
    )
    return fig


def shade_periods(fig: go.Figure, flow: str, start: dt.date, end: dt.date) -> None:
    """Shade this flow's special periods where they overlap the selected range."""
    for period_flow, p_start, p_end, label in PERIODS:
        x0, x1 = max(p_start, start), min(prep.add_months(p_end, 1), prep.add_months(end, 1))
        if period_flow == flow and x0 < x1:
            fig.add_vrect(
                x0=x0,
                x1=x1,
                fillcolor=SHADE,
                line_width=0,
                layer="below",
                annotation_text=label,
                annotation_position="top left",
                annotation_font=dict(color=SCHEME.annotation, size=11),
            )


def add_outliers(fig: go.Figure, rows: pl.DataFrame, y: str) -> None:
    """Ring each outlier month, with its z-score on hover."""
    if rows.is_empty():
        return
    fig.add_trace(
        go.Scatter(
            x=rows["month"],
            y=rows[y],
            mode="markers",
            name=f"Outlier (|z| > {metrics.ANOMALY_Z:g})",
            marker=dict(symbol="circle-open", size=12, color=ANOMALY_COLOR, line_width=2),
            customdata=rows["z_score"],
            hovertemplate="Outlier, z = %{customdata:+.1f}<extra></extra>",
        )
    )


def show_chart(fig: go.Figure, alt: str) -> None:
    """Render a Plotly figure without its toolbar; `alt` names it for screen readers."""
    st.plotly_chart(fig, theme=None, width="stretch", config={"displayModeBar": False}, alt=alt)
