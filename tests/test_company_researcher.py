from src.research import company_researcher
from src.research.models import Company


def test_research_company_discovers_contacts(monkeypatch):
    company = Company(
        name="Test Startup",
        website="https://example.com",
    )

    html = """
    <html>
        <body>
            Contact our hiring team at hiring@example.com.
        </body>
    </html>
    """

    monkeypatch.setattr(
        company_researcher,
        "fetch_page",
        lambda url: html,
    )

    result = company_researcher.research_company(company)

    assert result.company_name == "Test Startup"
    assert result.url == "https://example.com"
    assert result.text
    assert len(result.contacts) == 1
    assert result.contacts[0].email == "hiring@example.com"
    assert result.contacts[0].company == "Test Startup"


def test_research_company_checks_relevant_same_domain_pages(monkeypatch):
    company = Company(name="Test Startup", website="https://example.com")
    homepage_html = """
    <a href="/careers/openings">Careers</a>
    <a href="/blog">Blog</a>
    <a href="https://external.example/contact">Contact</a>
    General: hello@example.com
    """
    careers_html = "Recruiting: hello@example.com talent@example.com"
    fetched_urls = []

    def fake_fetch_page(url):
        fetched_urls.append(url)
        if url == "https://example.com":
            return homepage_html
        if url == "https://example.com/careers/openings":
            return careers_html
        return ""

    monkeypatch.setattr(company_researcher, "fetch_page", fake_fetch_page)

    result = company_researcher.research_company(company)

    assert fetched_urls[0] == "https://example.com"
    assert "https://example.com/careers/openings" in fetched_urls
    assert "https://example.com/blog" not in fetched_urls
    assert "https://external.example/contact" not in fetched_urls
    assert [(contact.email, contact.source) for contact in result.contacts] == [
        ("hello@example.com", "https://example.com/careers/openings"),
        ("talent@example.com", "https://example.com/careers/openings"),
    ]
