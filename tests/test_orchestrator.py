from src.applications.tracker import load_contacts
from src.email.drafts import load_drafts
from src.orchestrator import run_pipeline
from src.research.models import Company, Contact, Job, ResearchResult, UserProfile


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
        lambda _, **__: ResearchResult(
            company_name="Test Startup",
            url="https://example.com",
            text="Test company",
            contacts=[contact],
        ),
    )

    monkeypatch.setattr(
        "src.orchestrator.discover_jobs",
        lambda _, **__: [job],
    )

    applications_path = tmp_path / "applications.json"
    contacts_path = tmp_path / "contacts.json"
    drafts_path = tmp_path / "email_drafts.json"

    result = run_pipeline(
        companies_file,
        applications_path=applications_path,
        contacts_path=contacts_path,
        drafts_path=drafts_path,
    )

    assert result == {
        "companies_processed": 1,
        "jobs_discovered": 1,
        "applications_created": 1,
        "drafts_created": 1,
    }

    assert applications_path.exists()
    assert contacts_path.exists()
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
        lambda _, **__: ResearchResult(
            company_name="Test Startup",
            url="https://example.com",
            text="Test company",
            contacts=[contact],
        ),
    )

    monkeypatch.setattr(
        "src.orchestrator.discover_jobs",
        lambda _, **__: [job],
    )

    applications_path = tmp_path / "applications.json"
    contacts_path = tmp_path / "contacts.json"
    drafts_path = tmp_path / "email_drafts.json"

    first = run_pipeline(
        companies_file,
        applications_path=applications_path,
        contacts_path=contacts_path,
        drafts_path=drafts_path,
    )

    second = run_pipeline(
        companies_file,
        applications_path=applications_path,
        contacts_path=contacts_path,
        drafts_path=drafts_path,
    )

    assert first["applications_created"] == 1
    assert first["drafts_created"] == 1

    assert second["applications_created"] == 0
    assert second["drafts_created"] == 0


def test_pipeline_creates_one_draft_for_the_best_contact_and_job(
    tmp_path,
    monkeypatch,
):
    companies_file = tmp_path / "companies.csv"
    companies_file.write_text(
        "name,website,careers_url\n"
        "Test Startup,https://example.com,https://example.com/careers\n",
        encoding="utf-8",
    )
    company = Company(name="Test Startup", website="https://example.com")
    contacts = [
        Contact(email="press@example.com", company=company.name),
        Contact(email="jane.doe@example.com", company=company.name),
        Contact(
            email="hiring@example.com",
            role="Hiring Manager",
            company=company.name,
        ),
        Contact(
            email="recruiting@example.com",
            role="Recruiting",
            company=company.name,
        ),
    ]
    jobs = [
        Job(
            title="Backend Engineer",
            company=company.name,
            url="https://example.com/jobs/backend",
        ),
        Job(
            title="Android Engineer",
            company=company.name,
            url="https://example.com/jobs/android",
        ),
        Job(
            title="Kotlin Engineer",
            company=company.name,
            url="https://example.com/jobs/kotlin",
        ),
    ]
    monkeypatch.setattr(
        "src.orchestrator.research_company",
        lambda _, **__: ResearchResult(
            company_name=company.name,
            url=company.website or "",
            text="Test company",
            contacts=contacts,
        ),
    )
    monkeypatch.setattr("src.orchestrator.discover_jobs", lambda _, **__: jobs)

    applications_path = tmp_path / "applications.json"
    contacts_path = tmp_path / "contacts.json"
    drafts_path = tmp_path / "email_drafts.json"

    first = run_pipeline(
        companies_file,
        applications_path=applications_path,
        contacts_path=contacts_path,
        drafts_path=drafts_path,
        profile=UserProfile(
            target_roles=["Android Engineer"],
            preferred_skills=["Kotlin"],
        ),
    )
    second = run_pipeline(
        companies_file,
        applications_path=applications_path,
        contacts_path=contacts_path,
        drafts_path=drafts_path,
        profile=UserProfile(
            target_roles=["Android Engineer"],
            preferred_skills=["Kotlin"],
     ),
    )

    drafts = load_drafts(drafts_path)
    assert first["applications_created"] == 3
    assert first["drafts_created"] == 1
    assert second["applications_created"] == 0
    assert second["drafts_created"] == 0
    assert len(drafts) == 1
    assert drafts[0].recipient == "recruiting@example.com"
    assert drafts[0].job_url == "https://example.com/jobs/android"
    assert {contact.email for contact in load_contacts(contacts_path)} == {
        contact.email for contact in contacts
    }
    persisted_contacts = load_contacts(contacts_path)
    assert {contact.qualification.value for contact in persisted_contacts} == {
        "eligible",
        "uncertain",
        "rejected",
    }
    assert all(contact.qualification_reason for contact in persisted_contacts)


def test_pipeline_creates_no_draft_without_an_eligible_contact(tmp_path, monkeypatch):
    companies_file = tmp_path / "companies.csv"
    companies_file.write_text(
        "name,website,careers_url\n"
        "Test Startup,https://example.com,https://example.com/careers\n",
        encoding="utf-8",
    )
    company = Company(name="Test Startup", website="https://example.com")
    contacts = [
        Contact(email="jane.doe@example.com", company=company.name),
        Contact(email="sales@example.com", company=company.name),
    ]
    job = Job(
        title="Android Engineer",
        company=company.name,
        url="https://example.com/jobs/android",
    )
    monkeypatch.setattr(
        "src.orchestrator.research_company",
        lambda _, **__: ResearchResult(
            company_name=company.name,
            url=company.website or "",
            text="Test company",
            contacts=contacts,
        ),
    )
    monkeypatch.setattr("src.orchestrator.discover_jobs", lambda _, **__: [job])

    contacts_path = tmp_path / "contacts.json"
    drafts_path = tmp_path / "email_drafts.json"

    result = run_pipeline(
        companies_file,
        applications_path=tmp_path / "applications.json",
        contacts_path=contacts_path,
        drafts_path=drafts_path,
    )

    assert result["drafts_created"] == 0
    assert not drafts_path.exists()
    assert {contact.email for contact in load_contacts(contacts_path)} == {
        contact.email for contact in contacts
    }

    assert result["drafts_created"] == 0
    assert not drafts_path.exists()
    assert {contact.email for contact in load_contacts(contacts_path)} == {
        contact.email for contact in contacts
    }
