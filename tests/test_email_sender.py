import pytest

from src.email.models import EmailStatus, OutreachEmail
from src.email.sender import MockEmailSender


def make_email(status: EmailStatus) -> OutreachEmail:
    return OutreachEmail(
        recipient="recruiter@example.com",
        subject="Android Developer",
        body="Hello Recruiter",
        company="Test Startup",
        status=status,
    )


def test_mock_sender_sends_approved_email():
    email = make_email(EmailStatus.approved)

    result = MockEmailSender().send(email)

    assert result.status == EmailStatus.sent


def test_mock_sender_rejects_draft_email():
    email = make_email(EmailStatus.draft)

    with pytest.raises(ValueError):
        MockEmailSender().send(email)


def test_mock_sender_rejects_rejected_email():
    email = make_email(EmailStatus.rejected)

    with pytest.raises(ValueError):
        MockEmailSender().send(email)


def test_mock_sender_rejects_already_sent_email():
    email = make_email(EmailStatus.sent)

    with pytest.raises(ValueError):
        MockEmailSender().send(email)
