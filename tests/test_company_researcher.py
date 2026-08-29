import time

from src.research import company_researcher
from src.research.job_discovery import discover_jobs
from src.research.models import Company
from src.research.scraper import PageFetcher


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
    assert result.contacts[0].source_url == "https://example.com"
    assert result.contacts[0].discovery_type == "homepage"


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
    assert all(contact.discovery_type == "same_domain_page" for contact in result.contacts)


def test_research_company_skips_failed_candidate_page(capsys):
    company = Company(name="Test Startup", website="https://example.com")

    def request(url):
        if url == "https://example.com/":
            return '<a href="/contact">Contact</a>'
        raise RuntimeError("not found")

    result = company_researcher.research_company(company, PageFetcher(request))

    assert result.contacts == []
    assert "skipped https://example.com/contact - not found" in capsys.readouterr().out


def test_candidate_results_are_processed_in_discovery_order():
    company = Company(name="Test Startup", website="https://example.com")

    def request(url):
        if url == "https://example.com/":
            return (
                '<a href="/careers">Careers</a>'
                '<a href="/contact">Contact</a>'
            )
        if url == "https://example.com/careers":
            time.sleep(0.02)
            return "careers@example.com"
        return "contact@example.com"

    result = company_researcher.research_company(company, PageFetcher(request))

    assert [contact.email for contact in result.contacts] == [
        "careers@example.com",
        "contact@example.com",
    ]


def test_research_and_job_discovery_share_cached_careers_response():
    company = Company(
        name="Test Startup",
        website="https://example.com",
        careers_url="https://example.com/careers",
    )
    requested_urls = []

    def request(url):
        requested_urls.append(url)
        if url == "https://example.com/":
            return '<a href="/careers">Careers</a>'
        return '<a href="/jobs/123">Android Engineer</a>'

    page_fetcher = PageFetcher(request)

    company_researcher.research_company(company, page_fetcher)
    jobs = discover_jobs(company, page_fetcher)

    assert [job.url for job in jobs] == ["https://example.com/jobs/123"]
    assert requested_urls.count("https://example.com/careers") == 1
