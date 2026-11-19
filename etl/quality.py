"""Data quality checks on the clean table, run before the snapshot is saved.

Each check is a small function that takes the clean table and returns a
CheckResult. run() runs them all, prints a report, appends the row counts and
check results to the run logs, and raises QualityError if any critical check
failed, so a bad table never reaches the dashboard.

The raw files' format (JODI columns, EIA units and series IDs) is checked while
they are read, in transform.py; these checks judge the table that comes out,
plus whether each source has published on schedule.
"""

import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from etl import config
from etl.transform import OUTPUT_SCHEMA, read_eia

SERIES = ["source", "flow"]  # one series = one source's imports or exports
SOURCES = {"jodi", *(source for _, source, _ in config.EIA_SERIES.values())}
FLOWS = set(config.JODI_FLOWS.values())
REQUIRED = ["month", "flow", "source"]  # columns that must never be null
MAX_EXAMPLES = 5  # rows quoted in a failure message


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one check. A failed critical check stops the run; others warn."""

    name: str
    passed: bool
    critical: bool
    detail: str

    @property
    def status(self) -> str:
        """PASS, FAIL (stops the run) or WARN."""
        if self.passed:
            return "PASS"
        return "FAIL" if self.critical else "WARN"


class QualityError(Exception):
    """Raised by run() when at least one critical check failed."""

    def __init__(self, failed: list[CheckResult]):
        self.failed = failed
        super().__init__("; ".join(f"{r.name}: {r.detail}" for r in failed))


def run(
    clean: pl.DataFrame,
    latest_week: dt.date,
    today: dt.date | None = None,
    log_dir: Path | None = None,
) -> list[CheckResult]:
    """Run every check, print a report and raise QualityError on a critical failure.

    latest_week is the end date of EIA's latest weekly figure (see latest_eia_week),
    which the clean table can't show: it keeps only complete months. With log_dir,
    row counts are compared with the previous run's, both logs are appended to and
    status.json (read by the dashboard) is rewritten, failures included, before
    anything is raised.
    """
    today = today or dt.date.today()
    run_at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    schema = check_schema(clean)
    if not schema.passed:
        # The other checks assume the expected columns, so they'd crash rather than report.
        if log_dir:
            append_check_log(log_dir, run_at, [schema])
            write_status(log_dir, run_at, [schema], latest={})
        raise QualityError([schema])

    counts = row_counts(clean)
    results = [
        schema,
        *(check(clean) for check in CHECKS),
        check_rows_not_shrinking(counts, previous_counts(log_dir) if log_dir else None),
        check_weekly_on_time(latest_week, today),
        *(check_monthly_on_time(clean, source, today) for source in ("eia_monthly", "jodi")),
    ]
    print(report(counts, results))
    annotate_github(results)
    if log_dir:
        latest = {
            "eia_weekly": latest_week,
            "eia_monthly": latest_month(clean, "eia_monthly"),
            "jodi": latest_month(clean, "jodi"),
        }
        append_count_log(log_dir, run_at, counts)
        append_check_log(log_dir, run_at, results)
        write_status(log_dir, run_at, results, latest)
    failed = [r for r in results if r.status == "FAIL"]
    if failed:
        raise QualityError(failed)
    return results


def report(counts: pl.DataFrame, results: list[CheckResult]) -> str:
    """Row counts per series, then one line per check."""
    lines = ["Row counts:"]
    for row in counts.iter_rows(named=True):
        lines.append(
            f"  {row['source']:<12} {row['flow']:<8} {row['rows']:>4} rows  "
            f"{row['first_month']} to {row['last_month']}  {row['missing_months']} missing "
            f"months  {row['duplicate_rows']} duplicates  {row['null_values']} null values"
        )
    lines.append("Checks:")
    lines += [f"  {r.status:<4}  {r.name:<20} {r.detail}" for r in results]
    return "\n".join(lines)


def row_counts(clean: pl.DataFrame) -> pl.DataFrame:
    """Per series: rows, first and last month, and counts of missing months,
    duplicate rows and null values. One row per series, as logged each run."""
    gaps = missing_months(clean).group_by(SERIES).agg(missing_months=pl.len())
    return (
        clean.group_by(SERIES)
        .agg(
            rows=pl.len(),
            first_month=pl.col("month").min(),
            last_month=pl.col("month").max(),
            duplicate_rows=pl.len() - pl.col("month").n_unique(),
            null_values=pl.col("value_kbd").null_count(),
        )
        .join(gaps, on=SERIES, how="left")
        .select(
            *SERIES,
            "rows",
            "first_month",
            "last_month",
            pl.col("missing_months").fill_null(0),  # series with no gaps aren't in `gaps`
            "duplicate_rows",
            "null_values",
        )
        .sort(SERIES)
    )


# --- run logs -----------------------------------------------------------------


def append_count_log(log_dir: Path, run_at: str, counts: pl.DataFrame) -> None:
    """Add this run's row counts to log_dir/counts.csv, one row per series."""
    append_csv(log_dir / "counts.csv", counts.select(pl.lit(run_at).alias("run_at"), pl.all()))


