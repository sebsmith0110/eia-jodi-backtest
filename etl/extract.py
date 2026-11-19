"""Download raw JODI and EIA files, untouched, into raw/<source>/<download date>/.

Each dated folder is a complete set for its source, because transform reads
only the newest folder. A new folder is only written when at least one file's
content differs from the newest existing set, so re-running the job on a day
with no new release stores nothing new. Run on its own: python -m etl.extract
"""

import hashlib
import shutil
from datetime import date
from pathlib import Path

import requests

from etl import config
from etl.transform import get_latest_raw_dir

TIMEOUT_SECONDS = 120


def run() -> None:
    """Download both sources and save any that changed since the last download."""
    today = date.today()
    for source, files in (("jodi", download_jodi(today.year)), ("eia", download_eia())):
        saved = save_if_changed(source, files, today)
        print(f"{source}: {'saved to ' + str(saved) if saved else 'unchanged, nothing saved'}")


def fetch(url: str) -> bytes | None:
    """GET a URL and return its body, or None on 404. Other HTTP errors raise."""
    response = requests.get(url, timeout=TIMEOUT_SECONDS)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def download_jodi(current_year: int) -> dict[str, bytes]:
    """Every yearly JODI primary CSV since config.JODI_START_YEAR, keyed by saved file name.

    JODI names the current year's file differently (see config.jodi_url), and
    around New Year the switch-over date isn't documented, so each year falls
    back to the other naming. The current year may not be published yet in
    early January, so only that year is allowed to be missing.
    """
    files = {}
    for year in range(config.JODI_START_YEAR, current_year + 1):
        past_name = config.jodi_url(year, current_year=year + 1)  # .../2025.csv
        latest_name = config.jodi_url(year, current_year=year)  # .../primaryyear2025.csv
        urls = [latest_name, past_name] if year == current_year else [past_name, latest_name]
        content = next((c for c in map(fetch, urls) if c is not None), None)  # lazy: stops at hit
        if content is None:
            if year == current_year:
                continue
            raise FileNotFoundError(f"No JODI file for {year} at {urls}")
        files[f"primary_{year}.csv"] = content
    return files


def download_eia() -> dict[str, bytes]:
    """Every series in config.EIA_SERIES, keyed by saved file name (MCRIMUS2.xls)."""
    files = {}
    for series_id, (_, _, freq) in config.EIA_SERIES.items():
        url = config.EIA_XLS_URL.format(series_id=series_id, freq=freq)
        content = fetch(url)
        if content is None:
            raise FileNotFoundError(f"EIA series {series_id} not found at {url}")
        files[f"{series_id}.xls"] = content
    return files


def save_if_changed(source: str, files: dict[str, bytes], today: date) -> Path | None:
    """Write files to raw/<source>/<today>/ unless they match the newest saved set.

    Returns the folder written, or None when nothing changed.
    """
    try:
        previous = get_latest_raw_dir(source)
    except FileNotFoundError:
        previous = None
    if previous is not None and _hashes(files) == _folder_hashes(previous):
        return None

    # Write to a temp folder then rename, so a failed run never leaves a half
    # set that transform would pick up as the newest download.
    target = config.RAW_DIR / source / today.isoformat()
    staging = target.with_name(target.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for name, content in files.items():
        (staging / name).write_bytes(content)
    shutil.rmtree(target, ignore_errors=True)  # same-day re-run with a newer release
    staging.rename(target)
    return target


def _hashes(files: dict[str, bytes]) -> dict[str, str]:
    """SHA-256 of each file's content, keyed by file name."""
    return {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}


def _folder_hashes(folder: Path) -> dict[str, str]:
    """SHA-256 of each file in a saved download folder, keyed by file name."""
    return _hashes({p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()})


if __name__ == "__main__":
    run()
