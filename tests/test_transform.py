import datetime as dt
from pathlib import Path

import polars as pl
import pytest
import xlsxwriter

from etl import config
from etl.transform import (
    OUTPUT_SCHEMA,
    get_latest_raw_dir,
    read_eia,
    read_jodi_files,
    transform_eia_monthly,
    transform_eia_weekly,
    transform_jodi,
)


@pytest.fixture
def raw_dir(tmp_path, monkeypatch):
    """A throwaway data/raw folder, set up like the real one.

    RAW_DIR is relative ("data/raw"), as in config.py, and the working directory is
    the temp folder, so paths behave exactly as they do when the pipeline runs.
    """
    monkeypatch.chdir(tmp_path)
    raw = Path("data") / "raw"
    raw.mkdir(parents=True)
    monkeypatch.setattr(config, "RAW_DIR", raw)
    return raw


# --- get_latest_raw_dir --------------------------------------------------------


def make_dirs(base: Path, *names: str) -> None:
    for name in names:
        (base / name).mkdir(parents=True)


def test_picks_the_most_recent_date(raw_dir):
    make_dirs(raw_dir / "jodi", "2026-11-02", "2026-11-16", "2026-11-09")

    assert get_latest_raw_dir("jodi").name == "2026-11-16"


def test_returns_a_path_to_an_existing_folder(raw_dir):
    make_dirs(raw_dir / "jodi", "2026-11-16")

    result = get_latest_raw_dir("jodi")

    assert result == raw_dir / "jodi" / "2026-11-16"
    assert result.is_dir()


def test_compares_dates_across_month_and_year_boundaries(raw_dir):
    make_dirs(raw_dir / "eia", "2025-12-31", "2026-01-01", "2026-09-30")

    assert get_latest_raw_dir("eia").name == "2026-09-30"


def test_ignores_files_and_folders_that_are_not_dates(raw_dir):
    source = raw_dir / "jodi"
    make_dirs(source, "2026-11-09", "notes", "latest", "2026-13-01", "2099-1-1")
    (source / "primary_2026.csv").write_text("stray file")
    (source / "2099-12-31").write_text("a file named like a date, not a folder")

    assert get_latest_raw_dir("jodi").name == "2026-11-09"


def test_only_looks_in_the_requested_source(raw_dir):
    make_dirs(raw_dir / "jodi", "2026-11-09")
    make_dirs(raw_dir / "eia", "2026-11-16")

    assert get_latest_raw_dir("jodi").name == "2026-11-09"
    assert get_latest_raw_dir("eia").name == "2026-11-16"


def test_raises_when_there_are_no_dated_folders(raw_dir):
    make_dirs(raw_dir / "jodi", "notes")

    with pytest.raises(FileNotFoundError, match="No dated download folders"):
        get_latest_raw_dir("jodi")


def test_raises_when_the_source_folder_does_not_exist(raw_dir):
    with pytest.raises(FileNotFoundError):
        get_latest_raw_dir("jodi")


# --- read_jodi_files -----------------------------------------------------------

JODI_HEADER = (
    "REF_AREA,TIME_PERIOD,ENERGY_PRODUCT,FLOW_BREAKDOWN,UNIT_MEASURE,OBS_VALUE,ASSESSMENT_CODE"
)


def write_jodi_csv(path: Path, *rows: str) -> None:
    """Write a tiny JODI-style CSV: the real header plus the given data rows."""
    path.write_text("\n".join([JODI_HEADER, *rows]) + "\n")


# One US crude import/export row in kbd to keep, plus one row failing each filter.
MIXED_JODI_ROWS = (
    "US,2025-08,CRUDEOIL,TOTIMPSB,KBD,6282.8710,1",  # keep
    "US,2025-08,CRUDEOIL,TOTEXPSB,KBD,3572.2903,1",  # keep
    "GB,2025-08,CRUDEOIL,TOTIMPSB,KBD,900.0000,1",  # wrong country
    "US,2025-08,NGL,TOTIMPSB,KBD,100.0000,1",  # wrong product
    "US,2025-08,CRUDEOIL,TOTIMPSB,KBBL,194769.0000,1",  # wrong unit
    "US,2025-08,CRUDEOIL,INDPROD,KBD,13793.8710,1",  # wrong flow
)


