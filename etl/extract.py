"""Download raw JODI and EIA files, untouched, into raw/<source>/<download date>/.

A file is only saved when its content hash differs from the previous copy, so
re-running the job on a day with no new release stores nothing new.
"""


def run() -> None:
    raise NotImplementedError("Step 3")
