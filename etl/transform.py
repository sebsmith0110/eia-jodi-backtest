"""Turn the raw JODI and EIA downloads into one clean long table.

Each source has a read_* function (files in, raw rows out) and a transform
function (raw rows in, OUTPUT_SCHEMA out). Transform functions never touch the
disk, so tests can feed them small hand-made tables. Checks on the clean table
live in quality.py, not here.
"""

from datetime import date
from pathlib import Path

import polars as pl

from etl import config

# Every transform function returns exactly these columns, so run() can stack
# the sources with pl.concat. Columns a source doesn't have are typed nulls.
OUTPUT_SCHEMA = {
    "month": pl.Date,  # first of the month
    "flow": pl.String,  # "imports" / "exports"
    "source": pl.String,  # "jodi" / "eia_monthly" / "eia_weekly"
    "value_kbd": pl.Float64,  # null where JODI had no number
    "assessment_code": pl.Int8,  # JODI only
    "days_covered": pl.Int8,  # EIA weekly only
}


def run() -> pl.DataFrame:
    """Read the latest downloads, transform each source and return the stacked table."""
    eia_tables = []
    jodi_df = transform_jodi(read_jodi_files(get_latest_raw_dir("jodi")))
    eia_dir = get_latest_raw_dir("eia")

    for series_id, (flow, _, frequency_code) in config.EIA_SERIES.items():
        raw = read_eia(eia_dir / f"{series_id}.xls")
        if frequency_code == "m":
            eia_tables.append(transform_eia_monthly(raw, flow))
        elif frequency_code == "w":
            eia_tables.append(transform_eia_weekly(raw, flow))
        else:
            raise ValueError(f"Unknown frequency {frequency_code!r} for {series_id}")

    return (
        pl.concat([jodi_df, *eia_tables])
        .filter(pl.col("month") >= date(config.JODI_START_YEAR, 1, 1))
        .sort("month", "flow", "source")
    )


def get_latest_raw_dir(source: str) -> Path:
    """Newest dated download folder for a source, e.g. data/raw/jodi/2026-11-16."""
    base = config.RAW_DIR / source
    date_folders = []

    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        try:
            date.fromisoformat(folder.name)
        except ValueError:
            continue
        date_folders.append(folder)

    if not date_folders:
        raise FileNotFoundError(f"No dated download folders in {base}")
    return max(date_folders, key=lambda p: p.name)


def read_jodi_files(folder: Path) -> pl.LazyFrame:
    """Scan every yearly JODI primary CSV in a folder as one table, unfiltered.

    Lazy because the files hold every country and product (hundreds of MB):
    nothing is loaded until transform_jodi adds its filter. Missing-value
    markers become nulls. Raises ValueError if the columns aren't
    config.JODI_COLUMNS (in any order).
    """
    file_paths = sorted(
        file
        for file in folder.iterdir()
        if file.is_file() and config.JODI_FILE_PATTERN.fullmatch(file.name)
    )
    if not file_paths:
        raise FileNotFoundError(f"No JODI primary CSVs in {folder}")

    lazy = pl.scan_csv(
        file_paths,
        null_values=config.JODI_MISSING,
        schema_overrides={"OBS_VALUE": pl.Float64, "ASSESSMENT_CODE": pl.Int8},
    )

    # Reads only the header. Needed because schema_overrides silently skips
    # columns that don't exist, e.g. if OBS_VALUE were renamed.
    columns = set(lazy.collect_schema().names())
    expected = set(config.JODI_COLUMNS)
    if columns != expected:
        raise ValueError(
            f"JODI columns changed in {folder}. Missing: {sorted(expected - columns)}, "
            f"unexpected: {sorted(columns - expected)}"
        )

    return lazy


def transform_jodi(raw: pl.LazyFrame | pl.DataFrame) -> pl.DataFrame:
    """Reduce raw JODI rows to US crude imports/exports in kbd, as OUTPUT_SCHEMA.

    Null values are kept: a missing month is for quality.py to report.
    """
    return (
        raw.lazy()
        .filter(
            pl.col("REF_AREA") == config.JODI_COUNTRY,
            pl.col("ENERGY_PRODUCT") == config.JODI_PRODUCT,
            pl.col("UNIT_MEASURE") == config.JODI_UNIT,
            pl.col("FLOW_BREAKDOWN").is_in(list(config.JODI_FLOWS)),
        )
        .select(
            month=pl.col("TIME_PERIOD").str.to_date("%Y-%m"),
            flow=pl.col("FLOW_BREAKDOWN").replace_strict(config.JODI_FLOWS),
            source=pl.lit("jodi"),
            value_kbd=pl.col("OBS_VALUE"),
            assessment_code=pl.col("ASSESSMENT_CODE"),
            days_covered=pl.lit(None),
        )
        .cast(OUTPUT_SCHEMA)
        .collect()
    )