def append_check_log(log_dir: Path, run_at: str, results: list[CheckResult]) -> None:
    """Add this run's check results to log_dir/checks.csv, one row per check."""
    rows = [(run_at, r.name, r.status, r.detail) for r in results]
    table = pl.DataFrame(rows, schema=["run_at", "check", "status", "detail"], orient="row")
    append_csv(log_dir / "checks.csv", table)


def previous_counts(log_dir: Path) -> pl.DataFrame | None:
    """The last logged run's row counts, or None before the first run."""
    path = log_dir / "counts.csv"
    if not path.exists():
        return None
    log = pl.read_csv(path)
    return log.filter(pl.col("run_at") == pl.col("run_at").max())


def write_status(
    log_dir: Path, run_at: str, results: list[CheckResult], latest: dict[str, dt.date | None]
) -> None:
    """Overwrite log_dir/status.json with this run's summary, for the dashboard: when
    the check ran, each source's latest period, its release date and whether it's
    late, and every check that didn't pass."""
    late = {r.name.removesuffix(" on time") for r in results if r.name.endswith(" on time")}
    late &= {r.name.removesuffix(" on time") for r in results if not r.passed}
    status = {
        "checked_at": run_at,
        "passed": not any(r.status == "FAIL" for r in results),
        "sources": {
            source: {
                "latest": period.isoformat() if period else None,
                "released": released_on(source, period).isoformat() if period else None,
                "late": source in late,
            }
            for source, period in latest.items()
        },
        "problems": [
            {"check": r.name, "status": r.status, "detail": r.detail}
            for r in results
            if not r.passed
        ],
    }
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")


def annotate_github(results: list[CheckResult]) -> None:
    """On GitHub Actions, turn each warning and failure into an annotation, so it shows
    on the workflow run's summary page rather than only in its log."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for r in results:
        if not r.passed:
            level = "error" if r.critical else "warning"
            print(f"::{level} title={r.name}::{r.detail}")


def append_csv(path: Path, table: pl.DataFrame) -> None:
    """Append rows to a CSV, writing the header only when the file is new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as file:
        table.write_csv(file, include_header=new)


# --- checks -------------------------------------------------------------------


def check_schema(clean: pl.DataFrame) -> CheckResult:
    """Columns and types match OUTPUT_SCHEMA; flow, source and month hold valid values."""
    problems = []
    if dict(clean.schema) != OUTPUT_SCHEMA:
        problems.append(f"columns are {dict(clean.schema)}, expected {OUTPUT_SCHEMA}")
    else:
        unknown_sources = set(clean["source"].drop_nulls()) - SOURCES
        unknown_flows = set(clean["flow"].drop_nulls()) - FLOWS
        not_month_start = clean.filter(pl.col("month").dt.day() != 1).height
        if unknown_sources:
            problems.append(f"unknown sources {sorted(unknown_sources)}")
        if unknown_flows:
            problems.append(f"unknown flows {sorted(unknown_flows)}")
        if not_month_start:
            problems.append(f"{not_month_start} months not on the 1st")
    return CheckResult(
        "schema",
        passed=not problems,
        critical=True,
        detail="; ".join(problems) or f"{len(OUTPUT_SCHEMA)} columns as expected",
    )


def check_completeness(clean: pl.DataFrame) -> CheckResult:
    """Every month is present between each series' first and last month."""
    missing = missing_months(clean)
    return CheckResult(
        "completeness",
        passed=missing.is_empty(),
        critical=True,
        detail=(
            f"{missing.height} missing months: {examples(missing)}"
            if missing.height
            else f"no gaps in {clean.select(SERIES).n_unique()} series"
        ),
    )


def check_duplicates(clean: pl.DataFrame) -> CheckResult:
    """No (month, flow, source) appears twice."""
    keys = ["month", *SERIES]
    duplicates = clean.filter(pl.struct(keys).is_duplicated()).select(keys).unique().sort(keys)
    return CheckResult(
        "duplicates",
        passed=duplicates.is_empty(),
        critical=True,
        detail=(
            f"{duplicates.height} duplicated keys: {examples(duplicates)}"
            if duplicates.height
            else "none"
        ),
    )


