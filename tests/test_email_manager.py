from src.email.manager import EmailDraftManager
from src.email.models import EmailStatus, OutreachEmail
from src.email.sender import MockEmailSender


def make_email() -> OutreachEmail:
    return OutreachEmail(
        recipient="recruiter@example.com",
        subject="Android Developer",
        body="Hello Recruiter",
        company="Test Startup",
        job_url="https://example.com/android",
        contact_name="Test Recruiter",
    )


def test_manager_adds_and_persists_draft(tmp_path):
    path = tmp_path / "email_drafts.json"

    manager = EmailDraftManager(path)

    email = manager.add(make_email())

    assert email.status == EmailStatus.draft

    loaded = manager.list()

    assert len(loaded) == 1
    assert loaded[0] == email


def test_manager_persists_approval(tmp_path):
    path = tmp_path / "email_drafts.json"

    manager = EmailDraftManager(path)
    manager.add(make_email())

    approved = manager.approve("recruiter@example.com")

    assert approved.status == EmailStatus.approved

    fresh_manager = EmailDraftManager(path)

    loaded = fresh_manager.list()

    assert loaded[0].status == EmailStatus.approved


def test_manager_persists_rejection(tmp_path):
    path = tmp_path / "email_drafts.json"

    manager = EmailDraftManager(path)
    manager.add(make_email())

    rejected = manager.reject("recruiter@example.com")

    assert rejected.status == EmailStatus.rejected

    fresh_manager = EmailDraftManager(path)

    loaded = fresh_manager.list()

    assert loaded[0].status == EmailStatus.rejected


def test_manager_sends_and_persists_status(tmp_path):
    path = tmp_path / "email_drafts.json"

    manager = EmailDraftManager(
        path,
        sender=MockEmailSender(),
    )

    manager.add(make_email())
    manager.approve("recruiter@example.com")

    sent = manager.send("recruiter@example.com")

    assert sent.status == EmailStatus.sent

    fresh_manager = EmailDraftManager(
        path,
        sender=MockEmailSender(),
    )

    loaded = fresh_manager.list()

    assert loaded[0].status == EmailStatus.sent