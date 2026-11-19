"""Overview page: filters, then one card per question, all on one 1920x1080 screen.

Each card is titled with the question it answers and opens with the answer, so the
page reads as findings first, evidence underneath.
"""

import datetime as dt

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from app import prep, ui
from etl import metrics

# Chart heights chosen so the page fits a ~950px-tall browser window.
TOP_CHART_HEIGHT = 300
BOTTOM_CHART_HEIGHT = 255

clean, flagged, downloaded = ui.load()
first_month = flagged["month"].min()
end_month = prep.latest_jodi_month(clean)

# --- title and filters --------------------------------------------------------

c_title, c_range, c_flow, c_source, c_units = st.columns(
    [2.6, 1.3, 1.0, 1.7, 0.8], vertical_alignment="bottom"
)
c_title.title("EIA vs JODI: US Crude Oil Trade")
# Filters keep their values for the whole session, so they survive a trip to the
# Method & data page, which also reads the date range to match its table to it.
preset = c_range.selectbox(
    "Date range",
    prep.PRESETS,
    index=prep.PRESETS.index(prep.DEFAULT_PRESET),
    key="preset",
    persist_state="session",
)
if preset == "Custom":
    picked = c_range.date_input(
        "From and to",
        value=(dt.date(2015, 1, 1), end_month),
        min_value=first_month,
        max_value=end_month,
        format="DD/MM/YYYY",
        help="Pick a start and an end date; each snaps to the start of its month.",
        label_visibility="collapsed",
        key="custom_range",
        persist_state="session",
    )
    start, end = prep.month_range(picked, first_month, end_month)
else:
    start, end = prep.preset_range(preset, first_month, end_month)
flow = c_flow.segmented_control(
    "Flow",
    ["Imports", "Exports"],
    default="Imports",
    required=True,
    key="flow",
    persist_state="session",
).lower()
sources = c_source.pills(
    "EIA release",
    ui.SOURCES,
    selection_mode="multi",
    default=["eia_weekly"],  # the early release is the main subject; PSM is opt-in
    format_func=ui.LABELS.get,
    key="sources",
    persist_state="session",
)
units = c_units.segmented_control(
    "Units", ["%", "kbd"], default="%", required=True, key="units", persist_state="session"
)

if not sources:
    st.warning("Select at least one EIA release.")
    st.stop()

diff_col = "diff_pct" if units == "%" else "diff_kbd"
bias_col, size_col = ("bias_pct", "mape") if units == "%" else ("bias_kbd", "mae_kbd")
size_name = "MAPE" if units == "%" else "MAE"
unit_fmt = (lambda v: f"{v:+.2f}%") if units == "%" else (lambda v: f"{v:+,.0f} kbd")
size_fmt = (lambda v: f"{v:.2f}%") if units == "%" else (lambda v: f"{v:,.0f} kbd")
value_hover = "%{y:+,.2f}%" if units == "%" else "%{y:+,.0f} kbd"

selected = prep.in_range(flagged, start, end).filter(
    pl.col("flow") == flow, pl.col("eia_source").is_in(sources)
)
range_label = f"{ui.fmt_month(start)}–{ui.fmt_month(end)}"
if selected.is_empty():
    st.warning(f"No months with both EIA and JODI data in {range_label}.")
    st.stop()

# --- numbers behind the cards -------------------------------------------------

series_keys = ["flow", "eia_source"]
scores = (
    metrics.summarise(selected, by=series_keys)
    .join(prep.lean_verdicts(selected), on=series_keys)
    .join(selected.group_by(series_keys).agg(outliers=pl.col("is_anomaly").sum()), on=series_keys)
    .sort("eia_source", descending=True)  # WPSR first
)
# Annual figures only for whole calendar years in the range (plus year-to-date), so
# a range starting mid-year never shows a few months as if they were a year.
complete_years, ytd_year = prep.calendar_years(start, end, end_month)
shown_years = complete_years + ([ytd_year] if ytd_year else [])
yearly = metrics.summarise(selected, by=[*series_keys, "year"]).filter(
    pl.col("year").is_in(shown_years)
)
# Trailing 12-month mean error on full history, so the range's first months are full.
rolling = prep.in_range(prep.with_rolling_bias(flagged, diff_col), start, end).filter(
    pl.col("flow") == flow
)