def check_required_fields(clean: pl.DataFrame) -> CheckResult:
    """month, flow and source are never null: a row without them can't be placed."""
    nulls = {col: clean[col].null_count() for col in REQUIRED if clean[col].null_count()}
    return CheckResult(
        "required fields",
        passed=not nulls,
        critical=True,
        detail=f"nulls in {nulls}" if nulls else f"no nulls in {', '.join(REQUIRED)}",
    )


def check_missing_values(clean: pl.DataFrame) -> CheckResult:
    """Counts months with no value. Only warns: JODI sometimes publishes a month with
    no number, and the comparison already skips those months."""
    missing = (
        clean.filter(pl.col("value_kbd").is_null()).select("month", *SERIES).sort("month", *SERIES)
    )
    return CheckResult(
        "missing values",
        passed=missing.is_empty(),
        critical=False,
        detail=f"{missing.height} months with no value: {examples(missing)}"
        if missing.height
        else "none",
    )


def check_value_range(clean: pl.DataFrame) -> CheckResult:
    """Every value is within config.VALUE_MIN_KBD to VALUE_MAX_KBD (nulls are skipped)."""
    low, high = config.VALUE_MIN_KBD, config.VALUE_MAX_KBD
    out_of_range = clean.filter(~pl.col("value_kbd").is_between(low, high)).select(
        "month", *SERIES, "value_kbd"
    )
    values = clean["value_kbd"].drop_nulls()
    return CheckResult(
        "value range",
        passed=out_of_range.is_empty(),
        critical=True,
        detail=(
            f"{out_of_range.height} values outside {low:,}-{high:,} kbd: {examples(out_of_range)}"
            if out_of_range.height
            else f"all within {low:,}-{high:,} kbd "
            f"(actual {values.min() or 0:,.1f}-{values.max() or 0:,.1f})"
        ),
    )


def check_weekly_on_time(latest_week: dt.date, today: dt.date) -> CheckResult:
    """EIA weekly has the week its latest release (before today) covered. Warns only."""
    due_week = latest_due_eia_week(today)
    release = eia_week_release(due_week)
    return CheckResult(
        "eia_weekly on time",
        passed=latest_week >= due_week,
        critical=False,
        detail=(
            f"latest week ends {latest_week} (due: week ending {due_week}, "
            f"released {release:%a %d %b})"
            if latest_week >= due_week
            else f"LATE: latest week ends {latest_week}, but the week ending {due_week} "
            f"was released {release:%a %d %b}"
        ),
    )


def check_monthly_on_time(clean: pl.DataFrame, source: str, today: dt.date) -> CheckResult:
    """A monthly source (eia_monthly or jodi) has the month its latest release before
    today covered. Warns only.

    Uses the flow that's furthest behind, so one flow stalling is enough to warn,
    and only months with a value: a published row with no number isn't new data.
    """
    release_date, lag = monthly_schedule(source)
    release = latest_release_before(today, release_date)
    due_month = add_months(release.replace(day=1), -lag)
    latest = latest_month(clean, source)
    on_time = latest is not None and latest >= due_month
    shown = f"{latest:%b %Y}" if latest else "none"
    return CheckResult(
        f"{source} on time",
        passed=on_time,
        critical=False,
        detail=(
            f"latest month {shown} (due: {due_month:%b %Y}, released {release:%a %d %b})"
            if on_time
            else f"LATE: latest month {shown}, but {due_month:%b %Y} "
            f"was released {release:%a %d %b}"
        ),
    )


def check_rows_not_shrinking(counts: pl.DataFrame, previous: pl.DataFrame | None) -> CheckResult:
    """No series has fewer rows than in the previous run: published history doesn't
    disappear, so a drop means a lost file or a broken read."""
    if previous is None:
        return CheckResult("rows not shrinking", True, True, "first logged run, nothing to compare")
    shrunk = (
        counts.join(
            previous.select(*SERIES, previous_rows="rows"), on=SERIES, how="full", coalesce=True
        )
        .filter(pl.col("rows").fill_null(0) < pl.col("previous_rows"))
        .select(*SERIES, "previous_rows", pl.col("rows").fill_null(0))
    )
    return CheckResult(
        "rows not shrinking",
        passed=shrunk.is_empty(),
        critical=True,
        detail=(
            f"{shrunk.height} series lost rows (series, before, now): {examples(shrunk)}"
            if shrunk.height
            else "no series has fewer rows than last run"
        ),
    )


