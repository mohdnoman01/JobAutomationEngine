from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from src.research.models import Company, Job


@dataclass(frozen=True)
class AshbyParser:
    def parse_jobs(
        self,
        html: str,
        company: Company,
        base_url: str,
    ) -> list[Job]:
        soup = BeautifulSoup(html, "html.parser")
        jobs: list[Job] = []
        seen_urls: set[str] = set()

        for node in self._candidate_nodes(soup):
            title = self._extract_title(node)
            url = self._extract_url(node, base_url)

            if not title or not url or url in seen_urls:
                continue

            seen_urls.add(url)

            jobs.append(
                Job(
                    title=title,
                    company=company.name,
                    url=url,
                    location=self._extract_location(node),
                    description=None,
                    source="ashby",
                    employment_type=None,
                )
            )

        return jobs

    def _candidate_nodes(self, soup: BeautifulSoup) -> list[Tag]:
        selectors = [
            "a[href*='/job/']",
            "a[href*='/jobs/']",
            "[data-job-id]",
        ]

        nodes: list[Tag] = []
        seen: set[int] = set()

        for selector in selectors:
            for node in soup.select(selector):
                marker = id(node)

                if marker in seen:
                    continue

                seen.add(marker)
                nodes.append(node)

        return nodes

    def _extract_title(self, node: Tag) -> str | None:
        selectors = [
            "[data-job-title]",
            "[data-testid='job-title']",
            "h3",
            "h2",
            "h1",
        ]

        for selector in selectors:
            element = node.select_one(selector)

            if element:
                text = element.get_text(" ", strip=True)

                if text:
                    return text

        if node.name == "a":
            text = node.get_text(" ", strip=True)
            return text or None

        return None

    def _extract_url(self, node: Tag, base_url: str) -> str | None:
        link = node if node.name == "a" else node.find("a", href=True)

        if not link:
            return None

        href = link.get("href")

        if not href:
            return None

        return urljoin(base_url, href)

    def _extract_location(self, node: Tag) -> str | None:
        selectors = [
            "[data-job-location]",
            "[data-location]",
            "[data-testid='job-location']",
        ]

        for selector in selectors:
            element = node.select_one(selector)

            if element:
                text = element.get_text(" ", strip=True)

                if text:
                    return text

        return None