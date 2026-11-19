import datetime as dt
import json
from pathlib import Path

import polars as pl
import pytest

from etl import config, quality
from etl.transform import OUTPUT_SCHEMA

JUL, AUG, SEP = dt.date(2026, 7, 1), dt.date(2026, 8, 1), dt.date(2026, 9, 1)
TODAY = dt.date(2026, 11, 19)  # a Thursday: Wednesday's WPSR covered the week ending 13 Nov
LATEST_WEEK = dt.date(2026, 11, 13)


def clean(*rows: tuple) -> pl.DataFrame:
    """Hand-made clean-table rows: (month, flow, source, value_kbd)."""
    return pl.DataFrame([(*row, None, None) for row in rows], schema=OUTPUT_SCHEMA, orient="row")


def good() -> pl.DataFrame:
    """Three complete months of imports from each source that pass every check on TODAY."""
    return clean(
        *(
            (month, "imports", source, 6500.0)
            for month in (JUL, AUG, SEP)
            for source in ("jodi", "eia_weekly", "eia_monthly")
        )
    )


def run_checks(table: pl.DataFrame) -> list[quality.CheckResult]:
    """quality.run with every source on time."""
    return quality.run(table, latest_week=LATEST_WEEK, today=TODAY)


def test_a_good_table_passes_every_check(capsys):
    results = run_checks(good())

    assert {r.status for r in results} == {"PASS"}
    # schema, the CHECKS list, rows not shrinking, then the 3 sources on time
    assert len(results) == 1 + len(quality.CHECKS) + 1 + 3
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
        run_checks(good().drop("month"))

    assert [r.name for r in error.value.failed] == ["schema"]


# --- required fields / duplicates / completeness --------------------------------


def test_required_fields_fails_on_a_null_source():
    result = quality.check_required_fields(clean((AUG, "imports", None, 1.0)))

    assert not result.passed
    assert result.detail == "nulls in {'source': 1}"


def test_duplicates_counts_extra_rows_and_months():
    # August copied once for all 3 sources, and JODI's August a third time.
    table = pl.concat(
        [good(), good().filter(pl.col("month") == AUG), clean((AUG, "imports", "jodi", 6500.0))]
    )

    result = quality.check_duplicates(table)

    assert result.status == "FAIL"
    assert result.detail.startswith("4 extra rows across 3 months, 0 with conflicting values")


def test_duplicates_flags_copies_that_disagree():
    table = pl.concat([good(), clean((AUG, "imports", "jodi", 9999.0))])

    result = quality.check_duplicates(table)

    assert result.detail == (
        "1 extra rows across 1 months, 1 with conflicting values: 2026-08-01 jodi imports 2 True"
    )


def test_a_null_and_a_number_for_the_same_month_conflict():
    table = clean((AUG, "imports", "jodi", 6500.0), (AUG, "imports", "jodi", None))

    assert quality.duplicated_months(table)["conflicting"].to_list() == [True]


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
    assert result.detail.startswith("3 months with no value")


def test_a_missing_value_does_not_stop_the_run(capsys):
    table = good().with_columns(
        value_kbd=pl.when(pl.col("month") == AUG).then(None).otherwise(pl.col("value_kbd"))
    )

    results = run_checks(table)

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
        run_checks(table)


# --- on time -------------------------------------------------------------------


# The workflow runs at 07:00 UTC, before every release, so a release is due by the
# next day's run: on the release day itself the previous one is still enough.
@pytest.mark.parametrize(
    ("latest_week", "today", "on_time"),
    [
        (dt.date(2026, 11, 6), dt.date(2026, 11, 18), True),  # Wed: release later that day
        (dt.date(2026, 11, 6), dt.date(2026, 11, 19), False),  # Thu: Wednesday's release missing
        (dt.date(2026, 11, 13), dt.date(2026, 11, 19), True),
        # Veterans Day week: the week ending 6 Nov came out on Thursday 12 Nov instead.
        (dt.date(2026, 10, 30), dt.date(2026, 11, 12), True),
        (dt.date(2026, 10, 30), dt.date(2026, 11, 13), False),
    ],
)
def test_weekly_is_late_the_day_after_its_release(latest_week, today, on_time):
    result = quality.check_weekly_on_time(latest_week, today)

    assert result.passed == on_time
    assert not result.critical


