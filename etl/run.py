"""Full pipeline in one command: python -m etl.run

Downloads both sources, builds the clean table, runs the quality checks and
saves the dashboard snapshot. A failed check stops the run before the snapshot
is written, so the dashboard keeps its last good data.
"""

from etl import config, extract, quality, snapshot, transform


def main() -> None:
    """Run every stage in order."""
    try:
        extract.run()
        clean = transform.run()
        latest_week = quality.latest_eia_week(transform.get_latest_raw_dir("eia"))
    except Exception as error:
        # Logged so the failure shows on the dashboard, then re-raised to fail the run.
        quality.log_crash(config.LOG_DIR, error)
        raise
    quality.run(clean, latest_week=latest_week, log_dir=config.LOG_DIR)
    path = snapshot.save(clean, transform.get_latest_raw_dir("jodi").name)
    print(f"Wrote {path}" if path else "Snapshot unchanged, nothing written")


if __name__ == "__main__":
    main()
