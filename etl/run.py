"""Pipeline entry point: python -m etl.run (locally, in Docker, and on ECS)."""

from etl import extract, load, quality, transform


def main() -> None:
    extract.run()
    transform.run()
    quality.run()
    load.run()


if __name__ == "__main__":
    main()
