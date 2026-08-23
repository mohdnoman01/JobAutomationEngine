from src.research.models import Company
from src.research.parsers.ashby import AshbyParser


def test_ashby_parser_extracts_jobs():
    html = """
    <html>
        <body>
            <a href="/job/android-developer">
                <h3>Android Developer</h3>
                <span data-job-location>Remote - India</span>
            </a>

            <a href="/job/backend-developer">
                <h3>Backend Developer</h3>
                <span data-job-location>Bangalore, India</span>
            </a>
        </body>
    </html>
    """

    company = Company(
        name="Test Startup",
        website="https://example.com",
        careers_url="https://jobs.example.com",
    )

    jobs = AshbyParser().parse_jobs(
        html,
        company,
        company.careers_url,
    )

    assert len(jobs) == 2

    assert jobs[0].title == "Android Developer"
    assert jobs[0].company == "Test Startup"
    assert jobs[0].url == "https://jobs.example.com/job/android-developer"
    assert jobs[0].location == "Remote - India"
    assert jobs[0].source == "ashby"

    assert jobs[1].title == "Backend Developer"