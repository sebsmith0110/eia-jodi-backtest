"""Save and read a committed snapshot of the clean table.

The raw downloads are too big for git, so the hosted dashboard reads this small
CSV instead. Refresh it after a new download: python -m etl.snapshot
"""

from pathlib import Path

import polars as pl

from etl import config, transform


def save(clean: pl.DataFrame, downloaded: str) -> Path:
    """Write the clean table as clean_<downloaded>.csv, replacing any older snapshot."""
    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for old in config.SNAPSHOT_DIR.glob("clean_*.csv"):
        old.unlink()
    path = config.SNAPSHOT_DIR / f"clean_{downloaded}.csv"
    clean.write_csv(path)
    return path


def load() -> tuple[pl.DataFrame, str]:
    """The newest snapshot's clean table (typed as OUTPUT_SCHEMA) and its download date."""
    files = sorted(config.SNAPSHOT_DIR.glob("clean_*.csv"))
    if not files:
        raise FileNotFoundError(f"No clean_*.csv snapshot in {config.SNAPSHOT_DIR}")
    path = files[-1]  # ISO dates in the name sort in date order
    clean = pl.read_csv(path, schema_overrides=transform.OUTPUT_SCHEMA).select(
        list(transform.OUTPUT_SCHEMA)
    )
    return clean, path.stem.removeprefix("clean_")


def main() -> None:
    """Rebuild the snapshot from the latest raw downloads."""
    path = save(transform.run(), transform.get_latest_raw_dir("jodi").name)
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
