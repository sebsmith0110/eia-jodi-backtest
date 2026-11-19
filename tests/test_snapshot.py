import datetime as dt

import polars as pl
import pytest

from etl import config, snapshot
from etl.transform import OUTPUT_SCHEMA


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch):
    """Point SNAPSHOT_DIR at a throwaway folder."""
    folder = tmp_path / "snapshot"
    monkeypatch.setattr(config, "SNAPSHOT_DIR", folder)
    return folder


def clean_table(latest_value: float | None = None) -> pl.DataFrame:
    """Two rows covering every column, nulls included."""
    return pl.DataFrame(
        {
            "month": [dt.date(2026, 7, 1), dt.date(2026, 8, 1)],
            "flow": ["imports", "imports"],
            "source": ["jodi", "eia_weekly"],
            "value_kbd": [6500.0, latest_value],
            "assessment_code": [1, None],
            "days_covered": [None, 31],
        },
        schema=OUTPUT_SCHEMA,
    )


def test_round_trip_keeps_values_and_types(snapshot_dir):
    snapshot.save(clean_table(), "2026-11-16")

    clean, downloaded = snapshot.load()

    assert downloaded == "2026-11-16"
    assert clean.schema == OUTPUT_SCHEMA
    assert clean.equals(clean_table())


def test_changed_table_replaces_the_older_snapshot(snapshot_dir):
    snapshot.save(clean_table(), "2026-11-09")
    snapshot.save(clean_table(latest_value=6400.0), "2026-11-16")

    assert [p.name for p in snapshot_dir.iterdir()] == ["clean_2026-11-16.csv"]


def test_unchanged_table_keeps_the_older_snapshot(snapshot_dir):
    snapshot.save(clean_table(), "2026-11-09")

    assert snapshot.save(clean_table(), "2026-11-16") is None
    assert [p.name for p in snapshot_dir.iterdir()] == ["clean_2026-11-09.csv"]


def test_windows_line_endings_still_count_as_unchanged(snapshot_dir):
    # Git on Windows may check the committed CSV out with CRLF endings
    snapshot_dir.mkdir()
    crlf = clean_table().write_csv().replace("\n", "\r\n")
    (snapshot_dir / "clean_2026-11-09.csv").write_bytes(crlf.encode())

    assert snapshot.save(clean_table(), "2026-11-16") is None


def test_load_fails_clearly_without_a_snapshot(snapshot_dir):
    with pytest.raises(FileNotFoundError, match="snapshot"):
        snapshot.load()
