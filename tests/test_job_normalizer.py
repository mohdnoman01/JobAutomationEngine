from src.research.job_normalizer import normalize_job
from src.research.models import Job


def test_normalize_job_cleans_text_and_url():
    job = Job(
        title="  Android   Developer  ",
        company="Test Startup",
        url="  https://example.com/jobs/123#apply  ",
        location="  Remote   -   India ",
        description="Build   Android apps.\nUse Kotlin.",
        source="generic",
        employment_type="  Full-time  ",
    )

    normalized = normalize_job(job)

    assert normalized.title == "Android Developer"
    assert normalized.company == "Test Startup"
    assert normalized.url == "https://example.com/jobs/123"
    assert normalized.location == "Remote - India"
    assert normalized.description == "Build Android apps. Use Kotlin."
    assert normalized.source == "generic"
    assert normalized.employment_type == "Full-time"


def test_normalize_job_preserves_none_values():
    job = Job(
        title="Android Developer",
        company="Test Startup",
        url="https://example.com/jobs/123",
    )

    normalized = normalize_job(job)

    assert normalized.location is None
    assert normalized.description is None
    assert normalized.source is None
    assert normalized.employment_type is None


def test_normalize_job_does_not_mutate_original():
    job = Job(
        title="  Android   Developer  ",
        company="Test Startup",
        url=" https://example.com/jobs/123#apply ",
    )

    normalized = normalize_job(job)

    assert job.title == "  Android   Developer  "
    assert job.url == " https://example.com/jobs/123#apply "

    assert normalized.title == "Android Developer"
    assert normalized.url == "https://example.com/jobs/123"