@pytest.mark.parametrize(
    ("today", "on_time"),
    [
        (dt.date(2026, 11, 19), True),  # release day (calendar: 19 Nov): Aug still enough
        (dt.date(2026, 11, 20), False),  # the day after: Sep is due
    ],
)
def test_jodi_is_late_the_day_after_its_calendar_date(today, on_time):
    table = good().filter(pl.col("month") <= AUG)

    assert quality.check_monthly_on_time(table, "jodi", today).passed == on_time


def test_jodi_assumes_the_22nd_for_a_month_not_in_its_calendar():
    table = clean((dt.date(2026, 10, 1), "imports", "jodi", 1.0))

    assert quality.check_monthly_on_time(table, "jodi", dt.date(2027, 1, 22)).passed
    assert not quality.check_monthly_on_time(table, "jodi", dt.date(2027, 1, 23)).passed


@pytest.mark.parametrize(
    ("today", "on_time"),
    [
        (dt.date(2026, 11, 30), True),  # release day (last weekday): Aug still enough
        (dt.date(2026, 12, 1), False),  # the day after: Sep is due
    ],
)
def test_eia_monthly_is_late_the_day_after_the_last_weekday(today, on_time):
    table = good().filter(pl.col("month") <= AUG)

    assert quality.check_monthly_on_time(table, "eia_monthly", today).passed == on_time


def test_eia_monthly_release_skips_a_month_ending_on_a_weekend():
    assert quality.eia_monthly_release(2027, 1) == dt.date(2027, 1, 29)  # 31 Jan is a Sunday


def test_a_late_message_says_what_was_due_and_when():
    result = quality.check_weekly_on_time(dt.date(2026, 11, 6), dt.date(2026, 11, 19))

    assert result.detail == (
        "LATE: latest week ends 2026-11-06, but the week ending 2026-11-13 was released Wed 18 Nov"
    )


def test_monthly_on_time_goes_by_the_flow_furthest_behind():
    # Imports are current but exports stopped in July.
    table = pl.concat([good(), clean((JUL, "exports", "jodi", 3000.0))])

    result = quality.check_monthly_on_time(table, "jodi", dt.date(2026, 11, 22))

    assert not result.passed
    assert result.detail.startswith("LATE: latest month Jul 2026")


def test_monthly_on_time_ignores_a_month_published_without_a_value():
    table = pl.concat([good(), clean((dt.date(2026, 10, 1), "imports", "jodi", None))])

    result = quality.check_monthly_on_time(table, "jodi", TODAY)

    assert result.detail.startswith("latest month Sep 2026")


def test_a_late_source_warns_but_does_not_stop_the_run(capsys):
    results = quality.run(good(), latest_week=dt.date(2026, 10, 30), today=TODAY)

    assert [r.name for r in results if r.status != "PASS"] == ["eia_weekly on time"]


def test_add_months_crosses_a_year_end():
    assert quality.add_months(dt.date(2026, 11, 1), 3) == dt.date(2027, 2, 1)
    assert quality.add_months(dt.date(2027, 1, 1), -2) == dt.date(2026, 11, 1)


def test_latest_eia_week_takes_the_weekly_series_furthest_behind(monkeypatch):
    # Reading the files is tested in test_transform; here each file is a stand-in table.
    last_week = {"WCRIMUS2.xls": LATEST_WEEK, "WCREXUS2.xls": dt.date(2026, 11, 6)}
    monkeypatch.setattr(
        quality, "read_eia", lambda path: pl.DataFrame({"date": [JUL, last_week[path.name]]})
    )

    assert quality.latest_eia_week(Path("eia")) == dt.date(2026, 11, 6)


# --- row counts and run logs ------------------------------------------------------


