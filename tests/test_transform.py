from pathlib import Path

import polars as pl
import pytest

from etl import config
from etl.transform import get_latest_raw_dir, read_jodi_files


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


def test_keeps_only_us_crude_imports_and_exports_in_kbd(tmp_path):
    write_jodi_csv(
        tmp_path / "primary_2025.csv",
        "US,2025-08,CRUDEOIL,TOTIMPSB,KBD,6282.8710,1",  # keep
        "US,2025-08,CRUDEOIL,TOTEXPSB,KBD,3572.2903,1",  # keep
        "GB,2025-08,CRUDEOIL,TOTIMPSB,KBD,900.0000,1",  # wrong country
        "US,2025-08,NGL,TOTIMPSB,KBD,100.0000,1",  # wrong product
        "US,2025-08,CRUDEOIL,TOTIMPSB,KBBL,194769.0000,1",  # wrong unit
        "US,2025-08,CRUDEOIL,INDPROD,KBD,13793.8710,1",  # wrong flow
    )

    result = read_jodi_files(tmp_path)

    assert result.height == 2
    assert set(result["FLOW_BREAKDOWN"]) == {"TOTIMPSB", "TOTEXPSB"}
    assert set(result["REF_AREA"]) == {"US"}
    assert set(result["UNIT_MEASURE"]) == {"KBD"}


def test_stacks_every_yearly_file(tmp_path):
    write_jodi_csv(tmp_path / "primary_2024.csv", "US,2024-12,CRUDEOIL,TOTIMPSB,KBD,6500.0,1")
    write_jodi_csv(tmp_path / "primary_2025.csv", "US,2025-01,CRUDEOIL,TOTIMPSB,KBD,6400.0,1")
    write_jodi_csv(tmp_path / "primary_2026.csv", "US,2026-01,CRUDEOIL,TOTIMPSB,KBD,6300.0,1")

    result = read_jodi_files(tmp_path)

    assert sorted(result["TIME_PERIOD"]) == ["2024-12", "2025-01", "2026-01"]


@pytest.mark.parametrize("marker", config.JODI_MISSING)
def test_missing_value_markers_become_null(tmp_path, marker):
    write_jodi_csv(
        tmp_path / "primary_2025.csv",
        f"US,2025-08,CRUDEOIL,TOTIMPSB,KBD,{marker},3",
        "US,2025-08,CRUDEOIL,TOTEXPSB,KBD,3572.2903,1",
    )

    result = read_jodi_files(tmp_path).sort("FLOW_BREAKDOWN")

    # The row is kept (a missing month is information for quality.py), but its value is null.
    assert result.height == 2
    assert result["OBS_VALUE"].to_list() == [3572.2903, None]


def test_columns_and_types(tmp_path):
    write_jodi_csv(tmp_path / "primary_2025.csv", "US,2025-08,CRUDEOIL,TOTIMPSB,KBD,6282.8710,1")

    result = read_jodi_files(tmp_path)

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

    result = read_jodi_files(tmp_path)

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

    # Depending on which file is read first, either our check or polars' own
    # file-to-file comparison stops it. Either way it must not succeed.
    with pytest.raises((ValueError, pl.exceptions.ComputeError)):
        read_jodi_files(tmp_path)


def test_accepts_columns_in_a_different_order(tmp_path):
    reordered_header = (
        "TIME_PERIOD,REF_AREA,ENERGY_PRODUCT,FLOW_BREAKDOWN,UNIT_MEASURE,ASSESSMENT_CODE,OBS_VALUE"
    )
    write_all_years(tmp_path, reordered_header, "2025-08,US,CRUDEOIL,TOTIMPSB,KBD,1,6282.8710")

    result = read_jodi_files(tmp_path)

    # Columns are matched by name, so values still land in the right place.
    assert result["OBS_VALUE"].to_list() == [6282.871, 6282.871]
    assert result["ASSESSMENT_CODE"].to_list() == [1, 1]
