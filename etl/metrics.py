"""Score early EIA figures against JODI: differences, bias, accuracy and anomalies.

Takes the clean table from transform.run(). Every function is pure (DataFrame in,
DataFrame out), so the dashboard and load.py use the same tested numbers.
Differences are always EIA minus JODI: positive means EIA overestimated.
"""

import polars as pl

# The table compare() returns: one row per month, flow and EIA source where both
# EIA and JODI have a value.
COMPARISON_SCHEMA = {
    "month": pl.Date,
    "flow": pl.String,  # "imports" / "exports"
    "eia_source": pl.String,  # "eia_monthly" / "eia_weekly"
    "eia_kbd": pl.Float64,
    "jodi_kbd": pl.Float64,
    "diff_kbd": pl.Float64,  # eia_kbd - jodi_kbd
    "diff_pct": pl.Float64,  # diff_kbd / jodi_kbd * 100
}

# Anomaly rule: compare each month with the same series' previous ANOMALY_WINDOW
# months, and only once there are at least ANOMALY_MIN_HISTORY of them.
ANOMALY_WINDOW = 24
ANOMALY_MIN_HISTORY = 12
ANOMALY_Z = 3.0
# A flagged month must also be a big miss in its own right. Without these, a series
# with almost no spread (JODI copying EIA's imports up to 2005) flags a 0.5% miss at
# z = 192, and pre-2015 exports of 10-30 kbd flag misses of a few barrels.
ANOMALY_MIN_PCT = 1.0
ANOMALY_MIN_KBD = 50.0


def compare(clean: pl.DataFrame) -> pl.DataFrame:
    """Pair each EIA monthly and weekly value with JODI's value for the same month and flow.

    Returns COMPARISON_SCHEMA, sorted by month, flow and eia_source. Months where
    either side has no value (JODI nulls, weekly months JODI hasn't published yet)
    get no row; the dashboard shows those from the clean table instead.
    """
    jodi_rows = clean.filter(pl.col("source") == "jodi").select(
        "month",
        "flow",
        jodi_kbd=pl.col("value_kbd"),
    )
    eia_rows = clean.filter(pl.col("source") != "jodi")
    diff = pl.col("value_kbd") - pl.col("jodi_kbd")

    return (
        # Inner join: a month missing from either side gets no row.
        eia_rows.join(jodi_rows, on=["month", "flow"], how="inner")
        .drop_nulls(["value_kbd", "jodi_kbd"])
        .select(
            pl.col("month"),
            pl.col("flow"),
            pl.col("source").alias("eia_source"),
            pl.col("value_kbd").alias("eia_kbd"),
            pl.col("jodi_kbd"),
            diff.alias("diff_kbd"),
            # Null rather than infinity if JODI ever reports 0.
            pl.when(pl.col("jodi_kbd") != 0)
            .then(diff / pl.col("jodi_kbd") * 100)
            .alias("diff_pct"),
        )
        .cast(COMPARISON_SCHEMA)
        .sort("month", "flow", "eia_source")
    )


def summarise(comparison: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    """Score EIA against JODI for each group in `by`, e.g. ["flow", "eia_source"].

    `by` may also include "year", taken from month, for the accuracy-trend view.
    One row per group with:
        n_months      months compared
        bias_kbd      mean diff_kbd: which way EIA leans, in kbd
        bias_pct      mean diff_pct: the same, in %
        share_above   share of months EIA was above JODI (0.5 = no lean)
        mae_kbd       mean absolute diff_kbd: typical size of a miss
        mape          mean absolute diff_pct
        max_abs_pct   largest absolute diff_pct: the worst month
    """
    abs_pct = pl.col("diff_pct").abs()
    return (
        comparison.with_columns(year=pl.col("month").dt.year())
        .group_by(by)
        .agg(
            n_months=pl.len(),
            bias_kbd=pl.col("diff_kbd").mean(),
            bias_pct=pl.col("diff_pct").mean(),
            share_above=(pl.col("diff_kbd") > 0).mean(),
            mae_kbd=pl.col("diff_kbd").abs().mean(),
            mape=abs_pct.mean(),
            max_abs_pct=abs_pct.max(),
        )
        .sort(by)
    )


def flag_anomalies(comparison: pl.DataFrame) -> pl.DataFrame:
    """Add z_score and is_anomaly columns to the comparison table.

    For each flow and EIA source separately, in month order, diff_pct is compared
    with the mean and standard deviation of the previous ANOMALY_WINDOW months (not
    including the month itself). Months with fewer than ANOMALY_MIN_HISTORY earlier
    months get a null z_score and are never flagged. is_anomaly means
    |z_score| > ANOMALY_Z and the miss is at least ANOMALY_MIN_PCT and
    ANOMALY_MIN_KBD in size.
    """
    # Window by calendar month rather than row count, so a gap in the data doesn't
    # stretch the window further back; closed="left" leaves the month itself out.
    window = dict(
        by="month",
        window_size=f"{ANOMALY_WINDOW}mo",
        closed="left",
        min_samples=ANOMALY_MIN_HISTORY,
    )
    series = ["flow", "eia_source"]
    mean = pl.col("diff_pct").rolling_mean_by(**window).over(series)
    std = pl.col("diff_pct").rolling_std_by(**window).over(series)

    return (
        comparison.sort("month")
        # A flat history (std 0) would give an infinite z; treat it as unscored.
        .with_columns(z_score=pl.when(std > 0).then((pl.col("diff_pct") - mean) / std))
        .with_columns(
            is_anomaly=(
                (pl.col("z_score").abs() > ANOMALY_Z)
                & (pl.col("diff_pct").abs() >= ANOMALY_MIN_PCT)
                & (pl.col("diff_kbd").abs() >= ANOMALY_MIN_KBD)
            ).fill_null(False)
        )
        .sort("month", "flow", "eia_source")
    )
