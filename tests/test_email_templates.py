from src.email.models import EmailStatus
from src.email.templates import create_outreach_email
from src.research.models import Company, Contact, Job


def test_create_outreach_email():
    company = Company(name="Test Startup")

    job = Job(
        title="Android Developer",
        company="Test Startup",
        url="https://example.com/android",
    )

    contact = Contact(
        name="Test Recruiter",
        email="recruiter@example.com",
        role="Recruiter",
        company="Test Startup",
        source="public_page",
    )

    email = create_outreach_email(job, company, contact)

    assert email.recipient == "recruiter@example.com"
    assert email.subject == "Application for Android Developer at Test Startup"
    assert "Test Recruiter" in email.body
    assert "Android Developer" in email.body
    assert "Test Startup" in email.body
    assert email.job_url == "https://example.com/android"
    assert email.contact_name == "Test Recruiter"
    assert email.status == EmailStatus.draft


def test_create_outreach_email_uses_hiring_team_when_name_missing():
    company = Company(name="Test Startup")

    job = Job(
        title="Backend Developer",
        company="Test Startup",
        url="https://example.com/backend",
    )

    contact = Contact(
        email="jobs@example.com",
        company="Test Startup",
        source="public_page",
    )

    email = create_outreach_email(job, company, contact)

    assert "Hiring Team" in email.body
    assert email.contact_name is None