# EIA vs JODI backtest

How well do early-released EIA figures for US crude oil imports and exports line up with the JODI Oil statistics published later? This repo pulls both sources, aligns them, checks data quality and serves a dashboard showing bias, accuracy trends and anomalies.

## Data sources

| Source | Series | Units |
| --- | --- | --- |
| [JODI Oil World Database](https://www.jodidata.org/oil/database/data-downloads.aspx) | US, `CRUDEOIL`, `TOTIMPSB` / `TOTEXPSB` | thousand bbl/day (`KBD`) |
| [EIA Petroleum Supply Monthly](https://www.eia.gov/petroleum/data.php) | `MCRIMUS2` imports, `MCREXUS2` exports | thousand bbl/day |
| [EIA Weekly Petroleum Status Report](https://www.eia.gov/petroleum/data.php) | `WCRIMUS2` imports, `WCREXUS2` exports | thousand bbl/day |

## Pipeline

```
extract   -> raw files saved untouched to data/raw/<source>/<download date>/, only when they changed
transform -> one long table: month, flow, source, value_kbd (EIA weekly averaged into calendar months)
quality   -> schema, duplicates, gaps, nulls, value range, rows never shrinking, sources on time;
             row counts and results appended to data/logs/counts.csv and checks.csv
snapshot  -> data/snapshot/clean_<download date>.csv, rewritten only when the numbers changed
```

The **Refresh data** GitHub Actions workflow runs extract and snapshot every day at 07:00 UTC (or on demand from the Actions tab) and commits the snapshot when it changes. That commit redeploys the dashboard on Streamlit Community Cloud, which reads the snapshot because the raw downloads aren't in git. Run locally, the dashboard builds the table straight from the newest raw download.

## Layout

```
etl/        pipeline stages + config.py (source URLs and series IDs)
app/        Streamlit dashboard
tests/      pytest
.github/    CI (tests on every push) and the daily data refresh
```

## Running locally

```bash
uv sync                                    # install dependencies
uv run pytest                              # tests
uv run python -m etl.extract               # download new data
uv run python -m etl.snapshot              # rebuild the snapshot from the newest download
uv run python -m etl.run                   # all stages, quality checks included
uv run streamlit run app/streamlit_app.py
```

Set `DATA_DIR` to keep data somewhere other than `data/`.
