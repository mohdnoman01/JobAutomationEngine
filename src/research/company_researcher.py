from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable

from src.research.contact_discovery import (
    deduplicate_contacts,
    discover_contacts,
    discover_relevant_pages,
)
from src.research.scraper import PageFetcher, extract_text, fetch_page
from src.research.models import Company, ResearchResult


MAX_CONTACT_PAGE_WORKERS = 2


def _fetch_contact_pages(
    page_urls: list[str],
    fetch: Callable[[str], str],
) -> list[tuple[str, str | Exception]]:
    if not page_urls:
        return []

    with ThreadPoolExecutor(max_workers=MAX_CONTACT_PAGE_WORKERS) as executor:
        futures = [executor.submit(fetch, page_url) for page_url in page_urls]
        results: list[tuple[str, str | Exception]] = []

        for page_url, future in zip(page_urls, futures):
            try:
                results.append((page_url, future.result()))
            except Exception as exc:
                results.append((page_url, exc))

        return results


def research_company(
    company: Company,
    page_fetcher: PageFetcher | None = None,
) -> ResearchResult:
    if not company.website:
        raise ValueError("Company website is required")

    fetch = page_fetcher.fetch if page_fetcher else fetch_page
    html = fetch(company.website)
    text = extract_text(html)
    contacts = discover_contacts(
        html,
        company,
        source=company.website,
        discovery_type="homepage",
    )

    page_urls = discover_relevant_pages(html, company.website)

    for page_url, page_result in _fetch_contact_pages(page_urls, fetch):
        if isinstance(page_result, Exception):
            print(f"[research] {company.name}: skipped {page_url} - {page_result}")
            continue

        contacts.extend(
            discover_contacts(
                page_result,
                company,
                source=page_url,
                discovery_type="same_domain_page",
            )
        )

    return ResearchResult(
        company_name=company.name,
        url=company.website,
        text=text,
        contacts=deduplicate_contacts(contacts),
    )
