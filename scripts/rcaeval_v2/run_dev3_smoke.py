"""Run the exact v2-dev.3 72-record Provider Smoke schedule."""

from scripts.rcaeval_v2.run_dev3_design import _main_for_phase


def main(argv: tuple[str, ...] | None = None) -> int:
    return _main_for_phase("smoke", argv)


if __name__ == "__main__":
    raise SystemExit(main())