def test_read_is_lazy_and_does_not_filter(tmp_path):
    write_jodi_csv(tmp_path / "primary_2025.csv", *MIXED_JODI_ROWS)

    lazy = read_jodi_files(tmp_path)

    assert isinstance(lazy, pl.LazyFrame)
    assert lazy.collect().height == len(MIXED_JODI_ROWS)


def test_stacks_every_yearly_file(tmp_path):
    write_jodi_csv(tmp_path / "primary_2024.csv", "US,2024-12,CRUDEOIL,TOTIMPSB,KBD,6500.0,1")
    write_jodi_csv(tmp_path / "primary_2025.csv", "US,2025-01,CRUDEOIL,TOTIMPSB,KBD,6400.0,1")
    write_jodi_csv(tmp_path / "primary_2026.csv", "US,2026-01,CRUDEOIL,TOTIMPSB,KBD,6300.0,1")

    result = read_jodi_files(tmp_path).collect()

    assert sorted(result["TIME_PERIOD"]) == ["2024-12", "2025-01", "2026-01"]


@pytest.mark.parametrize("marker", config.JODI_MISSING)
def test_missing_value_markers_become_null(tmp_path, marker):
    write_jodi_csv(
        tmp_path / "primary_2025.csv",
        f"US,2025-08,CRUDEOIL,TOTIMPSB,KBD,{marker},3",
        "US,2025-08,CRUDEOIL,TOTEXPSB,KBD,3572.2903,1",
    )

    result = read_jodi_files(tmp_path).collect().sort("FLOW_BREAKDOWN")

    assert result.height == 2
    assert result["OBS_VALUE"].to_list() == [3572.2903, None]


def test_columns_and_types(tmp_path):
    write_jodi_csv(tmp_path / "primary_2025.csv", "US,2025-08,CRUDEOIL,TOTIMPSB,KBD,6282.8710,1")

    result = read_jodi_files(tmp_path).collect()

    assert result.columns == JODI_HEADER.split(",")
    assert result.schema["OBS_VALUE"] == pl.Float64
    assert result.schema["ASSESSMENT_CODE"] == pl.Int8
    assert result["OBS_VALUE"][0] == pytest.approx(6282.871)


def test_ignores_files_not_named_primary_year(tmp_path):
    write_jodi_csv(tmp_path / "primary_2025.csv", "US,2025-08,CRUDEOIL,TOTIMPSB,KBD,6282.8710,1")
    # JODI's own download names, a secondary-products file, a backup and a note.
    for name in ["2025.csv", "primaryyear2026.csv", "secondary_2025.csv", "primary_2025.csv.bak"]:
        write_jodi_csv(tmp_path / name, "US,2099-01,CRUDEOIL,TOTIMPSB,KBD,1.0,1")
    (tmp_path / "notes.txt").write_text("not a csv")
    (tmp_path / "primary_2099.csv").mkdir()  # a folder with a matching name

    result = read_jodi_files(tmp_path).collect()

    assert result["TIME_PERIOD"].to_list() == ["2025-08"]


def test_raises_when_folder_has_no_jodi_files(tmp_path):
    (tmp_path / "notes.txt").write_text("not a csv")

    with pytest.raises(FileNotFoundError, match="No JODI primary CSVs"):
        read_jodi_files(tmp_path)


# --- read_jodi_files: column check -----------------------------------------------

GOOD_ROW = "US,2025-08,CRUDEOIL,TOTIMPSB,KBD,6282.8710,1"


