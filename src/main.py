from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from src.orchestrator import run_pipeline


DEFAULT_APPLICATIONS_PATH = Path("data/output/applications.json")
DEFAULT_DRAFTS_PATH = Path("data/output/email_drafts.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the JobAutomation pipeline.",
    )
    parser.add_argument(
        "--companies",
        required=True,
        type=Path,
        help="Path to the companies CSV input file.",
    )
    parser.add_argument(
        "--applications-path",
        type=Path,
        default=DEFAULT_APPLICATIONS_PATH,
        help="Path for persisted applications.",
    )
    parser.add_argument(
        "--drafts-path",
        type=Path,
        default=DEFAULT_DRAFTS_PATH,
        help="Path for persisted email drafts.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        summary = run_pipeline(
            args.companies,
            applications_path=args.applications_path,
            drafts_path=args.drafts_path,
        )
    except Exception as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1

    print(
        "Pipeline complete: "
        f"companies={summary['companies_processed']}, "
        f"jobs={summary['jobs_discovered']}, "
        f"applications={summary['applications_created']}, "
        f"drafts={summary['drafts_created']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
