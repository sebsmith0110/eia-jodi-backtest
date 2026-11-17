import datetime as dt
import statistics

import polars as pl
import pytest

from etl.metrics import (
    ANOMALY_MIN_HISTORY,
    ANOMALY_MIN_KBD,
    ANOMALY_MIN_PCT,
    ANOMALY_WINDOW,
    ANOMALY_Z,
    COMPARISON_SCHEMA,
    compare,
    flag_anomalies,
    summarise,
)
from etl.transform import OUTPUT_SCHEMA

AUG = dt.date(2026, 8, 1)
SEP = dt.date(2026, 9, 1)


def clean(*rows: tuple) -> pl.DataFrame:
    """Hand-made clean-table rows: (month, flow, source, value_kbd)."""
    return pl.DataFrame([(*row, None, None) for row in rows], schema=OUTPUT_SCHEMA, orient="row")


def comparison(*rows: tuple) -> pl.DataFrame:
    """Hand-made comparison rows: (month, flow, eia_source, diff_kbd, diff_pct)."""
    return pl.DataFrame(
        [(m, f, s, 100.0 + d, 100.0, d, p) for m, f, s, d, p in rows],
        schema=COMPARISON_SCHEMA,
        orient="row",
    )


def monthly(
    diff_pcts: list[float | None],
    flow="imports",
    source="eia_monthly",
    jodi_kbd=10_000.0,
) -> pl.DataFrame:
    """One series of consecutive months from Jan 2020 with the given diff_pct values.

    JODI is fixed at jodi_kbd, so each month's diff_kbd is diff_pct of it.
    """
    rows = []
    for i, pct in enumerate(diff_pcts):
        month = dt.date(2020 + i // 12, i % 12 + 1, 1)
        diff_kbd = 0.0 if pct is None else pct / 100 * jodi_kbd
        rows.append((month, flow, source, jodi_kbd + diff_kbd, jodi_kbd, diff_kbd, pct))
    return pl.DataFrame(rows, schema=COMPARISON_SCHEMA, orient="row")


# --- compare -------------------------------------------------------------------


def test_compare_difference_is_eia_minus_jodi():
    result = compare(
        clean(
            (AUG, "imports", "jodi", 100.0),
            (AUG, "imports", "eia_monthly", 110.0),
        )
    )

    assert result.select("eia_kbd", "jodi_kbd", "diff_kbd", "diff_pct").rows() == [
        (110.0, 100.0, 10.0, 10.0)
    ]


def test_compare_eia_below_jodi_gives_a_negative_difference():
    result = compare(
        clean(
            (AUG, "imports", "jodi", 200.0),
            (AUG, "imports", "eia_weekly", 190.0),
        )
    )

    assert result.select("diff_kbd", "diff_pct").rows() == [(-10.0, -5.0)]


def test_compare_pairs_each_eia_source_with_jodi_for_the_same_flow():
    result = compare(
        clean(
            (AUG, "imports", "jodi", 6709.0),
            (AUG, "exports", "jodi", 3819.5),
            (AUG, "imports", "eia_weekly", 6725.5),
            (AUG, "imports", "eia_monthly", 6594.0),
            (AUG, "exports", "eia_monthly", 3992.0),
        )
    )

    assert result.select("flow", "eia_source", "jodi_kbd").rows() == [
        ("exports", "eia_monthly", 3819.5),
        ("imports", "eia_monthly", 6709.0),
        ("imports", "eia_weekly", 6709.0),
    ]


def test_compare_skips_a_month_jodi_has_not_published():
    result = compare(
        clean(
            (AUG, "imports", "jodi", 6709.0),
            (AUG, "imports", "eia_weekly", 6725.5),
            (SEP, "imports", "eia_weekly", 6397.6),
        )
    )

    assert result["month"].to_list() == [AUG]


def test_compare_skips_months_with_a_null_on_either_side():
    result = compare(
        clean(
            (AUG, "imports", "jodi", None),
            (AUG, "imports", "eia_monthly", 6594.0),
            (SEP, "imports", "jodi", 6500.0),
            (SEP, "imports", "eia_monthly", None),
        )
    )

    assert result.is_empty()


def test_compare_percentage_is_null_when_jodi_is_zero():
    result = compare(
        clean(
            (AUG, "exports", "jodi", 0.0),
            (AUG, "exports", "eia_monthly", 5.0),
        )
    )

    assert result.select("diff_kbd", "diff_pct").rows() == [(5.0, None)]


def test_compare_output_matches_the_comparison_schema():
    result = compare(
        clean(
            (AUG, "imports", "jodi", 6709.0),
            (AUG, "imports", "eia_monthly", 6594.0),
        )
    )

    assert result.schema == pl.Schema(COMPARISON_SCHEMA)


def test_compare_sorts_by_month_flow_and_source_whatever_the_input_order():
    result = compare(
        clean(
            (SEP, "imports", "eia_weekly", 1.0),
            (AUG, "imports", "jodi", 1.0),
            (SEP, "exports", "jodi", 1.0),
            (SEP, "imports", "jodi", 1.0),
            (AUG, "imports", "eia_monthly", 1.0),
            (SEP, "exports", "eia_monthly", 1.0),
            (SEP, "imports", "eia_monthly", 1.0),
        )
    )

    assert result.select("month", "flow", "eia_source").rows() == [
        (AUG, "imports", "eia_monthly"),
        (SEP, "exports", "eia_monthly"),
        (SEP, "imports", "eia_monthly"),
        (SEP, "imports", "eia_weekly"),
    ]


def test_compare_does_not_pair_jodi_with_a_different_flow():
    result = compare(
        clean(
            (AUG, "exports", "jodi", 3800.0),
            (AUG, "imports", "eia_monthly", 6594.0),
        )
    )

    assert result.is_empty()


def test_compare_gives_an_empty_table_with_the_schema_when_there_is_no_jodi():
    result = compare(clean((AUG, "imports", "eia_monthly", 6594.0)))

    assert result.is_empty()
    assert result.schema == pl.Schema(COMPARISON_SCHEMA)


# --- summarise -----------------------------------------------------------------


def test_summarise_scores_each_group():
    result = summarise(
        comparison(
            (AUG, "imports", "eia_monthly", 10.0, 5.0),
            (SEP, "imports", "eia_monthly", -30.0, -15.0),
            (AUG, "exports", "eia_monthly", 4.0, 2.0),
        ),
        by=["flow"],
    )

    assert result.rows(named=True) == [
        {
            "flow": "exports",
            "n_months": 1,
            "bias_kbd": 4.0,
            "bias_pct": 2.0,
            "share_above": 1.0,
            "mae_kbd": 4.0,
            "mape": 2.0,
            "max_abs_pct": 2.0,
        },
        {
            "flow": "imports",
            "n_months": 2,
            "bias_kbd": -10.0,
            "bias_pct": -5.0,
            "share_above": 0.5,
            "mae_kbd": 20.0,
            "mape": 10.0,
            "max_abs_pct": 15.0,
        },
    ]


def test_summarise_can_group_by_year():
    result = summarise(
        comparison(
            (dt.date(2024, 3, 1), "imports", "eia_weekly", 1.0, 1.0),
            (dt.date(2025, 3, 1), "imports", "eia_weekly", 2.0, 2.0),
            (dt.date(2025, 4, 1), "imports", "eia_weekly", 4.0, 4.0),
        ),
        by=["flow", "year"],
    )

    assert result.select("year", "n_months", "bias_pct").rows() == [(2024, 1, 1.0), (2025, 2, 3.0)]


def test_summarise_ignores_null_percentages():
    result = summarise(
        comparison(
            (AUG, "exports", "eia_monthly", 5.0, None),
            (SEP, "exports", "eia_monthly", 2.0, 4.0),
        ),
        by=["flow"],
    )

    assert result.select("n_months", "bias_kbd", "bias_pct", "mape").rows() == [(2, 3.5, 4.0, 4.0)]


def test_summarise_returns_the_group_columns_then_the_measures():
    result = summarise(
        comparison((AUG, "imports", "eia_monthly", 1.0, 1.0)), by=["flow", "eia_source"]
    )

    assert result.columns == [
        "flow",
        "eia_source",
        "n_months",
        "bias_kbd",
        "bias_pct",
        "share_above",
        "mae_kbd",
        "mape",
        "max_abs_pct",
    ]


def test_summarise_sorts_by_every_group_column():
    result = summarise(
        comparison(
            (AUG, "imports", "eia_weekly", 1.0, 1.0),
            (AUG, "imports", "eia_monthly", 1.0, 1.0),
            (AUG, "exports", "eia_weekly", 1.0, 1.0),
        ),
        by=["flow", "eia_source"],
    )

    assert result.select("flow", "eia_source").rows() == [
        ("exports", "eia_weekly"),
        ("imports", "eia_monthly"),
        ("imports", "eia_weekly"),
    ]


def test_summarise_does_not_count_an_exact_match_as_above():
    result = summarise(
        comparison(
            (AUG, "imports", "eia_monthly", 0.0, 0.0),
            (SEP, "imports", "eia_monthly", 5.0, 5.0),
        ),
        by=["flow"],
    )

    assert result["share_above"].to_list() == [0.5]


def test_summarise_offsetting_misses_give_zero_bias_but_keep_their_size():
    result = summarise(
        comparison(
            (AUG, "imports", "eia_monthly", 50.0, 5.0),
            (SEP, "imports", "eia_monthly", -50.0, -5.0),
        ),
        by=["flow"],
    )

    assert result.select("bias_kbd", "bias_pct", "mae_kbd", "mape").rows() == [
        (0.0, 0.0, 50.0, 5.0)
    ]


def test_summarise_percentages_are_null_when_none_can_be_computed():
    result = summarise(comparison((AUG, "exports", "eia_monthly", 5.0, None)), by=["flow"])

    assert result.select("n_months", "bias_pct", "mape", "max_abs_pct").rows() == [
        (1, None, None, None)
    ]


def test_summarise_only_adds_year_when_asked():
    result = summarise(comparison((AUG, "imports", "eia_monthly", 1.0, 1.0)), by=["flow"])

    assert "year" not in result.columns


def test_summarise_gives_an_empty_table_for_an_empty_comparison():
    result = summarise(comparison(), by=["flow", "eia_source"])

    assert result.is_empty()
    assert result.columns[:2] == ["flow", "eia_source"]


# --- flag_anomalies ------------------------------------------------------------


def test_flag_anomalies_flags_a_spike_against_recent_history():
    history = [1.0, -1.0] * (ANOMALY_MIN_HISTORY // 2)
    result = flag_anomalies(monthly([*history, 20.0, 1.0]))

    assert result["is_anomaly"].to_list() == [False] * len(history) + [True, False]


def test_flag_anomalies_leaves_early_months_unscored():
    result = flag_anomalies(monthly([1.0, -1.0] * (ANOMALY_MIN_HISTORY // 2) + [50.0]))

    assert result["z_score"].null_count() == ANOMALY_MIN_HISTORY
    assert result["z_score"][-1] is not None


def test_flag_anomalies_only_looks_back_over_the_window():
    # The wild months are older than the window, so they no longer mask the spike.
    wild = [100.0, -100.0] * (ANOMALY_MIN_HISTORY // 2)
    calm = [1.0, -1.0] * (ANOMALY_WINDOW // 2)
    result = flag_anomalies(monthly([*wild, *calm, 20.0]))

    assert result["is_anomaly"][-1]


def test_flag_anomalies_scores_each_series_separately():
    calm = [1.0, -1.0] * (ANOMALY_MIN_HISTORY // 2)
    noisy = [30.0, -30.0] * (ANOMALY_MIN_HISTORY // 2)
    result = flag_anomalies(
        pl.concat(
            [
                monthly([*calm, 20.0], source="eia_monthly"),
                monthly([*noisy, 20.0], source="eia_weekly"),
            ]
        )
    )

    last = result.filter(pl.col("month") == result["month"].max())
    assert last.select("eia_source", "is_anomaly").rows() == [
        ("eia_monthly", True),
        ("eia_weekly", False),
    ]


def test_flag_anomalies_does_not_score_a_flat_history():
    result = flag_anomalies(monthly([2.0] * ANOMALY_MIN_HISTORY + [9.0]))

    assert result["z_score"][-1] is None
    assert not result["is_anomaly"][-1]


def test_flag_anomalies_z_score_uses_the_mean_and_sample_std_of_earlier_months_only():
    history = [0.5, -1.2, 2.0, 0.3, -0.7, 1.1, -0.4, 0.9, -1.5, 0.2, 1.4, -0.6]
    result = flag_anomalies(monthly([*history, 4.0]))

    expected = (4.0 - statistics.mean(history)) / statistics.stdev(history)
    assert result["z_score"][-1] == pytest.approx(expected)


def test_flag_anomalies_window_holds_exactly_the_previous_window_months():
    # Month 0 is far older than the window; months 1..ANOMALY_WINDOW are the baseline.
    baseline = [float(i % 5) for i in range(ANOMALY_WINDOW)]
    result = flag_anomalies(monthly([1000.0, *baseline, 3.0]))

    expected = (3.0 - statistics.mean(baseline)) / statistics.stdev(baseline)
    assert result["z_score"][-1] == pytest.approx(expected)


def test_flag_anomalies_flags_misses_in_either_direction():
    history = [1.0, -1.0] * (ANOMALY_MIN_HISTORY // 2)
    result = flag_anomalies(monthly([*history, -20.0]))

    assert result["z_score"][-1] < -ANOMALY_Z
    assert result["is_anomaly"][-1]


def test_flag_anomalies_does_not_flag_a_z_score_exactly_on_the_threshold():
    history = [1.0, -1.0] * (ANOMALY_MIN_HISTORY // 2)
    on_threshold = ANOMALY_Z * statistics.stdev(history)
    result = flag_anomalies(monthly([*history, on_threshold]))

    assert result["z_score"][-1] == pytest.approx(ANOMALY_Z)
    assert not result["is_anomaly"][-1]


def test_flag_anomalies_window_is_in_calendar_months_not_rows():
    # A year of history, then a gap: by the next month most of it is older than the window.
    history = comparison(
        *[
            (dt.date(2020, m, 1), "imports", "eia_monthly", p, p)
            for m, p in zip(range(1, 13), [1.0, -1.0] * 6, strict=True)
        ]
    )
    after_gap = comparison((dt.date(2022, 6, 1), "imports", "eia_monthly", 50.0, 50.0))
    result = flag_anomalies(pl.concat([history, after_gap]))

    assert result["z_score"][-1] is None
    assert not result["is_anomaly"][-1]


def test_flag_anomalies_null_months_do_not_count_as_history():
    history = [1.0, -1.0] * (ANOMALY_MIN_HISTORY // 2)
    history[0] = None
    result = flag_anomalies(monthly([*history, 50.0]))

    assert result["z_score"][-1] is None


def test_flag_anomalies_never_flags_a_month_without_a_percentage():
    history = [1.0, -1.0] * (ANOMALY_MIN_HISTORY // 2)
    result = flag_anomalies(monthly([*history, None]))

    assert result["z_score"][-1] is None
    assert not result["is_anomaly"][-1]


def test_flag_anomalies_gives_the_same_result_whatever_the_input_order():
    data = monthly([1.0, -1.0] * (ANOMALY_MIN_HISTORY // 2) + [20.0, 0.5, -3.0])

    assert flag_anomalies(data.sample(fraction=1.0, shuffle=True, seed=1)).equals(
        flag_anomalies(data)
    )


def test_flag_anomalies_keeps_every_row_and_column_and_adds_two():
    data = monthly([1.0, -1.0] * (ANOMALY_MIN_HISTORY // 2) + [20.0])
    result = flag_anomalies(data)

    assert result.drop("z_score", "is_anomaly").equals(data)
    assert result.schema["z_score"] == pl.Float64
    assert result.schema["is_anomaly"] == pl.Boolean
    assert result["is_anomaly"].null_count() == 0


def test_flag_anomalies_handles_an_empty_comparison():
    result = flag_anomalies(comparison())

    assert result.is_empty()
    assert result.columns == [*COMPARISON_SCHEMA, "z_score", "is_anomaly"]


def test_flag_anomalies_skips_a_high_z_score_on_a_small_percentage_miss():
    # Near-identical history, as when JODI copied EIA: a tiny miss gets a huge z.
    history = [0.01, -0.01] * (ANOMALY_MIN_HISTORY // 2)
    result = flag_anomalies(monthly([*history, ANOMALY_MIN_PCT / 2]))

    assert result["z_score"][-1] > ANOMALY_Z
    assert result["diff_kbd"][-1] >= ANOMALY_MIN_KBD
    assert not result["is_anomaly"][-1]


def test_flag_anomalies_skips_a_high_z_score_on_a_small_volume_miss():
    # Pre-2015 exports: a few kbd off a tiny base is a big percentage.
    history = [1.0, -1.0] * (ANOMALY_MIN_HISTORY // 2)
    result = flag_anomalies(monthly([*history, 100.0], flow="exports", jodi_kbd=20.0))

    assert result["z_score"][-1] > ANOMALY_Z
    assert result["diff_kbd"][-1] < ANOMALY_MIN_KBD
    assert not result["is_anomaly"][-1]


def test_flag_anomalies_flags_a_miss_exactly_at_both_size_minimums():
    jodi_kbd = ANOMALY_MIN_KBD / ANOMALY_MIN_PCT * 100
    history = [0.01, -0.01] * (ANOMALY_MIN_HISTORY // 2)
    result = flag_anomalies(monthly([*history, -ANOMALY_MIN_PCT], jodi_kbd=jodi_kbd))

    assert result.select("diff_pct", "diff_kbd").row(-1) == (-ANOMALY_MIN_PCT, -ANOMALY_MIN_KBD)
    assert result["is_anomaly"][-1]
