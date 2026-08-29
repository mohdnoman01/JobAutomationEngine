from src.research.contact_discovery import (
    deduplicate_contacts,
    discover_contacts,
    discover_relevant_pages,
)
from src.research.scraper import fetch_page, extract_text
from src.research.models import Company, ResearchResult


def research_company(company: Company) -> ResearchResult:
    if not company.website:
        raise ValueError("Company website is required")

    html = fetch_page(company.website)
    text = extract_text(html)
    contacts = discover_contacts(
        html,
        company,
        source=company.website,
        discovery_type="homepage",
    )

    for page_url in discover_relevant_pages(html, company.website):
        try:
            page_html = fetch_page(page_url)
        except Exception as exc:
            print(f"[research] {company.name}: skipped {page_url} - {exc}")
            continue

        contacts.extend(
            discover_contacts(
                page_html,
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
