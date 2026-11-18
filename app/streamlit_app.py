"""Dashboard: streamlit run app/streamlit_app.py

Backtest of EIA's early US crude trade estimates against JODI, for market analysts.
Two pages: an Overview that answers the key questions on one screen, and Method &
data. Shared pieces live in app/ui.py; numbers come from etl.metrics and app.prep.
"""

import sys
from pathlib import Path

import streamlit as st

# `streamlit run` only puts app/ on the import path, not the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

st.set_page_config(page_title="EIA vs JODI backtest", layout="wide")

page = st.navigation(
    [
        st.Page(
            "app_pages/overview.py",
            title="Overview",
            icon=":material/dashboard:",
            default=True,
            help="Key findings and the four questions: lean, tracking, trend, outliers.",
        ),
        st.Page(
            "app_pages/method.py",
            title="Method & data",
            icon=":material/menu_book:",
            help="How errors and flags are calculated, known data issues, full statistics "
            "and the underlying data as CSV.",
        ),
    ],
    position="top",
)
page.run()
