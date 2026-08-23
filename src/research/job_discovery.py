from __future__ import annotations

from src.research.ats_detector import detect_ats
from src.research.models import Company, Job
from src.research.parsers import AshbyParser, GreenhouseParser, LeverParser
from src.research.scraper import fetch_page


def discover_jobs(company: Company) -> list[Job]:
    if not company.careers_url:
        print(f"[research] {company.name}: missing careers_url")
        return []

    html = fetch_page(company.careers_url)
    ats = detect_ats(company.careers_url, html)

    print(f"[research] {company.name}: detected ATS = {ats}")

    if ats == "greenhouse":
        return GreenhouseParser().parse_jobs(html, company, company.careers_url)

    if ats == "lever":
        LeverParser()
        print(f"[research] Lever parser not implemented yet for {company.name}")
        return []

    if ats == "ashby":
        AshbyParser()
        print(f"[research] Ashby parser not implemented yet for {company.name}")
        return []

    print(f"[research] No supported ATS parser for {company.name}")
    return []