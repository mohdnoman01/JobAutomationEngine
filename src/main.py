from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from src.orchestrator import run_pipeline
from src.research.scraper import HttpPerformanceMetrics

DEFAULT_APPLICATIONS_PATH = Path("data/output/applications.json")
DEFAULT_CONTACTS_PATH = Path("data/output/contacts.json")
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
    parser.add_argument(
        "--contacts-path",
        type=Path,
        default=DEFAULT_CONTACTS_PATH,
        help="Path for persisted research contacts.",
    )
    parser.add_argument(
        "--http-performance",
        action="store_true",
        help="Print a concise HTTP request performance summary.",
    )
    return parser


def format_http_performance(metrics: HttpPerformanceMetrics) -> str:
    summary = metrics.summary()
    message = (
        "HTTP performance: "
        f"requests={summary.requests}, "
        f"cache_hits={summary.cache_hits}, "
        f"failures={summary.failures}, "
        f"network_time={summary.network_time_seconds:.2f}s"
    )

    if summary.slowest_request is not None:
        message += (
            f", slowest={summary.slowest_request.url} "
            f"({summary.slowest_request.elapsed_seconds:.2f}s)"
        )

    return message


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    http_metrics = HttpPerformanceMetrics() if args.http_performance else None

    try:
        pipeline_arguments = {
            "applications_path": args.applications_path,
            "contacts_path": args.contacts_path,
            "drafts_path": args.drafts_path,
        }
        if http_metrics is not None:
            pipeline_arguments["http_metrics"] = http_metrics

        summary = run_pipeline(
            args.companies,
            **pipeline_arguments,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1

    print(
        "Pipeline complete: "
        f"companies={summary['companies_processed']}, "
        f"jobs={summary['jobs_discovered']}, "
        f"applications={summary['applications_created']}, "
        f"drafts={summary['drafts_created']}"
    )
    if http_metrics is not None:
        print(format_http_performance(http_metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
