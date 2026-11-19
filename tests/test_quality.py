import datetime as dt

import polars as pl
import pytest

from etl import config, quality
from etl.transform import OUTPUT_SCHEMA

JUL, AUG, SEP = dt.date(2026, 7, 1), dt.date(2026, 8, 1), dt.date(2026, 9, 1)


def clean(*rows: tuple) -> pl.DataFrame:
    """Hand-made clean-table rows: (month, flow, source, value_kbd)."""
    return pl.DataFrame([(*row, None, None) for row in rows], schema=OUTPUT_SCHEMA, orient="row")


def good() -> pl.DataFrame:
    """Three complete months of JODI and EIA weekly imports that pass every check."""
    return clean(
        *(
            (month, "imports", source, 6500.0)
            for month in (JUL, AUG, SEP)
            for source in ("jodi", "eia_weekly")
        )
    )


def test_a_good_table_passes_every_check(capsys):
    results = quality.run(good())

    assert [r.status for r in results] == ["PASS"] * (1 + len(quality.CHECKS))
    jodi_line = next(line for line in capsys.readouterr().out.splitlines() if "jodi" in line)
    assert jodi_line.split()[:4] == ["jodi", "imports", "3", "rows"]


# --- schema --------------------------------------------------------------------


def test_schema_fails_on_a_missing_column():
    result = quality.check_schema(good().drop("days_covered"))

    assert not result.passed
    assert result.critical


def test_schema_fails_on_a_wrong_type():
    result = quality.check_schema(good().with_columns(pl.col("value_kbd").cast(pl.String)))

    assert not result.passed


def test_schema_fails_on_an_unknown_source_or_flow():
    table = clean((AUG, "imports", "opec", 1.0), (AUG, "stocks", "jodi", 1.0))

    result = quality.check_schema(table)

    assert "unknown sources ['opec']" in result.detail
    assert "unknown flows ['stocks']" in result.detail


def test_schema_fails_on_a_month_not_on_the_first():
    result = quality.check_schema(clean((dt.date(2026, 8, 15), "imports", "jodi", 1.0)))

    assert result.detail == "1 months not on the 1st"


def test_run_stops_at_a_bad_schema_without_running_the_other_checks():
    with pytest.raises(quality.QualityError) as error:
        quality.run(good().drop("month"))

    assert [r.name for r in error.value.failed] == ["schema"]


# --- required fields / duplicates / completeness --------------------------------


def test_required_fields_fails_on_a_null_source():
    result = quality.check_required_fields(clean((AUG, "imports", None, 1.0)))

    assert not result.passed
    assert result.detail == "nulls in {'source': 1}"


def test_duplicates_lists_each_repeated_key_once():
    table = pl.concat([good(), good().filter(pl.col("month") == AUG)])

    result = quality.check_duplicates(table)

    assert not result.passed
    assert result.detail == (
        "2 duplicated keys: 2026-08-01 eia_weekly imports, 2026-08-01 jodi imports"
    )


def test_completeness_finds_a_month_missing_from_the_middle():
    table = good().filter(~((pl.col("month") == AUG) & (pl.col("source") == "jodi")))

    result = quality.check_completeness(table)

    assert not result.passed
    assert result.detail == "1 missing months: jodi imports 2026-08-01"


def test_completeness_does_not_expect_months_before_a_series_starts_or_after_it_ends():
    # JODI publishes later than EIA, so its series ending earlier is normal.
    table = good().filter(~((pl.col("month") == SEP) & (pl.col("source") == "jodi")))

    assert quality.check_completeness(table).passed


# --- missing values / value range ------------------------------------------------


def test_missing_values_only_warns():
    table = good().with_columns(
        value_kbd=pl.when(pl.col("month") == AUG).then(None).otherwise(pl.col("value_kbd"))
    )

    result = quality.check_missing_values(table)

    assert result.status == "WARN"
    assert result.detail.startswith("2 months with no value")


def test_a_missing_value_does_not_stop_the_run(capsys):
    table = good().with_columns(
        value_kbd=pl.when(pl.col("month") == AUG).then(None).otherwise(pl.col("value_kbd"))
    )

    results = quality.run(table)

    assert [r.name for r in results if r.status == "WARN"] == ["missing values"]


@pytest.mark.parametrize("value", [-0.1, config.VALUE_MAX_KBD + 0.1])
def test_value_range_fails_just_outside_the_limits(value):
    result = quality.check_value_range(clean((AUG, "imports", "jodi", value)))

    assert not result.passed
    assert result.critical


@pytest.mark.parametrize("value", [config.VALUE_MIN_KBD, config.VALUE_MAX_KBD, None])
def test_value_range_accepts_the_limits_themselves_and_nulls(value):
    assert quality.check_value_range(clean((AUG, "imports", "jodi", value))).passed


def test_a_units_change_stops_the_run(capsys):
    # Barrels per day instead of thousand barrels per day: 1000x too big.
    table = good().with_columns(pl.col("value_kbd") * 1000)

    with pytest.raises(quality.QualityError, match="value range"):
        quality.run(table)
