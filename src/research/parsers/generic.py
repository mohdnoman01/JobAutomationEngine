from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from src.research.models import Company, Job


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

    def _looks_like_job_link(self, title: str, href: str) -> bool:
        text = f"{title} {href}".lower()

        job_keywords = (
            "job",
            "jobs",
            "career",
            "careers",
            "position",
            "opening",
            "vacancy",
            "role",
        )

        return any(keyword in text for keyword in job_keywords)