# WPSR is the early release this dashboard is about, so the headline numbers describe
# it; they fall back to PSM only when WPSR is filtered out.
focus = "eia_weekly" if "eia_weekly" in sources else "eia_monthly"
focus_name = ui.SHORT[focus]
focus_row = scores.filter(pl.col("eia_source") == focus).row(0, named=True)


# The least accurate month by the selected unit, and the bias verdict as one word.
worst = (
    selected.filter(pl.col("eia_source") == focus)
    .sort(pl.col(diff_col).abs(), descending=True)
    .row(0, named=True)
)
# Coloured with Markdown; the shades are redTextColor / greenTextColor in config.toml.
BIAS_WORD = {
    "Biased high": ":red[High]",
    "Biased low": ":red[Low]",
    "No significant bias": ":green[Neutral]",
}


def metric(column, label: str, value: str, note: str, help: str) -> None:
    """A headline number with a small grey note under it.

    The note is a caption, not the metric's delta: a delta keeps an indent for its
    arrow even when the arrow is hidden, so it never lines up with the label.
    """
    with column.container(gap=None):
        st.metric(label, value, help=help)
        st.caption(note)


# --- row 1: headline numbers | bias -------------------------------------------

left, right = st.columns([1, 1.45], gap="medium")
with left, st.container(border=True, height="stretch"):
    st.subheader(f"How accurate is {focus_name}?", anchor=False)
    # The grid fills the rest of the card and sits in its middle, not at the top.
    grid = st.container(height="stretch", vertical_alignment="center", gap="large")
    a, b, c = grid.columns(3)
    metric(
        a,
        size_name,
        size_fmt(focus_row[size_col]),
        f"MAE {focus_row['mae_kbd']:,.0f} kbd"
        if units == "%"
        else f"MAPE {focus_row['mape']:.2f}%",
        "Average size of the error, whichever way it went.",
    )
    metric(
        b,
        "Mean error",
        unit_fmt(focus_row[bias_col]),
        f"median {focus_row['median_pct']:+.2f}%",
        "EIA − JODI averaged over the range; positive means EIA overstated.",
    )
    metric(
        c,
        "Directional bias",
        BIAS_WORD[focus_row["verdict"]],
        f"{focus_row['n_above']} over · {focus_row['n_below']} under",
        f"Sign test p = {focus_row['p_value']:.3f}. Biased when p < {prep.LEAN_P:g} and "
        f"|median error| ≥ {prep.LEAN_MIN_PCT:g}%.",
    )
    d, e, f = grid.columns(3)
    metric(
        d,
        "Least accurate month",
        unit_fmt(worst[diff_col]),
        ui.fmt_month(worst["month"]),
        "The month with the largest error in the range.",
    )
    metric(
        e,
        "Outliers",
        str(focus_row["outliers"]),
        f"|z| > {metrics.ANOMALY_Z:g}",
        f"Months more than {metrics.ANOMALY_Z:g}σ from the trailing "
        f"{metrics.ANOMALY_WINDOW}-month mean; see Method & data.",
    )
    metric(
        f,
        "Months compared",
        str(focus_row["n_months"]),
        range_label,
        "Months where both EIA and JODI have a value.",
    )

# The bias chart carries the headline finding, so it gets the largest card.
with right, st.container(border=True):
    head, toggle = st.columns([3, 1], vertical_alignment="center")
    head.subheader(f"Does {focus_name} lean high or low?", anchor=False)
    show_single = toggle.toggle("Monthly errors", value=False)
    fig = go.Figure()
    ui.zero_line(fig)
    for source in sources:
        rows = rolling.filter(pl.col("eia_source") == source)
        if show_single:
            fig.add_trace(
                go.Scatter(
                    x=rows["month"],
                    y=rows[diff_col],
                    name=f"{ui.SHORT[source]} monthly",
                    line=dict(color=ui.COLORS[source], width=1),
                    opacity=0.6,
                    legendgroup=source,
                    hovertemplate=value_hover,
                )
            )
        fig.add_trace(
            go.Scatter(
                x=rows["month"],
                y=rows["rolling"],
                name=f"{ui.SHORT[source]} {prep.ROLLING_MONTHS}-month mean error",
                line=dict(color=ui.COLORS[source], width=3),
                legendgroup=source,
                hovertemplate=value_hover,
            )
        )
    # Rings sit on single-month values, so they only make sense with those lines shown.
    if show_single:
        ui.add_outliers(fig, selected.filter("is_anomaly"), diff_col)
    ui.shade_periods(fig, flow, start, end)
    if units == "%":
        fig.update_yaxes(ticksuffix="%")
    y_title = "EIA − JODI" if units == "%" else "EIA − JODI (kbd)"
    ui.show_chart(ui.style(fig, y_title, TOP_CHART_HEIGHT), alt="Trailing mean error")

