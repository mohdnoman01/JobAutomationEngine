from src.research.models import Company
from src.research.parsers.generic import GenericParser


def test_generic_parser_extracts_job_links():
    html = """
    <html>
        <body>
            <a href="/jobs/android-developer">
                Android Developer
            </a>

            <a href="/careers/backend-developer">
                Backend Developer
            </a>

            <a href="/about">
                About Us
            </a>

            <a href="/contact">
                Contact
            </a>
        </body>
    </html>
    """

    company = Company(
        name="Test Startup",
        careers_url="https://example.com/careers",
    )

    jobs = GenericParser().parse_jobs(
        html,
        company,
        company.careers_url,
    )

    assert len(jobs) == 2

    assert jobs[0].title == "Android Developer"
    assert jobs[0].url == "https://example.com/jobs/android-developer"
    assert jobs[0].source == "generic"

    assert jobs[1].title == "Backend Developer"
    assert jobs[1].url == "https://example.com/careers/backend-developer"