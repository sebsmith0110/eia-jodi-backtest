import datetime as dt

import polars as pl
import pytest

from app import prep
from etl.metrics import COMPARISON_SCHEMA
from etl.transform import OUTPUT_SCHEMA

AUG = dt.date(2026, 8, 1)
SEP = dt.date(2026, 9, 1)
OCT = dt.date(2026, 10, 1)


def clean(*rows: tuple) -> pl.DataFrame:
    """Hand-made clean-table rows: (month, flow, source, value_kbd)."""
    return pl.DataFrame([(*row, None, None) for row in rows], schema=OUTPUT_SCHEMA, orient="row")


def months_back(end: dt.date, n: int) -> list[dt.date]:
    """The n months ending at `end`, oldest first."""
    return [prep.add_months(end, -i) for i in range(n - 1, -1, -1)]


def weekly_misses(end: dt.date, pcts: list[float], flow="imports") -> pl.DataFrame:
    """Comparison rows for weekly EIA ending at `end`, JODI fixed at 1,000 kbd."""
    rows = [
        (m, flow, "eia_weekly", 1000 + p * 10, 1000.0, p * 10, p)
        for m, p in zip(months_back(end, len(pcts)), pcts, strict=True)
    ]
    return pl.DataFrame(rows, schema=COMPARISON_SCHEMA, orient="row")


# --- add_months / preset_range / in_range --------------------------------------


@pytest.mark.parametrize(
    ("month", "n", "expected"),
    [
        (AUG, 1, SEP),
        (AUG, 5, dt.date(2027, 1, 1)),
        (AUG, -8, dt.date(2025, 12, 1)),
        (AUG, -59, dt.date(2021, 9, 1)),
        (AUG, 0, AUG),
    ],
)
def test_add_months_crosses_year_boundaries(month, n, expected):
    assert prep.add_months(month, n) == expected


@pytest.mark.parametrize(
    ("preset", "expected"),
    [
        ("Last 12 months", (dt.date(2025, 9, 1), AUG)),
        ("Last 3 years", (dt.date(2023, 9, 1), AUG)),
        ("Last 5 years", (dt.date(2021, 9, 1), AUG)),
        ("Since 2015", (dt.date(2015, 1, 1), AUG)),
        ("Since 2002", (dt.date(2002, 1, 1), AUG)),
    ],
)
def test_preset_range(preset, expected):
    assert prep.preset_range(preset, first=dt.date(2002, 1, 1), last=AUG) == expected


def test_preset_range_never_goes_before_the_first_month():
    assert prep.preset_range("Since 2015", first=dt.date(2020, 1, 1), last=AUG) == (
        dt.date(2020, 1, 1),
        AUG,
    )


def test_every_preset_except_custom_has_a_range():
    for preset in prep.PRESETS:
        if preset != "Custom":
            start, end = prep.preset_range(preset, first=dt.date(2002, 1, 1), last=AUG)
            assert start <= end


def test_in_range_includes_both_ends():
    df = clean(*[(m, "imports", "jodi", 1.0) for m in [AUG, SEP, OCT]])

    assert prep.in_range(df, AUG, SEP)["month"].to_list() == [AUG, SEP]


# --- latest_jodi_month ---------------------------------------------------------


def test_latest_jodi_month_ignores_eia_and_null_jodi_months():
    df = clean(
        (AUG, "imports", "jodi", 1.0),
        (SEP, "imports", "jodi", None),
        (OCT, "imports", "eia_weekly", 1.0),
    )

    assert prep.latest_jodi_month(df) == AUG


# --- with_rolling_bias ---------------------------------------------------------


def test_rolling_bias_averages_the_last_twelve_months_including_this_one():
    pcts = [float(i) for i in range(14)]
    result = prep.with_rolling_bias(weekly_misses(AUG, pcts), "diff_pct")

    assert result["rolling"][-1] == pytest.approx(sum(pcts[-12:]) / 12)


def test_rolling_bias_needs_six_months():
    result = prep.with_rolling_bias(weekly_misses(AUG, [1.0] * 6), "diff_pct")

    assert result["rolling"].null_count() == prep.ROLLING_MIN_MONTHS - 1


def test_rolling_bias_keeps_series_apart():
    both = pl.concat(
        [
            weekly_misses(AUG, [1.0] * 6, flow="imports"),
            weekly_misses(AUG, [5.0] * 6, flow="exports"),
        ]
    )
    result = prep.with_rolling_bias(both, "diff_pct").filter(pl.col("month") == AUG)

    assert dict(result.select("flow", "rolling").rows()) == {"imports": 1.0, "exports": 5.0}


def test_rolling_bias_can_use_kbd():
    result = prep.with_rolling_bias(weekly_misses(AUG, [2.0] * 6), "diff_kbd")

    assert result["rolling"][-1] == pytest.approx(20.0)


# --- sign_test_p / lean_verdicts / matching_months -----------------------------


def misses(*rows: tuple) -> pl.DataFrame:
    """Comparison rows from (flow, eia_source, diff_pct), one month each from Jan 2020.

    JODI is fixed at 1,000 kbd, so diff_kbd is diff_pct * 10.
    """
    out = [
        (prep.add_months(dt.date(2020, 1, 1), i), f, s, 1000 + p * 10, 1000.0, p * 10, p)
        for i, (f, s, p) in enumerate(rows)
    ]
    return pl.DataFrame(out, schema=COMPARISON_SCHEMA, orient="row")


@pytest.mark.parametrize(
    ("above", "below", "expected"),
    [
        (5, 5, 1.0),
        (6, 0, 2 / 64),
        (0, 6, 2 / 64),
        (5, 1, 2 * 7 / 64),
        (0, 0, 1.0),
    ],
)
def test_sign_test_p(above, below, expected):
    assert prep.sign_test_p(above, below) == pytest.approx(expected)