def write_all_years(folder: Path, header: str, row: str) -> None:
    """Simulate JODI changing its format: every yearly file gets the same new layout."""
    for year in (2025, 2026):
        (folder / f"primary_{year}.csv").write_text(f"{header}\n{row}\n")


@pytest.mark.parametrize(
    ("header", "row", "missing", "unexpected"),
    [
        pytest.param(
            JODI_HEADER.replace("OBS_VALUE", "VALUE"),
            GOOD_ROW,
            "OBS_VALUE",
            "VALUE",
            id="value-column-renamed",
        ),
        pytest.param(
            JODI_HEADER.replace("ASSESSMENT_CODE", "QUALITY"),
            GOOD_ROW,
            "ASSESSMENT_CODE",
            "QUALITY",
            id="assessment-column-renamed",
        ),
        pytest.param(
            JODI_HEADER.replace("TIME_PERIOD,", ""),
            GOOD_ROW.replace("2025-08,", ""),
            "TIME_PERIOD",
            None,
            id="column-missing",
        ),
        pytest.param(
            JODI_HEADER + ",NOTE",
            GOOD_ROW + ",hello",
            None,
            "NOTE",
            id="extra-column",
        ),
    ],
)
def test_raises_when_every_file_has_different_columns(tmp_path, header, row, missing, unexpected):
    write_all_years(tmp_path, header, row)

    with pytest.raises(ValueError, match="JODI columns changed") as error:
        read_jodi_files(tmp_path)

    # The message names exactly what changed, so a failed run explains itself.
    message = str(error.value)
    if missing:
        assert f"Missing: ['{missing}']" in message
    if unexpected:
        assert f"unexpected: ['{unexpected}']" in message


def test_raises_when_download_is_an_html_error_page(tmp_path):
    for year in (2025, 2026):
        (tmp_path / f"primary_{year}.csv").write_text("<!DOCTYPE html><html>Error</html>\n")

    with pytest.raises(ValueError, match="JODI columns changed"):
        read_jodi_files(tmp_path)


def test_raises_when_one_file_differs_from_the_others(tmp_path):
    write_jodi_csv(tmp_path / "primary_2025.csv", GOOD_ROW)
    (tmp_path / "primary_2026.csv").write_text(
        JODI_HEADER.replace("OBS_VALUE", "VALUE") + "\n" + GOOD_ROW + "\n"
    )

    # Depending on which file is read first, either our column check or polars'
    # own file-to-file comparison (on collect) stops it.
    with pytest.raises((ValueError, pl.exceptions.ComputeError)):
        read_jodi_files(tmp_path).collect()


def test_accepts_columns_in_a_different_order(tmp_path):
    reordered_header = (
        "TIME_PERIOD,REF_AREA,ENERGY_PRODUCT,FLOW_BREAKDOWN,UNIT_MEASURE,ASSESSMENT_CODE,OBS_VALUE"
    )
    write_all_years(tmp_path, reordered_header, "2025-08,US,CRUDEOIL,TOTIMPSB,KBD,1,6282.8710")

    result = read_jodi_files(tmp_path).collect()

    assert result["OBS_VALUE"].to_list() == [6282.871, 6282.871]
    assert result["ASSESSMENT_CODE"].to_list() == [1, 1]


# --- transform_jodi ------------------------------------------------------------


def jodi_raw(*rows: tuple) -> pl.DataFrame:
    """Hand-made raw JODI rows, typed as read_jodi_files returns them."""
    return pl.DataFrame(
        rows,
        schema={
            "REF_AREA": pl.String,
            "TIME_PERIOD": pl.String,
            "ENERGY_PRODUCT": pl.String,
            "FLOW_BREAKDOWN": pl.String,
            "UNIT_MEASURE": pl.String,
            "OBS_VALUE": pl.Float64,
            "ASSESSMENT_CODE": pl.Int8,
        },
        orient="row",
    )