def read_eia(path: Path) -> pl.DataFrame:
    """Read one EIA series file (e.g. MCRIMUS2.xls) into two columns: date, value.

    Every EIA file has this layout on sheet "Data 1":
        Back to Contents | Data 1: U.S. Imports of Crude Oil (Thousand Barrels per Day)
        Sourcekey        | MCRIMUS2
        Date             | U.S. Imports of Crude Oil (Thousand Barrels per Day)
        2026-08-15       | 6594

    Dates are left as EIA stamps them. Raises ValueError if the Sourcekey doesn't
    match the file name, the units aren't kbd, or a value has no date.
    """
    series_id = path.stem

    titles = pl.read_excel(path, sheet_name="Data 1", has_header=False, read_options={"n_rows": 3})
    sourcekey = titles[1, 1]
    if sourcekey != series_id:
        raise ValueError(f"{path} holds series {sourcekey!r}, expected {series_id!r}")

    # Values are read as text on purpose: read as numbers, the Excel reader quietly
    # turns a text cell into null, and the row would vanish below. The strict cast
    # to Float64 makes stray text fail loudly instead.
    data = pl.read_excel(
        path,
        sheet_name="Data 1",
        read_options={"header_row": 2, "dtypes": {0: "date", 1: "string"}},
    )
    header = data.columns[1]
    if config.EIA_UNIT not in header:
        raise ValueError(f"{path} is not in {config.EIA_UNIT}: header is {header!r}")

    # The value header differs per file, so columns are picked by position.
    result = data.select(
        date=pl.nth(0),
        value=pl.nth(1).str.strip_chars().cast(pl.Float64),
    ).filter(pl.col("value").is_not_null())  # empty cells: blank rows, a missing week

    undated = result.filter(pl.col("date").is_null())
    if undated.height:
        raise ValueError(f"{path} has values without a date: {undated['value'].to_list()}")
    return result


def transform_eia_monthly(raw: pl.DataFrame, flow: str) -> pl.DataFrame:
    """Relabel one monthly EIA series (from read_eia) as OUTPUT_SCHEMA.

    Values are already monthly averages, so nothing is recalculated. EIA stamps
    each month on the 15th; moving it to the 1st matches JODI's months.
    `flow` comes from config.EIA_SERIES, as the file itself doesn't say.
    """
    return raw.select(
        month=pl.col("date").dt.month_start(),
        flow=pl.lit(flow),
        source=pl.lit("eia_monthly"),
        value_kbd=pl.col("value"),
        assessment_code=pl.lit(None),
        days_covered=pl.lit(None),
    ).cast(OUTPUT_SCHEMA)


def transform_eia_weekly(raw: pl.DataFrame, flow: str) -> pl.DataFrame:
    """Turn one weekly EIA series (from read_eia) into complete calendar-month averages.

    Each value is a daily rate for the 7 days ending on its Friday. Every day gets
    its week's rate and days are averaged by month, so a week split across two
    months counts in each by its number of days. Incomplete months are dropped.
    Raises ValueError on duplicate weeks.
    """
    # Checked here because averaging hides them: a duplicate plus a missing week
    # in the same month adds up to a full month with the wrong average.
    duplicates = raw.filter(pl.col("date").is_duplicated())["date"].unique().sort()
    if not duplicates.is_empty():
        raise ValueError(
            f"{len(duplicates)} duplicate weeks in EIA weekly {flow}: {duplicates.to_list()}"
        )

    daily_df = raw.select(
        day=pl.date_ranges(
            start=pl.col("date") - pl.duration(days=6), end=pl.col("date"), interval="1d"
        ),
        value=pl.col("value"),
    ).explode("day")

    return (
        daily_df.sort("day")
        .group_by_dynamic("day", every="1mo")
        .agg(
            pl.col("value").mean(),
            pl.len().alias("days_covered"),
        )
        .filter(pl.col("days_covered") == pl.col("day").dt.days_in_month())
        .select(
            month=pl.col("day"),
            flow=pl.lit(flow),
            source=pl.lit("eia_weekly"),
            value_kbd=pl.col("value"),
            assessment_code=pl.lit(None),
            days_covered=pl.col("days_covered"),
        )
        .cast(OUTPUT_SCHEMA)
    )
