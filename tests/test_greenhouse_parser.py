from src.research.models import Company
from src.research.parsers.greenhouse import GreenhouseParser


GREENHOUSE_HTML = """
<html>
  <body>
    <div class="opening">
      <a href="/jobs/123?gh_jid=123">Senior Backend Engineer</a>
      <span class="location">Remote, US</span>
    </div>
    <div class="opening">
      <a href="https://boards.greenhouse.io/example/jobs/456">Data Scientist</a>
      <span class="location">New York, NY</span>
      <div data-employment-type="Full-time"></div>
    </div>
  </body>
</html>
"""


def test_greenhouse_parser_extracts_jobs() -> None:
    parser = GreenhouseParser()
    company = Company(name="Example Inc", careers_url="https://boards.greenhouse.io/example")

    jobs = parser.parse_jobs(GREENHOUSE_HTML, company, company.careers_url)

    assert len(jobs) == 2
    assert jobs[0].title == "Senior Backend Engineer"
    assert jobs[0].company == "Example Inc"
    assert jobs[0].url == "https://boards.greenhouse.io/jobs/123?gh_jid=123"
    assert jobs[0].location == "Remote, US"
    assert jobs[0].source == "greenhouse"

    assert jobs[1].title == "Data Scientist"
    assert jobs[1].employment_type == "Full-time"