"""Shared pieces for the dashboard pages: labels, colours, data loading, chart helpers."""

import datetime as dt
import json
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from app import prep, schemes
from etl import config, metrics, snapshot, transform

SOURCES = ["eia_monthly", "eia_weekly"]
LABELS = {
    "eia_monthly": "EIA monthly (PSM)",
    "eia_weekly": "EIA weekly (WPSR)",
    "jodi": "JODI",
    "imports": "Imports",
    "exports": "Exports",
}
UK = ZoneInfo("Europe/London")  # check times are stored in UTC, shown in UK time
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


def load() -> tuple[pl.DataFrame, pl.DataFrame, str]:
    """Clean table, comparison with outliers (scored on full history), download date.

    Built from the raw downloads when they exist (locally), else read from the
    committed snapshot (on Streamlit Community Cloud).
    """
    local = (config.RAW_DIR / "jodi").is_dir()
    if local:
        data_version = tuple(transform.get_latest_raw_dir(s).name for s in ("jodi", "eia"))
    else:
        data_version = tuple(p.name for p in sorted(config.SNAPSHOT_DIR.glob("clean_*.csv")))
    return _load(local, data_version)


# The cache is keyed on which files hold the data, so a new download or snapshot is
# loaded fresh. Without that, a running app keeps serving the first table it loaded,
# even after the daily refresh pushes a new snapshot.
@st.cache_data
def _load(local: bool, data_version: tuple) -> tuple[pl.DataFrame, pl.DataFrame, str]:
    """load(), cached per data version."""
    if local:
        clean = transform.run()
        downloaded = transform.get_latest_raw_dir("jodi").name
    else:
        clean, downloaded = snapshot.load()
    flagged = metrics.flag_anomalies(metrics.compare(clean))
    return clean, flagged, downloaded


def data_status() -> dict | None:
    """The last pipeline run's summary (data/logs/status.json, written by etl.quality),
    or None if the pipeline hasn't run. Not cached: it's tiny and should show a new
    check as soon as one is pushed."""
    path = config.LOG_DIR / "status.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def show_data_status() -> None:
    """One small line: each source's latest data and release date, and when the
    pipeline last checked for new data; a warning badge on any late source, and a
    second line saying what's late."""
    status = data_status()
    if status is None:
        st.caption("No data check has run yet.")
        return

    parts = []
    for source in ("eia_weekly", "eia_monthly", "jodi"):
        info = status["sources"].get(source)
        if not info or not info["latest"]:
            continue
        latest = dt.date.fromisoformat(info["latest"])
        period = f"week ending {latest:%d %b}" if source == "eia_weekly" else fmt_month(latest)
        released = dt.date.fromisoformat(info["released"])
        text = f"**{LABELS[source]}** {period}, released {released:%d %b}"
        if info["late"]:
            text += " :orange-badge[:material/warning: Late]"
        parts.append(text)

    checked = dt.datetime.fromisoformat(status["checked_at"]).astimezone(UK)
    parts.append(f"Last checked for new data {checked:%d %b %Y, %H:%M} UK time")
    if not status["passed"]:
        parts.append(":red-badge[:material/error: Last check failed: showing earlier data]")
    st.caption(" · ".join(parts))

    problems = [p["detail"] for p in status["problems"] if p["check"].endswith(" on time")]
    if problems:
        st.caption(":orange[" + " · ".join(problems) + "]")


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


def zero_line(fig: go.Figure) -> None:
    """Draw y = 0 as a dark target line, so zero error reads as the goal."""
    # layer="below" keeps lines crossing zero visible on top of it.
    fig.add_hline(y=0, line=dict(color=INK, width=2), layer="below")


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
