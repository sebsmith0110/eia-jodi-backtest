"""Shape the clean and comparison tables for the dashboard.

Pure functions (DataFrame in, DataFrame out), tested like etl/metrics.py, so the
Streamlit file only lays out what these return.
"""

import datetime as dt
import math

import polars as pl

PRESETS = [
    "Last 12 months",
    "Last 3 years",
    "Last 5 years",
    "Since 2015",
    "Since 2002",
    "Custom",
]
DEFAULT_PRESET = "Since 2015"

# Rolling bias line on the bias chart: 12 months smooths out one-off misses.
ROLLING_MONTHS = 12
ROLLING_MIN_MONTHS = 6


# A series "leans" only if EIA lands on one side of JODI more often than chance would
# explain (sign test, two-sided p < LEAN_P) AND the median miss is at least
# LEAN_MIN_PCT that way, so JODI copying EIA to within rounding never reads as a lean.
# Median, not mean: a few huge % misses on tiny pre-2015 exports would swamp a mean.
LEAN_P = 0.05
LEAN_MIN_PCT = 0.5
VERDICTS = ("Biased high", "Biased low", "No significant bias")
# JODI within this of EIA monthly means JODI almost certainly reused EIA's figure.
MATCH_KBD = 1.0
# Annual chart: more labels than this crowd its narrow column on long ranges.
YEAR_TICKS_MAX = 12
YEAR_TICK_STEPS = (1, 2, 5, 10)


def add_months(month: dt.date, n: int) -> dt.date:
    """First of the month n months after (or before, if n < 0) `month`."""
    index = month.year * 12 + month.month - 1 + n
    return dt.date(index // 12, index % 12 + 1, 1)


def preset_range(preset: str, first: dt.date, last: dt.date) -> tuple[dt.date, dt.date]:
    """(start, end) months for a date-range preset; every preset ends at `last`.

    The start never goes before `first`, the oldest month with data.
    """
    starts = {
        "Last 12 months": add_months(last, -11),
        "Last 3 years": add_months(last, -35),
        "Last 5 years": add_months(last, -59),
        "Since 2015": dt.date(2015, 1, 1),
        "Since 2002": first,
    }
    return max(starts[preset], first), last


def in_range(df: pl.DataFrame, start: dt.date, end: dt.date) -> pl.DataFrame:
    """Rows whose month is between start and end, inclusive."""
    return df.filter(pl.col("month").is_between(start, end))


def latest_jodi_month(clean: pl.DataFrame) -> dt.date:
    """Latest month JODI has a value for, across both flows."""
    return clean.filter(pl.col("source") == "jodi").drop_nulls("value_kbd")["month"].max()


def with_rolling_bias(comparison: pl.DataFrame, column: str) -> pl.DataFrame:
    """Add `rolling`: the mean of `column` over each series' last ROLLING_MONTHS months.

    Computed on the full history, so the first months of a short date range
    still get a full 12-month average once the caller filters the range.
    """
    return comparison.sort("month").with_columns(
        rolling=pl.col(column)
        .rolling_mean_by(
            by="month",
            window_size=f"{ROLLING_MONTHS}mo",
            closed="right",
            min_samples=ROLLING_MIN_MONTHS,
        )
        .over("flow", "eia_source")
    )


def sign_test_p(n_above: int, n_below: int) -> float:
    """Two-sided chance of a split at least this uneven if above and below were equally likely."""
    n = n_above + n_below
    if n == 0:
        return 1.0
    k = max(n_above, n_below)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / 2**n
    return min(1.0, 2 * tail)


def lean_verdicts(comparison: pl.DataFrame) -> pl.DataFrame:
    """Does each series lean high, low or neither over the months given?

    One row per flow and EIA source: n_above, n_below (months EIA was above or
    below JODI; exact matches count as neither), median_pct, p_value and verdict,
    one of VERDICTS.
    """
    counts = (
        comparison.group_by("flow", "eia_source")
        .agg(
            n_above=(pl.col("diff_kbd") > 0).sum(),
            n_below=(pl.col("diff_kbd") < 0).sum(),
            median_pct=pl.col("diff_pct").median(),
        )
        .sort("flow", "eia_source")
    )
    p_values = [sign_test_p(a, b) for a, b in counts.select("n_above", "n_below").rows()]
    significant = pl.col("p_value") < LEAN_P
    return counts.with_columns(p_value=pl.Series(p_values, dtype=pl.Float64)).with_columns(
        verdict=pl.when(
            significant
            & (pl.col("n_above") > pl.col("n_below"))
            & (pl.col("median_pct") >= LEAN_MIN_PCT)
        )
        .then(pl.lit(VERDICTS[0]))
        .when(
            significant
            & (pl.col("n_below") > pl.col("n_above"))
            & (pl.col("median_pct") <= -LEAN_MIN_PCT)
        )
        .then(pl.lit(VERDICTS[1]))
        .otherwise(pl.lit(VERDICTS[2]))
    )


def matching_months(comparison: pl.DataFrame) -> pl.DataFrame:
    """How often JODI equals EIA to within MATCH_KBD, per flow and EIA source.

    One row per series: n_months, n_matching.
    """
    return (
        comparison.group_by("flow", "eia_source")
        .agg(
            n_months=pl.len(),
            n_matching=(pl.col("diff_kbd").abs() < MATCH_KBD).sum(),
        )
        .sort("flow", "eia_source")
    )


def calendar_years(start: dt.date, end: dt.date, latest: dt.date) -> tuple[list[int], int | None]:
    """Which years a range can show annual figures for: (complete years, year-to-date).

    Complete years have all of Jan-Dec inside start..end, so a range starting in
    September never shows a four-month "year". The year-to-date year is end.year when
    the range runs to `latest` (the newest month with data) mid-year from January
    onwards; otherwise None.
    """
    complete = [
        y
        for y in range(start.year, end.year + 1)
        if start <= dt.date(y, 1, 1) and end >= dt.date(y, 12, 1)
    ]
    runs_to_latest = end == latest and end.month != 12
    ytd = end.year if runs_to_latest and start <= dt.date(end.year, 1, 1) else None
    return complete, ytd


def year_ticks(years: list[int], max_ticks: int = YEAR_TICKS_MAX) -> list[int]:
    """The years to label on an annual chart's x axis, at most max_ticks of them.

    Steps through 1, 2, 5 then 10 years, counting back from the latest year so the
    newest (often year-to-date) point is always labelled.
    """
    if not years:
        return []
    last = max(years)
    for step in YEAR_TICK_STEPS:
        ticks = [y for y in years if (last - y) % step == 0]
        if len(ticks) <= max_ticks:
            return ticks
    return ticks


def month_range(
    picked: tuple[dt.date, ...] | dt.date | None, first: dt.date, last: dt.date
) -> tuple[dt.date, dt.date]:
    """Turn a calendar pick into a (start, end) range of whole months within first..last.

    Days snap to the first of their month, since every series is monthly. Half a
    pick (only a start date so far) runs to `last`; nothing picked = all data.
    """
    if picked is None:
        picked = ()
    elif isinstance(picked, dt.date):
        picked = (picked,)
    months = [min(max(d.replace(day=1), first), last) for d in picked]
    if not months:
        return first, last
    if len(months) == 1:
        return months[0], last
    return min(months), max(months)
