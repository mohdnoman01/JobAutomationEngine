from src.research.models import Company
from src.research.parsers.lever import LeverParser


def test_lever_parser_extracts_jobs():
    html = """
    <html>
        <body>
            <div class="posting">
                <a class="posting-title" href="/jobs/android-developer">
                    <h5>Android Developer</h5>
                </a>

                <div class="posting-categories">
                    <span class="location">Remote - India</span>
                </div>
            </div>

            <div class="posting">
                <a class="posting-title" href="/jobs/backend-developer">
                    <h5>Backend Developer</h5>
                </a>

                <div class="posting-categories">
                    <span class="location">Bangalore, India</span>
                </div>
            </div>
        </body>
    </html>
    """

    company = Company(
        name="Test Startup",
        website="https://example.com",
        careers_url="https://jobs.example.com",
    )

    jobs = LeverParser().parse_jobs(
        html,
        company,
        company.careers_url,
    )

    assert len(jobs) == 2

    assert jobs[0].title == "Android Developer"
    assert jobs[0].company == "Test Startup"
    assert jobs[0].url == "https://jobs.example.com/jobs/android-developer"
    assert jobs[0].location == "Remote - India"
    assert jobs[0].source == "lever"

    assert jobs[1].title == "Backend Developer"