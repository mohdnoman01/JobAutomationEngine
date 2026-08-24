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