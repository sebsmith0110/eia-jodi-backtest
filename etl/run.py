"""Full pipeline in one command: python -m etl.run

Downloads both sources, builds the clean table, runs the quality checks and
saves the dashboard snapshot. A failed check stops the run before the snapshot
is written, so the dashboard keeps its last good data.
"""

from etl import config, extract, quality, snapshot, transform


def main() -> None:
    """Run every stage in order."""
    extract.run()
    clean = transform.run()
    quality.run(
        clean,
        latest_week=quality.latest_eia_week(transform.get_latest_raw_dir("eia")),
        log_dir=config.LOG_DIR,
    )
    path = snapshot.save(clean, transform.get_latest_raw_dir("jodi").name)
    print(f"Wrote {path}" if path else "Snapshot unchanged, nothing written")


if __name__ == "__main__":
    main()
