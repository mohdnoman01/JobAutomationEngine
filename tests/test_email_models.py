from src.email.models import EmailStatus, OutreachEmail


def test_outreach_email_defaults_to_draft():
    email = OutreachEmail(
        recipient="recruiter@example.com",
        subject="Android Developer opportunity",
        body="Hello, I am interested in the Android Developer role.",
        company="Test Startup",
    )

    assert email.status == EmailStatus.draft
    assert email.company == "Test Startup"
    assert email.job_url is None


def test_outreach_email_supports_job_and_contact():
    email = OutreachEmail(
        recipient="recruiter@example.com",
        subject="Android Developer",
        body="Hello Recruiter",
        company="Test Startup",
        job_url="https://example.com/job",
        contact_name="Test Recruiter",
        status=EmailStatus.approved,
    )

    assert email.status == EmailStatus.approved
    assert email.job_url == "https://example.com/job"
    assert email.contact_name == "Test Recruiter"