def test_transform_keeps_only_us_crude_imports_and_exports_in_kbd(tmp_path):
    write_jodi_csv(tmp_path / "primary_2025.csv", *MIXED_JODI_ROWS)

    result = transform_jodi(read_jodi_files(tmp_path)).sort("flow")

    assert result["flow"].to_list() == ["exports", "imports"]
    assert result["value_kbd"].to_list() == [3572.2903, 6282.871]


def test_transform_output_matches_the_shared_schema():
    raw = jodi_raw(("US", "2026-08", "CRUDEOIL", "TOTIMPSB", "KBD", 6709.0, 1))

    result = transform_jodi(raw)

    # Every source must return exactly this, or pl.concat in run() fails.
    assert result.schema == pl.Schema(OUTPUT_SCHEMA)


def test_transform_maps_columns_to_clean_values():
    raw = jodi_raw(
        ("US", "2026-08", "CRUDEOIL", "TOTIMPSB", "KBD", 6709.0, 1),
        ("US", "2026-08", "CRUDEOIL", "TOTEXPSB", "KBD", 3819.5, 2),
    )

    result = transform_jodi(raw).sort("flow")

    assert result.to_dicts() == [
        {
            "month": dt.date(2026, 8, 1),
            "flow": "exports",
            "source": "jodi",
            "value_kbd": 3819.5,
            "assessment_code": 2,
            "days_covered": None,
        },
        {
            "month": dt.date(2026, 8, 1),
            "flow": "imports",
            "source": "jodi",
            "value_kbd": 6709.0,
            "assessment_code": 1,
            "days_covered": None,
        },
    ]


def test_transform_keeps_months_with_no_value():
    raw = jodi_raw(("US", "2026-08", "CRUDEOIL", "TOTIMPSB", "KBD", None, 3))

    result = transform_jodi(raw)

    assert result.height == 1
    assert result["value_kbd"].to_list() == [None]


@pytest.mark.parametrize("marker", config.JODI_MISSING)
def test_missing_markers_end_up_null_not_zero(tmp_path, marker):
    write_jodi_csv(tmp_path / "primary_2025.csv", f"US,2025-08,CRUDEOIL,TOTIMPSB,KBD,{marker},3")

    result = transform_jodi(read_jodi_files(tmp_path))

    assert result["value_kbd"].to_list() == [None]


def test_transform_accepts_lazy_or_eager_input():
    raw = jodi_raw(("US", "2026-08", "CRUDEOIL", "TOTIMPSB", "KBD", 6709.0, 1))

    assert transform_jodi(raw.lazy()).equals(transform_jodi(raw))


# --- read_eia ------------------------------------------------------------------

EIA_HEADER = "U.S. Imports of Crude Oil (Thousand Barrels per Day)"


def write_eia_file(
    folder: Path,
    series_id: str,
    rows: list[tuple],
    header: str = EIA_HEADER,
    sourcekey: str | None = None,
) -> Path:
    """Write a small workbook laid out like an EIA download (sheet "Data 1").

    Saved as .xlsx because nothing can write the old .xls format; the reader
    treats both the same.
    """
    path = folder / f"{series_id}.xlsx"
    book = xlsxwriter.Workbook(path)
    book.add_worksheet("Contents")
    sheet = book.add_worksheet("Data 1")
    date_format = book.add_format({"num_format": "mmm dd, yyyy"})

    sheet.write_row(0, 0, ["Back to Contents", f"Data 1: {header}"])
    sheet.write_row(1, 0, ["Sourcekey", sourcekey or series_id])
    sheet.write_row(2, 0, ["Date", header])
    for i, (day, value) in enumerate(rows, start=3):
        if isinstance(day, dt.date):
            sheet.write_datetime(i, 0, dt.datetime.combine(day, dt.time()), date_format)
        elif day is not None:  # text in the date column, e.g. a footnote
            sheet.write(i, 0, day)
        if value is not None:
            sheet.write(i, 1, value)
    book.close()
    return path


