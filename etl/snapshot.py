"""Save and read a committed snapshot of the clean table.

The raw downloads are too big for git, so the hosted dashboard reads this small
CSV instead. The daily Refresh data workflow rebuilds it; by hand: python -m etl.snapshot

Every snapshot saved is also copied to archive/, named by when it was saved (UTC),
and never deleted. That keeps a record of exactly what the dashboard showed when,
and of how the sources revise their own figures over time.
"""

import datetime as dt
from pathlib import Path

import polars as pl

from etl import config, transform

ARCHIVE_DIR_NAME = "archive"


def save(clean: pl.DataFrame, downloaded: str, saved_at: dt.datetime | None = None) -> Path | None:
    """Write the clean table as clean_<downloaded>.csv, replacing the previous snapshot,
    and add a copy to the archive as clean_<saved_at, UTC>.csv.

    Returns None without writing either when the table matches the current snapshot,
    so a fresh download with no new numbers doesn't rename the file (and make
    a pointless commit in the scheduled refresh).
    """
    saved_at = saved_at or dt.datetime.now(dt.UTC)
    archive = archive_dir()
    archive.mkdir(parents=True, exist_ok=True)
    old_files = list(config.SNAPSHOT_DIR.glob("clean_*.csv"))
    text = clean.write_csv()
    if len(old_files) == 1 and old_files[0].read_text(encoding="utf-8") == text:
        return None
    for old in old_files:
        old.unlink()  # already in the archive, from when it was saved
    path = config.SNAPSHOT_DIR / f"clean_{downloaded}.csv"
    path.write_text(text, encoding="utf-8", newline="")
    archived = archive / f"clean_{saved_at:%Y-%m-%d_%H%M%S}.csv"
    archived.write_text(text, encoding="utf-8", newline="")
    return path


def load() -> tuple[pl.DataFrame, str]:
    """The newest snapshot's clean table (typed as OUTPUT_SCHEMA) and its download date."""
    files = sorted(config.SNAPSHOT_DIR.glob("clean_*.csv"))
    if not files:
        raise FileNotFoundError(f"No clean_*.csv snapshot in {config.SNAPSHOT_DIR}")
    path = files[-1]  # ISO dates in the name sort in date order
    return read(path), path.stem.removeprefix("clean_")


def archived() -> list[Path]:
    """Every archived snapshot, oldest first."""
    return sorted(archive_dir().glob("clean_*.csv"))


def read(path: Path) -> pl.DataFrame:
    """One snapshot file (current or archived) as the clean table, typed as OUTPUT_SCHEMA."""
    return pl.read_csv(path, schema_overrides=transform.OUTPUT_SCHEMA).select(
        list(transform.OUTPUT_SCHEMA)
    )


def archive_dir() -> Path:
    """Where every saved snapshot is kept (read at call time, so tests can move it)."""
    return config.SNAPSHOT_DIR / ARCHIVE_DIR_NAME


def main() -> None:
    """Rebuild the snapshot from the latest raw downloads."""
    path = save(transform.run(), transform.get_latest_raw_dir("jodi").name)
    print(f"Wrote {path}" if path else "Snapshot unchanged, nothing written")


if __name__ == "__main__":
    main()
