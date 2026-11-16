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
extract   -> raw files saved untouched to raw/<source>/<download date>/
transform -> one long table: month, flow, source, value_kbd
quality   -> schema, units, duplicates, gaps, ranges, freshness
load      -> Postgres: clean table, metrics, run log
```

In production the same code runs as a scheduled ECS Fargate task (EventBridge, weekly after EIA's Wednesday release), with raw files in S3 and Postgres on RDS.

## Layout

```
etl/        pipeline stages + config.py (source URLs and series IDs)
app/        Streamlit dashboard
tests/      pytest
Dockerfile  ETL image for ECS
```

## Running locally

```bash
uv sync                          # install dependencies
uv run pytest                    # tests
uv run python -m etl.run         # run the pipeline
uv run streamlit run app/streamlit_app.py
```

Settings come from environment variables; see `.env.example`.