def test_read_eia_returns_date_and_value(tmp_path):
    path = write_eia_file(
        tmp_path, "MCRIMUS2", [(dt.date(2026, 7, 15), 6224), (dt.date(2026, 8, 15), 6594)]
    )

    result = read_eia(path)

    assert result.schema == pl.Schema({"date": pl.Date, "value": pl.Float64})
    # Dates are left as EIA stamps them; moving to month start is the transform's job.
    assert result.rows() == [(dt.date(2026, 7, 15), 6224.0), (dt.date(2026, 8, 15), 6594.0)]


def test_read_eia_drops_rows_with_no_value(tmp_path):
    path = write_eia_file(
        tmp_path,
        "WCRIMUS2",
        [
            (dt.date(2026, 10, 30), 6500),
            (dt.date(2026, 11, 6), None),  # week with an empty value cell
            (None, None),  # blank row
        ],
    )

    result = read_eia(path)

    assert result.rows() == [(dt.date(2026, 10, 30), 6500.0)]


@pytest.mark.parametrize("date_cell", [None, "Notes: revised"], ids=["empty", "text"])
def test_read_eia_raises_on_a_value_without_a_date(tmp_path, date_cell):
    path = write_eia_file(tmp_path, "WCRIMUS2", [(dt.date(2026, 10, 30), 6500), (date_cell, 123)])

    with pytest.raises(ValueError, match=r"values without a date: \[123.0\]"):
        read_eia(path)


def test_read_eia_raises_when_sourcekey_does_not_match_file_name(tmp_path):
    path = write_eia_file(
        tmp_path, "MCRIMUS2", [(dt.date(2026, 8, 15), 3992)], sourcekey="MCREXUS2"
    )

    with pytest.raises(ValueError, match="holds series 'MCREXUS2', expected 'MCRIMUS2'"):
        read_eia(path)


def test_read_eia_raises_when_units_change(tmp_path):
    path = write_eia_file(
        tmp_path,
        "MCRIMUS2",
        [(dt.date(2026, 8, 15), 204414)],
        header="U.S. Imports of Crude Oil (Thousand Barrels)",
    )

    with pytest.raises(ValueError, match="not in Thousand Barrels per Day"):
        read_eia(path)


def test_read_eia_raises_on_text_in_the_value_column(tmp_path):
    path = write_eia_file(
        tmp_path, "MCRIMUS2", [(dt.date(2026, 7, 15), 6224), (dt.date(2026, 8, 15), "NA")]
    )

    # Not silently turned into null and dropped.
    with pytest.raises(pl.exceptions.InvalidOperationError):
        read_eia(path)


# --- transform_eia_monthly -----------------------------------------------------


def eia_raw(*rows: tuple) -> pl.DataFrame:
    """Hand-made rows, typed as read_eia returns them."""
    return pl.DataFrame(rows, schema={"date": pl.Date, "value": pl.Float64}, orient="row")


def test_eia_monthly_moves_dates_to_the_first_of_the_month():
    raw = eia_raw((dt.date(2026, 7, 15), 6224.0), (dt.date(2026, 8, 15), 6594.0))

    result = transform_eia_monthly(raw, "imports")

    assert result["month"].to_list() == [dt.date(2026, 7, 1), dt.date(2026, 8, 1)]


def test_eia_monthly_relabels_without_changing_values():
    raw = eia_raw((dt.date(2026, 8, 15), 3992.0))

    result = transform_eia_monthly(raw, "exports")

    assert result.to_dicts() == [
        {
            "month": dt.date(2026, 8, 1),
            "flow": "exports",
            "source": "eia_monthly",
            "value_kbd": 3992.0,
            "assessment_code": None,
            "days_covered": None,
        }
    ]


def test_eia_monthly_output_matches_the_shared_schema():
    result = transform_eia_monthly(eia_raw((dt.date(2026, 8, 15), 6594.0)), "imports")

    assert result.schema == pl.Schema(OUTPUT_SCHEMA)


