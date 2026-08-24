from src.research.contact_discovery import discover_contacts
from src.research.scraper import fetch_page, extract_text
from src.research.models import Company, ResearchResult


def research_company(company: Company) -> ResearchResult:
    if not company.website:
        raise ValueError("Company website is required")

    html = fetch_page(company.website)
    text = extract_text(html)
    contacts = discover_contacts(text, company)

    return ResearchResult(
        company_name=company.name,
        url=company.website,
        text=text,
        contacts=contacts,
    )