from etl import config


def test_jodi_url_uses_special_name_for_current_year():
    assert config.jodi_url(2026, current_year=2026).endswith("/primaryyear2026.csv")
    assert config.jodi_url(2025, current_year=2026).endswith("/2025.csv")


def test_every_eia_series_has_a_known_flow_and_source():
    for flow, source, freq in config.EIA_SERIES.values():
        assert flow in config.JODI_FLOWS.values()
        assert source in {"eia_monthly", "eia_weekly"}
        assert freq in {"m", "w"}


def test_pipeline_modules_import():
    from etl import run  # noqa: F401
