from datetime import date
from pathlib import Path

import polars as pl

from etl import config


def run() -> None:
    raise NotImplementedError("Step 4")


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


def read_jodi_files(folder: Path) -> pl.DataFrame:
    """Read every JODI primary CSV in a folder, keeping only US crude imports/exports in kbd.

    Filtering happens inside the lazy scan, so the full all-country files are never
    loaded into memory. Missing-value markers (config.JODI_MISSING) become nulls.

    Raises ValueError if the column names aren't exactly config.JODI_COLUMNS (order
    doesn't matter). Polars already rejects files that differ from each other; this
    catches JODI changing the format of every file at once.
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

    # Reads only the header. Checked before anything else because schema_overrides
    # silently skips columns that don't exist, e.g. if OBS_VALUE were renamed.
    columns = set(lazy.collect_schema().names())
    expected = set(config.JODI_COLUMNS)
    if columns != expected:
        raise ValueError(
            f"JODI columns changed in {folder}. Missing: {sorted(expected - columns)}, "
            f"unexpected: {sorted(columns - expected)}"
        )

    return (
        lazy.filter(
            pl.col("REF_AREA") == config.JODI_COUNTRY,
            pl.col("ENERGY_PRODUCT") == config.JODI_PRODUCT,
            pl.col("UNIT_MEASURE") == config.JODI_UNIT,
            pl.col("FLOW_BREAKDOWN").is_in(list(config.JODI_FLOWS)),
        )
        .collect()
    )
