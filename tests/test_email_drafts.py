from src.email.drafts import load_drafts, save_drafts
from src.email.models import EmailStatus, OutreachEmail


def test_email_draft_persistence(tmp_path):
    path = tmp_path / "email_drafts.json"

    drafts = [
        OutreachEmail(
            recipient="recruiter@example.com",
            subject="Android Developer",
            body="Hello Recruiter",
            company="Test Startup",
            job_url="https://example.com/job",
            contact_name="Test Recruiter",
            status=EmailStatus.draft,
        )
    ]

    save_drafts(drafts, path)

    loaded = load_drafts(path)

    assert len(loaded) == 1
    assert loaded[0] == drafts[0]


def test_missing_draft_file_returns_empty_list(tmp_path):
    path = tmp_path / "missing.json"

    assert load_drafts(path) == []


def test_email_status_survives_persistence(tmp_path):
    path = tmp_path / "email_drafts.json"

    drafts = [
        OutreachEmail(
            recipient="recruiter@example.com",
            subject="Android Developer",
            body="Hello",
            company="Test Startup",
            status=EmailStatus.approved,
        )
    ]

    save_drafts(drafts, path)

    loaded = load_drafts(path)

    assert loaded[0].status == EmailStatus.approved
    