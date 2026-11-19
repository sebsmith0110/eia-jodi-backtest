"""Source definitions and settings shared by every pipeline stage."""

import os
import re
from pathlib import Path

# Where raw downloads land: raw/<source>/<download date>/. Override with the
# DATA_DIR environment variable, e.g. to test against a throwaway folder.
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
RAW_DIR = DATA_DIR / "raw"
# Committed copy of the clean table, so the dashboard runs where raw data isn't
# available (Streamlit Community Cloud). File name: clean_<download date>.csv
SNAPSHOT_DIR = DATA_DIR / "snapshot"

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
