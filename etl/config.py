"""Source definitions and settings shared by every pipeline stage."""

import os
from pathlib import Path

# Where raw downloads land locally. Mirrors the S3 layout: raw/<source>/<download date>/
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
RAW_DIR = DATA_DIR / "raw"

# Postgres connection: local Docker in development, RDS in production.
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/backtest"
)

# --- JODI Oil World Database --------------------------------------------------
# Annual "Primary" CSVs, no login needed.
# https://www.jodidata.org/oil/database/data-downloads.aspx
JODI_BASE_URL = "https://www.jodidata.org/_resources/files/downloads/oil-data/annual-csv/primary"
JODI_START_YEAR = 2015
JODI_COUNTRY = "US"
JODI_PRODUCT = "CRUDEOIL"
JODI_UNIT = "KBD"  # thousand barrels per day
JODI_FLOWS = {"TOTIMPSB": "imports", "TOTEXPSB": "exports"}
JODI_MISSING = "-"  # missing values are the literal string "-"


def jodi_url(year: int, current_year: int) -> str:
    """The current year's file has a different name from past years'."""
    name = f"primaryyear{year}.csv" if year == current_year else f"{year}.csv"
    return f"{JODI_BASE_URL}/{name}"


# --- EIA ----------------------------------------------------------------------
# All series are US total crude oil, thousand barrels per day.
# https://www.eia.gov/petroleum/data.php
EIA_XLS_URL = "https://www.eia.gov/dnav/pet/hist_xls/{series_id}{freq}.xls"
EIA_SERIES = {
    # series_id: (flow, source label, frequency code used in the download URL)
    "MCRIMUS2": ("imports", "eia_monthly", "m"),  # Petroleum Supply Monthly
    "MCREXUS2": ("exports", "eia_monthly", "m"),
    "WCRIMUS2": ("imports", "eia_weekly", "w"),  # Weekly Petroleum Status Report
    "WCREXUS2": ("exports", "eia_weekly", "w"),
}
