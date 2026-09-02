from __future__ import annotations  # noqa: I001

from src.research.ats_detector import detect_ats
from src.research.models import Company, Job
from src.research.parsers import (
    AshbyParser,
    GenericParser,
    GreenhouseParser,
    LeverParser,
)
from src.research.scraper import PageFetcher, fetch_page

from src.research.job_normalizer import normalize_job


def discover_jobs(
    company: Company,
    page_fetcher: PageFetcher | None = None,
) -> list[Job]:
    if not company.careers_url:
        print(f"[research] {company.name}: missing careers_url")
        return []

    fetch = page_fetcher.fetch if page_fetcher else fetch_page
    html = fetch(company.careers_url)
    ats = detect_ats(company.careers_url, html)

    print(f"[research] {company.name}: detected ATS = {ats}")

    if ats == "greenhouse":
        jobs = GreenhouseParser().parse_jobs(
            html,
            company,
            company.careers_url,
        )
        return [normalize_job(job) for job in jobs]

    if ats == "lever":
        jobs = LeverParser().parse_jobs(
            html,
            company,
            company.careers_url,
        )
        return [normalize_job(job) for job in jobs]

    if ats == "ashby":
        jobs = AshbyParser().parse_jobs(
            html,
            company,
            company.careers_url,
        )
        return [normalize_job(job) for job in jobs]

    print(f"[research] {company.name}: using generic job parser")

    jobs = GenericParser().parse_jobs(
        html,
        company,
        company.careers_url,
    )
    return [normalize_job(job) for job in jobs]
