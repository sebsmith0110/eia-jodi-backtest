"""Source definitions and settings shared by every pipeline stage."""

import os
import re
from datetime import date
from pathlib import Path

# Where raw downloads land: raw/<source>/<download date>/. Override with the
# DATA_DIR environment variable, e.g. to test against a throwaway folder.
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
RAW_DIR = DATA_DIR / "raw"
# Committed copy of the clean table, so the dashboard runs where raw data isn't
# available (Streamlit Community Cloud). File name: clean_<download date>.csv
SNAPSHOT_DIR = DATA_DIR / "snapshot"
# Quality run logs, appended to every run: counts.csv (row counts per series) and
# checks.csv (each check's result). Committed, like the snapshot.
LOG_DIR = DATA_DIR / "logs"

# --- JODI Oil World Database --------------------------------------------------
# Annual "Primary" CSVs, no login needed.
# https://www.jodidata.org/oil/database/data-downloads.aspx
JODI_BASE_URL = "https://www.jodidata.org/_resources/files/downloads/oil-data/annual-csv/primary"
JODI_START_YEAR = 2002  # JODI's first month is Jan 2002
JODI_COUNTRY = "US"
JODI_PRODUCT = "CRUDEOIL"
JODI_UNIT = "KBD"  # thousand barrels per day
JODI_FLOWS = {"TOTIMPSB": "imports", "TOTEXPSB": "exports"}
# Markers for "no number" in OBS_VALUE: "-" and ".." (not available), "x" (not applicable),
# "N/A" (2002-2008 files only). Any other text makes the read fail loudly, on purpose.
JODI_MISSING = ["-", "..", "x", "N/A"]
# Exact columns every JODI primary CSV must have (any order).
JODI_COLUMNS = [
    "REF_AREA",
    "TIME_PERIOD",
    "ENERGY_PRODUCT",
    "FLOW_BREAKDOWN",
    "UNIT_MEASURE",
    "OBS_VALUE",
    "ASSESSMENT_CODE",
]
# Name raw JODI files are saved under (extract) and read back by (transform): primary_2025.csv
JODI_FILE_PATTERN = re.compile(r"primary_\d{4}\.csv")


def jodi_url(year: int, current_year: int) -> str:
    """The current year's file has a different name from past years'."""
    name = f"primaryyear{year}.csv" if year == current_year else f"{year}.csv"
    return f"{JODI_BASE_URL}/{name}"


# --- EIA ----------------------------------------------------------------------
# All series are US total crude oil, thousand barrels per day.
# https://www.eia.gov/petroleum/data.php
EIA_UNIT = "Thousand Barrels per Day"  # must appear in each file's value header
EIA_XLS_URL = "https://www.eia.gov/dnav/pet/hist_xls/{series_id}{freq}.xls"
EIA_SERIES = {
    # series_id: (flow, source label, frequency code used in the download URL)
    "MCRIMUS2": ("imports", "eia_monthly", "m"),  # Petroleum Supply Monthly
    "MCREXUS2": ("exports", "eia_monthly", "m"),
    "WCRIMUS2": ("imports", "eia_weekly", "w"),  # Weekly Petroleum Status Report
    "WCREXUS2": ("exports", "eia_weekly", "w"),
}


# --- Quality checks -------------------------------------------------------------
# Sense limits for any US crude import or export value, in kbd. Real values run
# from about 2 (pre-2015 exports) to about 10,800, so these only catch gross errors
# such as a change of units.
VALUE_MIN_KBD = 0
VALUE_MAX_KBD = 50_000

# Release schedules, for warning when a source is late. The Refresh data workflow runs
# daily at 07:00 UTC, before any of these releases, so data released on a day is due by
# the next morning's run; still missing then means it's a day late. Late only warns.
#
# EIA weekly (WPSR): Wednesdays for the week ending the previous Friday; Thursdays in
# holiday weeks. Week-ending dates of those weeks, 2026-2027, from
# https://www.eia.gov/petroleum/supply/weekly/schedule.php
EIA_WEEKLY_HOLIDAY_WEEKS = {
    date(2026, 1, 16), date(2026, 2, 13), date(2026, 5, 22),
    date(2026, 9, 4), date(2026, 10, 9), date(2026, 11, 6),
    date(2027, 1, 15), date(2027, 2, 12), date(2027, 5, 28),
    date(2027, 7, 2), date(2027, 9, 3), date(2027, 10, 8),
}  # fmt: skip
# EIA monthly (PSM): the last weekday of each month, with data for two months earlier
# (30 Oct 2026 brought Aug 2026; next release 30 Nov 2026).
EIA_MONTHLY_LAG_MONTHS = 2
# JODI: first update of each month, with data for two months earlier (19 Nov 2026
# brought Sep 2026). Dates from https://www.jodidata.org/oil/support/update-calendar.aspx;
# add each year's when JODI publishes them. Months not listed assume the 22nd.
JODI_RELEASE_DATES = {
    date(2026, 1, 21), date(2026, 2, 19), date(2026, 3, 18), date(2026, 4, 21),
    date(2026, 5, 20), date(2026, 6, 22), date(2026, 7, 21), date(2026, 8, 20),
    date(2026, 9, 22), date(2026, 10, 21), date(2026, 11, 19), date(2026, 12, 21),
}  # fmt: skip
JODI_DEFAULT_RELEASE_DAY = 22
JODI_LAG_MONTHS = 2