def test_row_counts_counts_gaps_duplicates_and_nulls_per_series():
    table = pl.concat(
        [
            clean((JUL, "imports", "jodi", 1.0), (SEP, "imports", "jodi", None)),  # Aug missing
            clean((JUL, "imports", "eia_weekly", 1.0), (JUL, "imports", "eia_weekly", 1.0)),
        ]
    )

    counts = quality.row_counts(table).select(
        "source", "rows", "missing_months", "duplicate_rows", "null_values"
    )

    assert counts.rows() == [("eia_weekly", 2, 0, 1, 0), ("jodi", 2, 1, 0, 1)]


def counts_with(rows_by_source: dict[str, int]) -> pl.DataFrame:
    """A previous run's counts: just the series and their row counts."""
    return pl.DataFrame(
        {"source": list(rows_by_source), "flow": "imports", "rows": list(rows_by_source.values())}
    )


def test_rows_not_shrinking_passes_on_the_first_run():
    assert quality.check_rows_not_shrinking(quality.row_counts(good()), None).passed


def test_rows_not_shrinking_passes_when_series_grow():
    previous = counts_with({"jodi": 2, "eia_weekly": 3, "eia_monthly": 3})

    assert quality.check_rows_not_shrinking(quality.row_counts(good()), previous).passed


def test_rows_not_shrinking_fails_when_a_series_loses_rows():
    previous = counts_with({"jodi": 4, "eia_weekly": 3, "eia_monthly": 3})

    result = quality.check_rows_not_shrinking(quality.row_counts(good()), previous)

    assert result.status == "FAIL"
    assert result.detail == "1 series lost rows (series, before, now): jodi imports 4 3"


def test_rows_not_shrinking_fails_when_a_series_disappears():
    previous = counts_with({"jodi": 3, "eia_weekly": 3, "eia_monthly": 3})
    now = quality.row_counts(good().filter(pl.col("source") != "jodi"))

    assert not quality.check_rows_not_shrinking(now, previous).passed


def test_each_run_appends_counts_and_checks_to_the_logs(tmp_path, capsys):
    for _ in range(2):
        quality.run(good(), latest_week=LATEST_WEEK, today=TODAY, log_dir=tmp_path)

    counts = pl.read_csv(tmp_path / "counts.csv")
    checks = pl.read_csv(tmp_path / "checks.csv")
    assert counts.columns[:4] == ["run_at", "source", "flow", "rows"]
    assert counts.height == 2 * 3  # 2 runs x 3 series, one header
    assert checks.height == 2 * (1 + len(quality.CHECKS) + 1 + 3)
    assert set(checks["status"]) == {"PASS"}


def test_the_next_run_compares_with_the_last_logged_counts(tmp_path, capsys):
    quality.run(good(), latest_week=LATEST_WEEK, today=TODAY, log_dir=tmp_path)

    with pytest.raises(quality.QualityError, match="rows not shrinking"):
        quality.run(
            good().filter(pl.col("month") != JUL),
            latest_week=LATEST_WEEK,
            today=TODAY,
            log_dir=tmp_path,
        )


def test_a_failed_run_is_still_logged(tmp_path, capsys):
    with pytest.raises(quality.QualityError):
        quality.run(
            good().with_columns(pl.col("value_kbd") * 1000),
            latest_week=LATEST_WEEK,
            today=TODAY,
            log_dir=tmp_path,
        )

    checks = pl.read_csv(tmp_path / "checks.csv")
    assert checks.filter(pl.col("status") == "FAIL")["check"].to_list() == ["value range"]


# --- status.json and GitHub annotations ---------------------------------------


def test_released_on_follows_each_schedule():
    assert quality.released_on("eia_weekly", dt.date(2026, 11, 13)) == dt.date(2026, 11, 18)
    assert quality.released_on("eia_weekly", dt.date(2026, 11, 6)) == dt.date(2026, 11, 12)
    assert quality.released_on("eia_monthly", AUG) == dt.date(2026, 10, 30)
    assert quality.released_on("jodi", SEP) == dt.date(2026, 11, 19)