# --- row 2: levels | accuracy | outliers --------------------------------------

c_levels, c_acc, c_out = st.columns([1.25, 1, 1.05], gap="medium")

with c_levels, st.container(border=True, height="stretch"):
    st.subheader(f"Does {focus_name} track JODI?", anchor=False)
    fig = go.Figure()
    levels = prep.in_range(clean, start, end).filter(pl.col("flow") == flow)
    for source in ["jodi", *sources]:
        rows = levels.filter(pl.col("source") == source)
        fig.add_trace(
            go.Scatter(
                x=rows["month"],
                y=rows["value_kbd"],
                name=ui.LABELS[source],
                line=dict(color=ui.COLORS[source], width=2),
                hovertemplate="%{y:,.0f} kbd",
            )
        )
    ui.add_outliers(fig, selected.filter("is_anomaly"), "eia_kbd")
    ui.shade_periods(fig, flow, start, end)
    ui.show_chart(
        ui.style(fig, f"{flow.capitalize()} (kbd)", BOTTOM_CHART_HEIGHT),
        alt=f"{ui.LABELS[flow]}: EIA and JODI volumes",
    )

with c_acc, st.container(border=True, height="stretch"):
    st.subheader(f"Is {focus_name} getting more accurate?", anchor=False)
    if shown_years:
        fig = go.Figure()
        for source in sources:
            rows = yearly.filter(pl.col("eia_source") == source)
            fig.add_trace(
                go.Scatter(
                    x=rows["year"],
                    y=rows[size_col],
                    name=ui.SHORT[source],
                    mode="lines+markers",
                    line=dict(color=ui.COLORS[source], width=2),
                    marker=dict(
                        size=8,
                        symbol=["circle-open" if y == ytd_year else "circle" for y in rows["year"]],
                        line=dict(color=ui.COLORS[source], width=2),
                    ),
                    customdata=[
                        "year-to-date" if y == ytd_year else "full year" for y in rows["year"]
                    ],
                    hovertemplate="%{y:,.2f} (%{customdata})",
                )
            )
        # One tick per year; the partial current year says so on the axis itself.
        fig.update_xaxes(
            hoverformat="d",
            tickmode="array",
            tickvals=shown_years,
            ticktext=[f"{y}<br>YTD" if y == ytd_year else str(y) for y in shown_years],
            tickangle=0,
        )
        fig.update_yaxes(rangemode="tozero")
        if units == "%":
            fig.update_yaxes(ticksuffix="%")
        y_label = f"Annual {size_name}" if units == "%" else f"Annual {size_name} (kbd)"
        fig = ui.style(fig, y_label, BOTTOM_CHART_HEIGHT, dates=False)
        fig.update_layout(margin_b=44)  # room for the two-line "2026 / YTD" tick
        ui.show_chart(fig, alt=f"Annual {size_name}")

with c_out, st.container(border=True, height="stretch"):
    st.subheader("Which months stand out?", anchor=False)
    outlier_table = selected.filter("is_anomaly").sort("month", descending=True)
    if not outlier_table.is_empty():
        release = ["eia_source"] if len(sources) > 1 else []
        st.dataframe(
            outlier_table.with_columns(pl.col("eia_source").replace_strict(ui.SHORT)).select(
                "month", *release, "diff_pct", "diff_kbd", "z_score"
            ),
            hide_index=True,
            width="stretch",
            height=BOTTOM_CHART_HEIGHT,
            column_config={
                "month": st.column_config.DateColumn("Month", format="MMM YYYY"),
                "eia_source": "Release",
                "diff_pct": st.column_config.NumberColumn("Error", format="%+.1f%%"),
                "diff_kbd": st.column_config.NumberColumn("Error (kbd)", format="%+,.0f"),
                "z_score": st.column_config.NumberColumn("z", format="%+.1f"),
            },
        )
