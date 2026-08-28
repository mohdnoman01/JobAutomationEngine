from pathlib import Path

from src.orchestrator import run_pipeline
from src.research.models import Company, Contact, Job, ResearchResult


def test_pipeline_creates_application_and_email_draft(
    tmp_path,
    monkeypatch,
):
    companies_file = tmp_path / "companies.csv"
    companies_file.write_text(
        "name,website,careers_url\n"
        "Test Startup,https://example.com,"
        "https://example.com/careers\n",
        encoding="utf-8",
    )

    company = Company(
        name="Test Startup",
        website="https://example.com",
        careers_url="https://example.com/careers",
    )

    contact = Contact(
        name="Test Recruiter",
        email="recruiter@example.com",
        role="Recruiter",
        company="Test Startup",
        source="public_page",
    )

    job = Job(
        title="Android Developer",
        company="Test Startup",
        url="https://example.com/jobs/android",
    )

    monkeypatch.setattr(
        "src.orchestrator.research_company",
        lambda _: ResearchResult(
            company_name="Test Startup",
            url="https://example.com",
            text="Test company",
            contacts=[contact],
        ),
    )

    monkeypatch.setattr(
        "src.orchestrator.discover_jobs",
        lambda _: [job],
    )

    applications_path = tmp_path / "applications.json"
    drafts_path = tmp_path / "email_drafts.json"

    result = run_pipeline(
        companies_file,
        applications_path=applications_path,
        drafts_path=drafts_path,
    )

    assert result == {
        "companies_processed": 1,
        "jobs_discovered": 1,
        "applications_created": 1,
        "drafts_created": 1,
    }

    assert applications_path.exists()
    assert drafts_path.exists()


def test_pipeline_does_not_duplicate_existing_records(
    tmp_path,
    monkeypatch,
):
    companies_file = tmp_path / "companies.csv"
    companies_file.write_text(
        "name,website,careers_url\n"
        "Test Startup,https://example.com,"
        "https://example.com/careers\n",
        encoding="utf-8",
    )

    company = Company(
        name="Test Startup",
        website="https://example.com",
        careers_url="https://example.com/careers",
    )

    contact = Contact(
        name="Test Recruiter",
        email="recruiter@example.com",
        company="Test Startup",
        source="public_page",
    )

    job = Job(
        title="Android Developer",
        company="Test Startup",
        url="https://example.com/jobs/android",
    )

    monkeypatch.setattr(
        "src.orchestrator.research_company",
        lambda _: ResearchResult(
            company_name="Test Startup",
            url="https://example.com",
            text="Test company",
            contacts=[contact],
        ),
    )

    monkeypatch.setattr(
        "src.orchestrator.discover_jobs",
        lambda _: [job],
    )

    applications_path = tmp_path / "applications.json"
    drafts_path = tmp_path / "email_drafts.json"

    first = run_pipeline(
        companies_file,
        applications_path=applications_path,
        drafts_path=drafts_path,
    )

    second = run_pipeline(
        companies_file,
        applications_path=applications_path,
        drafts_path=drafts_path,
    )

    assert first["applications_created"] == 1
    assert first["drafts_created"] == 1

    assert second["applications_created"] == 0
    assert second["drafts_created"] == 0