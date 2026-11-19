from datetime import date

import pytest

from etl import config, extract


@pytest.fixture
def raw_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    return tmp_path


def test_first_download_is_saved(raw_dir):
    folder = extract.save_if_changed("eia", {"A.xls": b"1"}, date(2026, 11, 19))
    assert folder == raw_dir / "eia" / "2026-11-19"
    assert (folder / "A.xls").read_bytes() == b"1"


def test_unchanged_download_is_not_saved(raw_dir):
    extract.save_if_changed("eia", {"A.xls": b"1"}, date(2026, 11, 19))
    assert extract.save_if_changed("eia", {"A.xls": b"1"}, date(2026, 11, 26)) is None
    assert not (raw_dir / "eia" / "2026-11-26").exists()


def test_changed_download_saves_a_complete_set(raw_dir):
    extract.save_if_changed("eia", {"A.xls": b"1", "B.xls": b"1"}, date(2026, 11, 19))
    folder = extract.save_if_changed("eia", {"A.xls": b"1", "B.xls": b"2"}, date(2026, 11, 26))
    assert sorted(p.name for p in folder.iterdir()) == ["A.xls", "B.xls"]


def test_same_day_rerun_replaces_folder(raw_dir):
    extract.save_if_changed("eia", {"A.xls": b"1"}, date(2026, 11, 19))
    folder = extract.save_if_changed("eia", {"A.xls": b"2"}, date(2026, 11, 19))
    assert (folder / "A.xls").read_bytes() == b"2"
    assert not list((raw_dir / "eia").glob("*.partial"))


def test_jodi_falls_back_to_other_name_and_skips_unpublished_current_year(monkeypatch):
    monkeypatch.setattr(config, "JODI_START_YEAR", 2025)
    # 2025 is still under its "current year" name; 2026 isn't published yet.
    published = {config.jodi_url(2025, current_year=2025): b"2025 data"}
    monkeypatch.setattr(extract, "fetch", published.get)
    assert extract.download_jodi(current_year=2026) == {"primary_2025.csv": b"2025 data"}


def test_jodi_missing_past_year_raises(monkeypatch):
    monkeypatch.setattr(config, "JODI_START_YEAR", 2025)
    monkeypatch.setattr(extract, "fetch", lambda url: None)
    with pytest.raises(FileNotFoundError, match="2025"):
        extract.download_jodi(current_year=2026)
