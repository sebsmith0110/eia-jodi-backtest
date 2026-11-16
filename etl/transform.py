"""Clean and align raw files into one long table.

Output columns: month, flow, source (jodi / eia_monthly / eia_weekly), value_kbd.
EIA weekly values are spread across each week's 7 days (weeks end on Friday)
and averaged by calendar month; the incomplete current month is dropped.
"""


def run() -> None:
    raise NotImplementedError("Step 4")
