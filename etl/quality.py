"""Data quality checks run before the snapshot is saved.

Checks: expected columns and units, no duplicate (month, flow, source), no gaps
in the month sequence, values in a plausible range, and fresh-enough latest
month. Each result is logged; a failed critical check stops the run.
"""

import polars as pl


def run(clean: pl.DataFrame) -> None:
    raise NotImplementedError("Step 5")
