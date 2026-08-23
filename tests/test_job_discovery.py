from src.research.job_discovery import discover_jobs
from src.research.models import Company


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