def test_lean_verdict_high_when_eia_is_mostly_above_by_enough():
    result = prep.lean_verdicts(misses(*[("imports", "eia_weekly", 2.0)] * 8))

    assert result.select("n_above", "n_below", "verdict").row(0) == (8, 0, "Biased high")


def test_lean_verdict_low_when_eia_is_mostly_below_by_enough():
    result = prep.lean_verdicts(misses(*[("imports", "eia_weekly", -2.0)] * 8))

    assert result["verdict"][0] == "Biased low"


def test_lean_verdict_needs_more_than_chance():
    # 5 of 6 above happens by chance about 22% of the time.
    rows = [("imports", "eia_weekly", 2.0)] * 5 + [("imports", "eia_weekly", -1.0)]
    result = prep.lean_verdicts(misses(*rows))

    assert result["p_value"][0] > prep.LEAN_P
    assert result["verdict"][0] == "No significant bias"


def test_lean_verdict_ignores_a_consistent_but_tiny_lean():
    # JODI copying EIA: always a touch above, by a trivial amount.
    result = prep.lean_verdicts(misses(*[("imports", "eia_monthly", 0.01)] * 20))

    assert result["p_value"][0] < prep.LEAN_P
    assert result["verdict"][0] == "No significant bias"


def test_lean_verdict_is_not_hidden_by_one_huge_miss_the_other_way():
    # The mean is negative, but EIA was above in 10 of 11 months.
    rows = [("exports", "eia_weekly", 1.0)] * 10 + [("exports", "eia_weekly", -50.0)]
    result = prep.lean_verdicts(misses(*rows))

    assert result["median_pct"][0] == 1.0
    assert result["verdict"][0] == "Biased high"


def test_lean_verdict_does_not_count_exact_matches_as_either_side():
    rows = [("imports", "eia_monthly", 0.0)] * 4 + [("imports", "eia_monthly", 1.0)]
    result = prep.lean_verdicts(misses(*rows))

    assert result.select("n_above", "n_below").row(0) == (1, 0)


def test_lean_verdict_scores_each_series_separately():
    rows = [("imports", "eia_weekly", -2.0)] * 8 + [("imports", "eia_monthly", 0.5)] * 2
    result = prep.lean_verdicts(misses(*rows))

    assert result.select("eia_source", "verdict").rows() == [
        ("eia_monthly", "No significant bias"),
        ("eia_weekly", "Biased low"),
    ]


def test_matching_months_counts_jodi_within_one_kbd_of_eia():
    # diff_kbd is diff_pct * 10: 0.0 and 0.05 match, 0.2 (2 kbd) doesn't.
    rows = [("imports", "eia_monthly", p) for p in [0.0, 0.05, -0.05, 0.2]]
    result = prep.matching_months(misses(*rows))

    assert result.select("n_months", "n_matching").row(0) == (4, 3)


# --- calendar_years ------------------------------------------------------------


def test_calendar_years_skips_a_partial_first_year():
    # "Last 5 years" to Aug 2026 starts in Sep 2021: 2021 has only 4 months in range.
    complete, ytd = prep.calendar_years(dt.date(2021, 9, 1), AUG, latest=AUG)

    assert complete == [2022, 2023, 2024, 2025]
    assert ytd == 2026


def test_calendar_years_includes_a_year_that_starts_and_ends_the_range():
    complete, ytd = prep.calendar_years(dt.date(2015, 1, 1), dt.date(2016, 12, 1), latest=AUG)

    assert complete == [2015, 2016]
    assert ytd is None


def test_calendar_years_year_to_date_only_when_the_range_reaches_the_latest_data():
    # A custom range ending mid-2020 is a partial year, not year-to-date.
    complete, ytd = prep.calendar_years(dt.date(2018, 1, 1), dt.date(2020, 6, 1), latest=AUG)

    assert complete == [2018, 2019]
    assert ytd is None


def test_calendar_years_year_to_date_needs_january_in_range():
    complete, ytd = prep.calendar_years(dt.date(2026, 3, 1), AUG, latest=AUG)

    assert complete == []
    assert ytd is None


def test_calendar_years_last_twelve_months_has_only_year_to_date():
    complete, ytd = prep.calendar_years(dt.date(2025, 9, 1), AUG, latest=AUG)

    assert complete == []
    assert ytd == 2026


# --- month_range ---------------------------------------------------------------

FIRST = dt.date(2002, 1, 1)


def test_month_range_snaps_days_to_the_start_of_their_month():
    assert prep.month_range((dt.date(2019, 3, 17), dt.date(2021, 6, 30)), FIRST, AUG) == (
        dt.date(2019, 3, 1),
        dt.date(2021, 6, 1),
    )


def test_month_range_clamps_to_the_data():
    assert prep.month_range((dt.date(1999, 5, 2), dt.date(2030, 1, 1)), FIRST, AUG) == (FIRST, AUG)


def test_month_range_runs_to_the_latest_month_while_only_a_start_is_picked():
    assert prep.month_range((dt.date(2020, 2, 9),), FIRST, AUG) == (dt.date(2020, 2, 1), AUG)


def test_month_range_with_nothing_picked_is_all_the_data():
    assert prep.month_range(None, FIRST, AUG) == (FIRST, AUG)
    assert prep.month_range((), FIRST, AUG) == (FIRST, AUG)


def test_month_range_accepts_a_single_date():
    assert prep.month_range(dt.date(2024, 7, 4), FIRST, AUG) == (dt.date(2024, 7, 1), AUG)