def test_status_gives_each_source_its_latest_period_and_release(tmp_path, capsys):
    quality.run(good(), latest_week=LATEST_WEEK, today=TODAY, log_dir=tmp_path)

    status = json.loads((tmp_path / "status.json").read_text())
    assert status["passed"]
    assert status["problems"] == []
    assert status["sources"]["eia_weekly"] == {
        "latest": "2026-11-13",
        "released": "2026-11-18",
        "late": False,
    }
    assert status["sources"]["jodi"]["latest"] == "2026-09-01"


def test_status_marks_a_late_source_and_says_why(tmp_path, capsys):
    quality.run(good(), latest_week=dt.date(2026, 11, 6), today=TODAY, log_dir=tmp_path)

    status = json.loads((tmp_path / "status.json").read_text())
    assert [s for s, info in status["sources"].items() if info["late"]] == ["eia_weekly"]
    assert status["problems"][0]["detail"].startswith("LATE: latest week ends 2026-11-06")
    assert status["passed"]  # late only warns


def test_status_records_a_failed_run(tmp_path, capsys):
    with pytest.raises(quality.QualityError):
        quality.run(good().drop("month"), latest_week=LATEST_WEEK, today=TODAY, log_dir=tmp_path)

    status = json.loads((tmp_path / "status.json").read_text())
    assert not status["passed"]
    assert status["problems"][0]["check"] == "schema"


def test_warnings_become_github_annotations_on_actions(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    late = quality.CheckResult("jodi on time", passed=False, critical=False, detail="LATE: x")

    quality.annotate_github([late])

    assert capsys.readouterr().out == "::warning title=jodi on time::LATE: x\n"


def test_no_annotations_outside_actions(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    quality.annotate_github([quality.CheckResult("x", False, True, "y")])

    assert capsys.readouterr().out == ""


# --- duplicates in the logs, and logging crashes --------------------------------


def test_row_counts_logs_conflicting_duplicates_per_series():
    table = pl.concat(
        [
            good(),
            clean((AUG, "imports", "jodi", 9999.0)),  # conflicts
            clean((AUG, "imports", "eia_weekly", 6500.0)),  # exact copy
        ]
    )

    counts = quality.row_counts(table).select("source", "duplicate_rows", "conflicting_duplicates")

    assert counts.rows() == [("eia_monthly", 0, 0), ("eia_weekly", 1, 0), ("jodi", 1, 1)]


def test_a_log_gains_new_columns_without_losing_old_rows(tmp_path):
    path = tmp_path / "counts.csv"
    quality.append_csv(path, pl.DataFrame({"run_at": ["day 1"], "rows": [3]}))

    quality.append_csv(path, pl.DataFrame({"run_at": ["day 2"], "rows": [4], "new_count": [1]}))

    log = pl.read_csv(path)
    assert log.columns == ["run_at", "rows", "new_count"]
    assert log.rows() == [("day 1", 3, None), ("day 2", 4, 1)]


def test_a_crash_before_the_checks_is_logged_and_keeps_the_source_details(tmp_path, capsys):
    quality.run(good(), latest_week=LATEST_WEEK, today=TODAY, log_dir=tmp_path)

    quality.log_crash(tmp_path, ValueError("1 duplicate weeks in EIA weekly imports: [...]"))

    status = json.loads((tmp_path / "status.json").read_text())
    assert not status["passed"]
    assert status["problems"][0]["detail"].startswith("1 duplicate weeks")
    assert status["sources"]["jodi"]["latest"] == "2026-09-01"
    last = pl.read_csv(tmp_path / "checks.csv").row(-1, named=True)
    assert (last["check"], last["status"]) == ("pipeline", "FAIL")


def test_a_crash_on_the_very_first_run_is_still_logged(tmp_path):
    quality.log_crash(tmp_path, OSError("download failed"))

    status = json.loads((tmp_path / "status.json").read_text())
    assert status["sources"] == {}
    assert status["problems"][0]["detail"] == "download failed"
