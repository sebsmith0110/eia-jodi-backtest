"""Data quality checks on the clean table, run before the snapshot is saved.

Each check is a small function that takes the clean table and returns a
CheckResult. run() runs them all, prints a report and raises QualityError if
any critical check failed, so a bad table never reaches the dashboard.

The raw files' format (JODI columns, EIA units and series IDs) is checked while
they are read, in transform.py; these checks judge the table that comes out.
"""

from dataclasses import dataclass

import polars as pl

from etl import config
from etl.transform import OUTPUT_SCHEMA

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


def run(clean: pl.DataFrame) -> list[CheckResult]:
    """Run every check, print a report and raise QualityError on a critical failure."""
    schema = check_schema(clean)
    if not schema.passed:
        # The other checks assume the expected columns, so they'd crash rather than report.
        raise QualityError([schema])
    results = [schema, *(check(clean) for check in CHECKS)]
    print(report(clean, results))
    failed = [r for r in results if r.status == "FAIL"]
    if failed:
        raise QualityError(failed)
    return results


def report(clean: pl.DataFrame, results: list[CheckResult]) -> str:
    """Row counts per series, then one line per check."""
    lines = ["Row counts:"]
    for row in row_counts(clean).iter_rows(named=True):
        lines.append(
            f"  {row['source']:<12} {row['flow']:<8} {row['rows']:>4} rows  "
            f"{row['first_month']} to {row['last_month']}  {row['null_values']} null values"
        )
    lines.append("Checks:")
    lines += [f"  {r.status:<4}  {r.name:<16} {r.detail}" for r in results]
    return "\n".join(lines)


def row_counts(clean: pl.DataFrame) -> pl.DataFrame:
    """Rows, first and last month, and null values for each series."""
    return (
        clean.group_by(SERIES)
        .agg(
            rows=pl.len(),
            first_month=pl.col("month").min(),
            last_month=pl.col("month").max(),
            null_values=pl.col("value_kbd").null_count(),
        )
        .sort(SERIES)
    )


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


# Run after check_schema has passed, in this order.
CHECKS = [
    check_required_fields,
    check_duplicates,
    check_completeness,
    check_missing_values,
    check_value_range,
]


# --- helpers ------------------------------------------------------------------


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
