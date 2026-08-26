import pytest

from src.email.approval import approve_email, reject_email
from src.email.models import EmailStatus, OutreachEmail


def make_email() -> OutreachEmail:
    return OutreachEmail(
        recipient="recruiter@example.com",
        subject="Android Developer",
        body="Hello Recruiter",
        company="Test Startup",
    )


def test_approve_draft():
    email = make_email()

    result = approve_email(email)

    assert result.status == EmailStatus.approved


def test_reject_draft():
    email = make_email()

    result = reject_email(email)

    assert result.status == EmailStatus.rejected


def test_cannot_approve_already_approved_email():
    email = make_email()
    email.status = EmailStatus.approved

    with pytest.raises(ValueError):
        approve_email(email)


def test_cannot_reject_already_sent_email():
    email = make_email()
    email.status = EmailStatus.sent

    with pytest.raises(ValueError):
        reject_email(email)