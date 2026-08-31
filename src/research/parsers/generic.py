from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from src.research.models import Company, Job

JOB_KEYWORDS = (
    "job",
    "jobs",
    "position",
    "opening",
    "openings",
    "vacancy",
    "vacancies",
    "role",
    "roles",
)

GENERIC_NAVIGATION_TITLES = {
    "careers",
    "career",
    "jobs",
    "job openings",
    "job opportunities",
    "open positions",
    "positions",
    "vacancies",
    "view jobs",
    "view openings",
    "apply now",
    "join us",
}

GENERIC_NAVIGATION_PATHS = {
    "/about",
    "/contact",
    "/careers",
    "/career",
    "/jobs",
    "/job",
    "/team",
    "/company",
    "/people",
}

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def _tokenize(value: str) -> set[str]:
    return set(TOKEN_PATTERN.findall(value.casefold()))


@dataclass(frozen=True)
class GenericParser:
    def parse_jobs(
        self,
        html: str,
        company: Company,
        base_url: str,
    ) -> list[Job]:
        soup = BeautifulSoup(html, "html.parser")

        jobs: list[Job] = []
        seen_urls: set[str] = set()

        for link in soup.find_all("a", href=True):
            title = link.get_text(" ", strip=True)
            href = link.get("href")

            if not title or not href:
                continue

            if not self._looks_like_job_link(title, href):
                continue

            url = urljoin(base_url, href)
            parsed_url = urlsplit(url)

            if parsed_url.fragment:
                url = url.split("#", maxsplit=1)[0]

            if url in seen_urls:
                continue

            seen_urls.add(url)

            jobs.append(
                Job(
                    title=title,
                    company=company.name,
                    url=url,
                    source="generic",
                )
            )

        return jobs

    def _looks_like_job_link(
        self,
        title: str,
        href: str,
    ) -> bool:
        normalized_title = " ".join(title.casefold().split())

        if normalized_title in GENERIC_NAVIGATION_TITLES:
            return False

        parsed_href = urlsplit(href)
        path = parsed_href.path.casefold()

        if path in GENERIC_NAVIGATION_PATHS:
            return False

        title_tokens = _tokenize(title)
        href_tokens = _tokenize(path)

        keyword_in_path = bool(href_tokens.intersection(JOB_KEYWORDS))

        keyword_in_title = bool(title_tokens.intersection(JOB_KEYWORDS))

        # A specific path under a careers/jobs/positions section
        # is strong evidence of an individual job posting.
        path_segments = [segment for segment in path.split("/") if segment]

        has_job_section = any(
            segment
            in {
                "career",
                "careers",
                "job",
                "jobs",
                "position",
                "positions",
                "opening",
                "openings",
                "vacancy",
                "vacancies",
            }
            for segment in path_segments
        )

        if has_job_section and len(title_tokens) >= 2:
            return True

         # Keep support for links whose path contains a job keyword.
        if keyword_in_path and len(title_tokens) >= 2:
            return True

        # Keep support for links whose title indicates a specific
        # opening/position while avoiding one-word navigation labels.
        return bool(keyword_in_title and len(title_tokens) >= 2)