# Run after check_schema has passed, in this order.
CHECKS = [
    check_required_fields,
    check_duplicates,
    check_completeness,
    check_missing_values,
    check_value_range,
]


# --- helpers ------------------------------------------------------------------


def latest_eia_week(eia_dir: Path) -> dt.date:
    """End date of the latest week in the EIA weekly files, from the series furthest behind."""
    weekly = [s for s, (_, source, _) in config.EIA_SERIES.items() if source == "eia_weekly"]
    return min(read_eia(eia_dir / f"{series_id}.xls")["date"].max() for series_id in weekly)


def latest_month(clean: pl.DataFrame, source: str) -> dt.date | None:
    """A monthly source's latest month with a value, from the flow furthest behind."""
    return (
        clean.filter(pl.col("source") == source, pl.col("value_kbd").is_not_null())
        .group_by("flow")
        .agg(pl.col("month").max())["month"]
        .min()
    )


def monthly_schedule(source: str):
    """(release date function, months between a data month and its release) for
    eia_monthly or jodi. release_date(year, month) gives that month's release."""
    return {
        "eia_monthly": (eia_monthly_release, config.EIA_MONTHLY_LAG_MONTHS),
        "jodi": (jodi_release, config.JODI_LAG_MONTHS),
    }[source]


def released_on(source: str, period: dt.date) -> dt.date:
    """When a source's period was (or will be) released, by its schedule: a week's
    end date for eia_weekly, a month for eia_monthly and jodi."""
    if source == "eia_weekly":
        return eia_week_release(period)
    release_date, lag = monthly_schedule(source)
    release_month = add_months(period, lag)
    return release_date(release_month.year, release_month.month)


def eia_week_release(week_end: dt.date) -> dt.date:
    """When EIA releases a week: the Wednesday after it ends, Thursday in holiday weeks."""
    return week_end + dt.timedelta(days=6 if week_end in config.EIA_WEEKLY_HOLIDAY_WEEKS else 5)


def latest_due_eia_week(today: dt.date) -> dt.date:
    """End of the latest week released before today: the week today's run should have."""
    friday = today - dt.timedelta(days=(today.weekday() - 4) % 7)  # on or before today
    while eia_week_release(friday) >= today:
        friday -= dt.timedelta(weeks=1)
    return friday


def eia_monthly_release(year: int, month: int) -> dt.date:
    """PSM release date in a month: its last weekday."""
    day = add_months(dt.date(year, month, 1), 1) - dt.timedelta(days=1)
    while day.weekday() >= 5:  # Saturday or Sunday
        day -= dt.timedelta(days=1)
    return day


def jodi_release(year: int, month: int) -> dt.date:
    """JODI's first update in a month, from its calendar, else the default day."""
    listed = [d for d in config.JODI_RELEASE_DATES if (d.year, d.month) == (year, month)]
    return listed[0] if listed else dt.date(year, month, config.JODI_DEFAULT_RELEASE_DAY)


def latest_release_before(today: dt.date, release_date) -> dt.date:
    """The most recent monthly release strictly before today: this month's if it's
    happened, else last month's. release_date(year, month) gives a month's release."""
    this_month = release_date(today.year, today.month)
    if this_month < today:
        return this_month
    last = add_months(today.replace(day=1), -1)
    return release_date(last.year, last.month)


def add_months(month: dt.date, n: int) -> dt.date:
    """The first of the month n months after (or before, if negative) a month."""
    index = month.year * 12 + month.month - 1 + n
    return dt.date(index // 12, index % 12 + 1, 1)


def missing_months(clean: pl.DataFrame) -> pl.DataFrame:
    """Months absent between each series' first and last month, as (source, flow, month)."""
    expected = (
        clean.drop_nulls(REQUIRED)
        .group_by(SERIES)
        .agg(month=pl.date_ranges(pl.col("month").min(), pl.col("month").max(), "1mo"))
        .explode("month")
    )
    return expected.join(clean, on=[*SERIES, "month"], how="anti").sort(*SERIES, "month")


def examples(rows: pl.DataFrame) -> str:
    """The first few rows as short text, e.g. '2026-08-01 jodi imports, ... and 3 more'."""
    shown = [" ".join(str(v) for v in row) for row in rows.head(MAX_EXAMPLES).iter_rows()]
    extra = rows.height - len(shown)
    return ", ".join(shown) + (f" and {extra} more" if extra > 0 else "")
