from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from src.research.models import Company, Job


@dataclass(frozen=True)
class GreenhouseParser:
    def parse_jobs(self, html: str, company: Company, base_url: str) -> list[Job]:
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
                    description=self._extract_description(node),
                    source="greenhouse",
                    employment_type=self._extract_employment_type(node),
                )
            )

        return jobs

    def _candidate_nodes(self, soup: BeautifulSoup) -> Iterable[Tag]:
        selectors = [
            "div.opening",
            "li.opening",
            "tr.opening",
            "div.job",
            "li.job",
            "tr.job",
            "a[href*='gh_jid=']",
            "a[href*='/jobs/']",
        ]

        seen: set[int] = set()
        for selector in selectors:
            for node in soup.select(selector):
                marker = id(node)
                if marker in seen:
                    continue
                seen.add(marker)
                yield node

    def _extract_title(self, node: Tag) -> str | None:
        title_selectors = [
            "[data-qa='job-title']",
            "[data-gh='job-title']",
            ".job_title",
            ".opening a",
            "a",
        ]

        for selector in title_selectors:
            element = node.select_one(selector) if hasattr(node, "select_one") else None
            if element:
                text = element.get_text(" ", strip=True)
                if text:
                    return text

        text = node.get_text(" ", strip=True)
        return text or None

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
            "[data-qa='job-location']",
            "[data-gh='location']",
            ".location",
            ".opening-location",
        ]

        for selector in selectors:
            element = node.select_one(selector)
            if element:
                text = element.get_text(" ", strip=True)
                if text:
                    return text

        return None

    def _extract_description(self, node: Tag) -> str | None:
        element = node.select_one("[data-description]")
        if element:
            description = element.get("data-description")
            if description:
                return str(description).strip() or None

        description = node.get("data-description")
        if description:
            return str(description).strip() or None
        return None

    def _extract_employment_type(self, node: Tag) -> str | None:
        element = node.select_one("[data-employment-type]")
        if element:
            employment_type = element.get("data-employment-type")
            if employment_type:
                return str(employment_type).strip() or None

        employment_type = node.get("data-employment-type")
        if employment_type:
            return str(employment_type).strip() or None
        return None