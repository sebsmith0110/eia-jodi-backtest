"""Method & data page: how the numbers are made, known data issues, full statistics."""

import polars as pl
import streamlit as st

from app import prep, ui
from etl import metrics

clean, flagged, downloaded = ui.load()
first_month = flagged["month"].min()
end_month = prep.latest_jodi_month(clean)

st.title("Method & data")
st.markdown(
    f"Data downloaded {downloaded}. Latest JODI month {ui.fmt_month(end_month)}; history "
    f"from {ui.fmt_month(first_month)}."
)

left, right = st.columns(2, gap="large")

with left, st.container(border=True):
    st.subheader("What is compared?", anchor=False)
    st.markdown(
        "EIA's US crude **import and export** estimates against **JODI Oil**"
        ", in thousand barrels per day (kbd).\n\n"
        "- **WPSR** (EIA weekly) is the first print, available around 5 days after a Friday, "
        "and well before JODI monthly data. It is the primary subject of this dashboard.\n"
        "- **PSM** (EIA monthly) is published later and revised; it is available for "
        "comparison.\n"
        "- WPSR weekly rates are aggregated to calendar months, day-weighted across month "
        "boundaries"
    )

with left, st.container(border=True):
    st.subheader("How are errors measured?", anchor=False)
    st.markdown(
        "- **Error** = EIA − JODI. Positive value means EIA over-estimated.\n"
        "- **MAPE / MAE**: Mean Absolute Percentage Error / Mean Absolute Error, ignoring "
        "direction.\n"
        f"- **Bias test**: a sign test on months over vs under JODI. Biased when "
        f"p < {prep.LEAN_P:g} and |median error| ≥ {prep.LEAN_MIN_PCT:g}%.\n"
        f"- **Trailing mean error**: the average error over the previous {prep.ROLLING_MONTHS} "
        "months, to show sustained leans.\n"
        f"- **Outliers**: % error more than {metrics.ANOMALY_Z:g}σ from the same series' "
        f"trailing {metrics.ANOMALY_WINDOW}-month mean (minimum "
        f"{metrics.ANOMALY_MIN_HISTORY} months of history), with |error| ≥ "
        f"{metrics.ANOMALY_MIN_PCT:g}% and ≥ {metrics.ANOMALY_MIN_KBD:g} kbd. Scored on "
        "full history, so the date range never changes which months are flagged. "
        "This is an estimate of what an 'anomaly' would be, but would ideally need definition from"
        " market analysts.\n"
        "- **Annual figures** use whole calendar years inside the range, plus year-to-date."
    )

with right, st.container(border=True):
    st.subheader("What should you watch out for?", anchor=False)
    st.markdown(
        "- **JODI mirrors PSM** in many months (within 1 kbd), consistent with JODI's US "
        "series being sourced from PSM. PSM's accuracy against JODI is overstated; WPSR is "
        "the independent test.\n"
        "- **PSM history is revised**, so focus should be directed towards EIA weekly data "
        "(WPSR).\n"
        "- **Pre-2015 exports** were tiny under the export ban, so % errors there are "
        "large on small volumes; use kbd for that period.\n"
        "- **Very large |z|** comes from a near-flat trailing window (e.g. JODI mirroring "
        "PSM) rather than an extreme error."
    )

with right, st.container(border=True):
    st.subheader("How accurate is each release?", anchor=False)
    # Defaults to the Overview's date range, so the table matches what was just seen.
    overview_preset = st.session_state.get("preset", prep.DEFAULT_PRESET)
    if overview_preset == "Custom":
        overview_range = prep.month_range(
            st.session_state.get("custom_range"), first_month, end_month
        )
    else:
        overview_range = prep.preset_range(overview_preset, first_month, end_month)
    same = f"Same as Overview ({ui.fmt_month(overview_range[0])}–{ui.fmt_month(overview_range[1])})"
    named = [p for p in prep.PRESETS if p != "Custom"]
    period = st.selectbox("Period", [same, *named])
    start, end = (
        overview_range if period == same else prep.preset_range(period, first_month, end_month)
    )
    rows = prep.in_range(flagged, start, end)
    keys = ["flow", "eia_source"]
    stats = (
        metrics.summarise(rows, by=keys)
        .join(prep.lean_verdicts(rows), on=keys)
        .sort("flow", "eia_source", descending=[True, True])
    )
    st.dataframe(
        stats.select(
            pl.col("flow").replace_strict(ui.LABELS),
            pl.col("eia_source").replace_strict(ui.SHORT),
            "n_months",
            "mape",
            "mae_kbd",
            "bias_pct",
            "median_pct",
            (pl.col("share_above") * 100).alias("share_above"),
            "p_value",
            "verdict",
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "flow": "Flow",
            "eia_source": "Release",
            "n_months": "Months",
            "mape": st.column_config.NumberColumn("MAPE", format="%.2f%%"),
            "mae_kbd": st.column_config.NumberColumn("MAE (kbd)", format="%,.0f"),
            "bias_pct": st.column_config.NumberColumn("Mean error", format="%+.2f%%"),
            "median_pct": st.column_config.NumberColumn("Median error", format="%+.2f%%"),
            "share_above": st.column_config.NumberColumn("Months over", format="%.0f%%"),
            "p_value": st.column_config.NumberColumn("Sign test p", format="%.3f"),
            "verdict": "Bias",
        },
    )

with st.container(border=True):
    head, button = st.columns([4, 1], vertical_alignment="center")
    head.subheader("Underlying data", anchor=False)
    button.download_button(
        "Download CSV",
        flagged.write_csv(),
        file_name="eia_vs_jodi_comparison.csv",
        mime="text/csv",
        icon=":material/download:",
        width="stretch",
    )
    st.dataframe(
        flagged.with_columns(
            pl.col("flow").replace_strict(ui.LABELS),
            pl.col("eia_source").replace_strict(ui.SHORT),
        ),
        hide_index=True,
        width="stretch",
        height=260,
        column_config={
            "month": st.column_config.DateColumn("Month", format="MMM YYYY"),
            "flow": "Flow",
            "eia_source": "Release",
            "eia_kbd": st.column_config.NumberColumn("EIA (kbd)", format="%,.0f"),
            "jodi_kbd": st.column_config.NumberColumn("JODI (kbd)", format="%,.0f"),
            "diff_kbd": st.column_config.NumberColumn("Error (kbd)", format="%+,.0f"),
            "diff_pct": st.column_config.NumberColumn("Error (%)", format="%+.2f%%"),
            "z_score": st.column_config.NumberColumn("z", format="%+.1f"),
            "is_anomaly": "Outlier",
        },
    )
