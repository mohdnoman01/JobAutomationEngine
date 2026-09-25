from src.research.job_discovery import discover_jobs
from src.research.models import Company, Job


def test_discover_jobs_routes_greenhouse(monkeypatch) -> None:
    company = Company(name="Example Inc", careers_url="https://boards.greenhouse.io/example")

    html = """
    <html>
      <body>
        <div class="opening">
          <a href="/jobs/123?gh_jid=123">Senior Backend Engineer</a>
          <span class="location">Remote, US</span>
        </div>
      </body>
    </html>
    """

    monkeypatch.setattr("src.research.job_discovery.fetch_page", lambda url: html)

    jobs = discover_jobs(company)

    assert len(jobs) == 1
    assert jobs[0].title == "Senior Backend Engineer"
    assert jobs[0].source == "greenhouse"


def test_discover_jobs_deduplicates_after_normalization(monkeypatch) -> None:
    company = Company(
        name="Example Inc",
        careers_url="https://example.com/careers",
    )

    monkeypatch.setattr(
        "src.research.job_discovery.fetch_page",
        lambda url: "<html></html>",
    )
    monkeypatch.setattr(
        "src.research.job_discovery.GenericParser.parse_jobs",
        lambda self, html, company, base_url: [
            Job(
                title="Backend Engineer",
                company=company.name,
                url="https://example.com/jobs/123#apply",
                source="generic",
            ),
            Job(
                title="Backend Engineer",
                company=company.name,
                url="https://example.com/jobs/123",
                source="generic",
            ),
            Job(
                title="Backend Engineer",
                company=company.name,
                url="https://example.com/jobs/456",
                source="generic",
            ),
        ],
    )

    jobs = discover_jobs(company)

    assert len(jobs) == 2
    assert [job.url for job in jobs] == [
        "https://example.com/jobs/123",
        "https://example.com/jobs/456",
    ]