# --- transform_eia_weekly --------------------------------------------------------


def weeks(first_friday: dt.date, *values: float) -> pl.DataFrame:
    """Consecutive weekly rows, as read_eia returns them, starting on a given Friday."""
    assert first_friday.weekday() == 4, "EIA weeks end on a Friday"
    return eia_raw(*((first_friday + dt.timedelta(weeks=i), v) for i, v in enumerate(values)))


# Real WCRIMUS2 values for the weeks ending Fri 4 Sep to Fri 2 Oct 2026. Together
# they cover exactly September (29 Aug - 2 Oct), with August and October partial.
SEPTEMBER_2026 = weeks(dt.date(2026, 9, 4), 6824.0, 7058.0, 5877.0, 5698.0, 6840.0)


def test_weekly_month_average_weights_each_week_by_its_days_in_the_month():
    result = transform_eia_weekly(SEPTEMBER_2026, "imports")

    # (4×6824 + 7×7058 + 7×5877 + 7×5698 + 5×6840) / 30. Grouping weeks by the
    # month they end in would give 6364.25 instead.
    assert result["month"].to_list() == [dt.date(2026, 9, 1)]
    assert result["value_kbd"][0] == pytest.approx(6397.5667, abs=1e-4)
    assert result["days_covered"][0] == 30


def test_weekly_week_across_a_month_boundary_is_split_by_day():
    # Only the week ending Fri 2 Oct (26 Sep - 2 Oct) is non-zero, so each month's
    # average shows how many of its days landed there: 5 in September, 2 in October.
    raw = weeks(dt.date(2026, 9, 4), 0, 0, 0, 0, 3100.0, 0, 0, 0, 0, 0)

    result = transform_eia_weekly(raw, "imports")

    assert result["month"].to_list() == [dt.date(2026, 9, 1), dt.date(2026, 10, 1)]
    assert result["value_kbd"].to_list() == pytest.approx([5 * 3100 / 30, 2 * 3100 / 31])


def test_weekly_drops_the_partial_latest_month():
    # Weeks to Fri 6 Nov 2026: October is complete, November has only 6 days.
    raw = weeks(dt.date(2026, 10, 2), *[6000.0] * 6)

    result = transform_eia_weekly(raw, "imports")

    assert result["month"].to_list() == [dt.date(2026, 10, 1)]


def test_weekly_drops_a_month_with_a_missing_week():
    raw = SEPTEMBER_2026.filter(pl.col("date") != dt.date(2026, 9, 18))

    assert transform_eia_weekly(raw, "imports").is_empty()


def test_weekly_handles_a_leap_year_february():
    # Weeks ending Fri 7 Jan to Fri 3 Mar 2028 cover 1 Jan - 3 Mar.
    raw = weeks(dt.date(2028, 1, 7), *[5000.0] * 9)

    result = transform_eia_weekly(raw, "imports")

    assert result.select("month", "days_covered").rows() == [
        (dt.date(2028, 1, 1), 31),
        (dt.date(2028, 2, 1), 29),
    ]


def test_weekly_output_labels_and_schema():
    result = transform_eia_weekly(SEPTEMBER_2026, "exports")

    assert result.schema == pl.Schema(OUTPUT_SCHEMA)
    row = result.row(0, named=True)
    assert (row["flow"], row["source"], row["assessment_code"]) == ("exports", "eia_weekly", None)


def test_weekly_raises_on_a_duplicate_week():
    # With 18 Sep missing and 11 Sep repeated, September still adds up to 30 days.
    raw = pl.concat(
        [
            SEPTEMBER_2026.filter(pl.col("date") != dt.date(2026, 9, 18)),
            eia_raw((dt.date(2026, 9, 11), 7058.0)),
        ]
    )

    with pytest.raises(ValueError, match=r"1 duplicate weeks .*2026, 9, 11"):
        transform_eia_weekly(raw, "imports")
