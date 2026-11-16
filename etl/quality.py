"""Data quality checks run before every load.

Checks: expected columns and units, no duplicate (month, flow, source), no gaps
in the month sequence, values in a plausible range, and fresh-enough latest
month. Each result is logged; a failed critical check stops the load.
"""


def run() -> None:
    raise NotImplementedError("Step 5")
