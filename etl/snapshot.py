"""Save and read a committed snapshot of the clean table.

The raw downloads are too big for git, so the hosted dashboard reads this small
CSV instead. The daily Refresh data workflow rebuilds it; by hand: python -m etl.snapshot
"""

from pathlib import Path

import polars as pl

from etl import config, transform


def save(clean: pl.DataFrame, downloaded: str) -> Path | None:
    """Write the clean table as clean_<downloaded>.csv, replacing any older snapshot.

    Returns None without writing when the table matches the current snapshot,
    so a fresh download with no new numbers doesn't rename the file (and make
    a pointless commit in the scheduled refresh).
    """
    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    old_files = list(config.SNAPSHOT_DIR.glob("clean_*.csv"))
    text = clean.write_csv()
    if len(old_files) == 1 and old_files[0].read_text(encoding="utf-8") == text:
        return None
    for old in old_files:
        old.unlink()
    path = config.SNAPSHOT_DIR / f"clean_{downloaded}.csv"
    path.write_text(text, encoding="utf-8", newline="")
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
    print(f"Wrote {path}" if path else "Snapshot unchanged, nothing written")


if __name__ == "__main__":
    main()
